/*
 * Wi-Fi setup portal: open access point + DNS that answers every name with
 * the device (so phones show the "sign in to network" page) + a one-page
 * HTML form. No JavaScript.
 */
#include "portal.h"

#include <ctype.h>
#include <stdio.h>
#include <string.h>

#include "config.h"
#include "esp_http_server.h"
#include "esp_log.h"
#include "mem.h"
#include "esp_mac.h"
#include "esp_system.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_timer.h"
#include "leds.h"
#include "logbuf.h"
#include "sat_common.h"
#include "lwip/sockets.h"

static const char *TAG = "portal";
static volatile int active, by_user, dns_run;
static int64_t opened_us;
static TaskHandle_t dns_handle;
static httpd_handle_t server;
static char ap_name[32];
static char scan[20][33];
static int nscan;

int portal_active(void) { return active; }
int portal_auto_closable(void) { return !by_user || esp_timer_get_time() - opened_us > 300000000LL; }

int portal_clients(void)
{
    wifi_sta_list_t l;
    return active && esp_wifi_ap_get_sta_list(&l) == ESP_OK ? l.num : 0;
}

static void html_esc(char *out, size_t n, const char *s)
{
    size_t o = 0;
    for (; *s && o + 7 < n; s++) {
        const char *r = NULL;
        switch (*s) {
        case '&': r = "&amp;"; break;
        case '<': r = "&lt;"; break;
        case '>': r = "&gt;"; break;
        case '"': r = "&quot;"; break;
        case '\'': r = "&#39;"; break;
        }
        if (r) {
            size_t l = strlen(r);
            memcpy(out + o, r, l);
            o += l;
        } else {
            out[o++] = *s;
        }
    }
    out[o] = 0;
}

static void do_scan(void)
{
    wifi_scan_config_t sc = {.show_hidden = false};
    nscan = 0;
    if (esp_wifi_scan_start(&sc, true) != ESP_OK)
        return;
    uint16_t n = 30;
    wifi_ap_record_t *recs = calloc(n, sizeof *recs);
    if (!recs)
        return;
    if (esp_wifi_scan_get_ap_records(&n, recs) == ESP_OK)
        for (int i = 0; i < n && nscan < 20; i++) {   /* sorted by signal */
            const char *s = (const char *)recs[i].ssid;
            int dup = !s[0];
            for (int j = 0; j < nscan && !dup; j++)
                dup = !strcmp(scan[j], s);
            if (!dup)
                snprintf(scan[nscan++], sizeof scan[0], "%s", s);
        }
    free(recs);
}

static esp_err_t page(httpd_req_t *req)
{
    char *buf = malloc(6144);
    if (!buf)
        return httpd_resp_send_500(req);
    char e1[200], e2[200], e3[140], e4[140], e5[100];
    html_esc(e1, sizeof e1, g_cfg.wifi_ssid);
    html_esc(e2, sizeof e2, g_cfg.server_url);
    html_esc(e3, sizeof e3, g_cfg.name);
    html_esc(e4, sizeof e4, g_cfg.room);
    html_esc(e5, sizeof e5, g_cfg.device_id);
    int o = snprintf(buf, 6144,
        "<!DOCTYPE html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width'>"
        "<title>Korvo setup</title><style>body{font-family:sans-serif;max-width:28em;margin:1em auto;padding:0 1em}"
        "label{display:block;margin-top:.8em}input,select{width:100%%;padding:.4em;box-sizing:border-box}"
        "button{margin-top:1.2em;padding:.6em 1.5em}small{color:#666}</style></head><body>"
        "<h2>Korvo satellite</h2><small>%s</small><form method=post action=/save>"
        "<label>Wi-Fi network<select name=ssid_pick><option value=''>(type it below)</option>", e5);
    for (int i = 0; i < nscan && o < 5000; i++) {
        char es[110];
        html_esc(es, sizeof es, scan[i]);
        o += snprintf(buf + o, 6144 - o, "<option%s>%s</option>", strcmp(scan[i], g_cfg.wifi_ssid) ? "" : " selected",
                      es);
    }
    snprintf(buf + o, 6144 - o,
        "</select></label><label>or network name<input name=ssid value=\"%s\"></label>"
        "<label>Password<input name=pass type=password placeholder='(unchanged if empty)'></label>"
        "<label>Server URL<input name=url value=\"%s\"></label>"
        "<label>Name<input name=name value=\"%s\"></label>"
        "<label>Room<input name=room value=\"%s\"></label>"
        "<button type=submit>Save and restart</button></form>"
        "<h3>Status</h3><p>Firmware %s, boot #%lu, last reset: %s. <a href=/log>Full log</a></p><pre style="
        "'font-size:11px;white-space:pre-wrap;background:#f4f4f4;padding:.5em'>",
        e1, e2, e3, e4, SAT_VERSION, (unsigned long)logbuf_boot_count(), logbuf_reset_reason());
    httpd_resp_set_type(req, "text/html");
    httpd_resp_send_chunk(req, buf, HTTPD_RESP_USE_STRLEN);
    /* the last lines of the log (HTML-escaped) */
    size_t n = logbuf_text(buf, 3000, 1);
    const char *p = n > 2500 ? buf + n - 2500 : buf;
    char esc[1100];
    while (*p) {
        char chunk[200];
        size_t k = strnlen(p, sizeof chunk - 1);
        memcpy(chunk, p, k);
        chunk[k] = 0;
        html_esc(esc, sizeof esc, chunk);
        httpd_resp_send_chunk(req, esc, HTTPD_RESP_USE_STRLEN);
        p += k;
    }
    httpd_resp_send_chunk(req, "</pre></body></html>", HTTPD_RESP_USE_STRLEN);
    free(buf);
    return httpd_resp_send_chunk(req, NULL, 0);
}

static esp_err_t logpage(httpd_req_t *req)
{
    char *buf = malloc(24 * 1024);
    if (!buf)
        return httpd_resp_send_500(req);
    char head[160];
    snprintf(head, sizeof head, "Korvo %s, boot #%lu, last reset: %s\n\n", SAT_VERSION,
             (unsigned long)logbuf_boot_count(), logbuf_reset_reason());
    size_t n = logbuf_text(buf, 24 * 1024, 1);
    httpd_resp_set_type(req, "text/plain; charset=utf-8");
    httpd_resp_send_chunk(req, head, HTTPD_RESP_USE_STRLEN);
    httpd_resp_send_chunk(req, buf, (ssize_t)n);
    free(buf);
    return httpd_resp_send_chunk(req, NULL, 0);
}

/* captive portal detection URLs and anything else: send to the form */
static esp_err_t redirect(httpd_req_t *req)
{
    if (!strcmp(req->uri, "/"))
        return page(req);
    if (!strcmp(req->uri, "/log"))
        return logpage(req);
    httpd_resp_set_status(req, "302 Found");
    httpd_resp_set_hdr(req, "Location", "http://192.168.4.1/");
    return httpd_resp_send(req, NULL, 0);
}

static void url_decode(char *s)
{
    char *o = s;
    for (; *s; s++) {
        if (*s == '+')
            *o++ = ' ';
        else if (*s == '%' && isxdigit((unsigned char)s[1]) && isxdigit((unsigned char)s[2])) {
            char h[3] = {s[1], s[2], 0};
            *o++ = (char)strtol(h, NULL, 16);
            s += 2;
        } else
            *o++ = *s;
    }
    *o = 0;
}

static int field(char *body, const char *key, char *out, size_t n)
{
    char tmp[200];
    if (httpd_query_key_value(body, key, tmp, sizeof tmp) != ESP_OK)
        return 0;
    url_decode(tmp);
    size_t l = strnlen(tmp, n - 1);
    memcpy(out, tmp, l);
    out[l] = 0;
    return 1;
}

static void restart_task(void *arg)
{
    vTaskDelay(pdMS_TO_TICKS(1500));
    esp_restart();
}

static esp_err_t save(httpd_req_t *req)
{
    char body[1024];
    int n = req->content_len < (int)sizeof body - 1 ? req->content_len : (int)sizeof body - 1, got = 0;
    while (got < n) {
        int r = httpd_req_recv(req, body + got, n - got);
        if (r <= 0)
            return httpd_resp_send_500(req);
        got += r;
    }
    body[got] = 0;
    char pick[33] = "", ssid[33] = "", pass[65] = "", url[160] = "", name[64] = "", room[64] = "";
    field(body, "ssid_pick", pick, sizeof pick);
    field(body, "ssid", ssid, sizeof ssid);
    field(body, "pass", pass, sizeof pass);
    field(body, "url", url, sizeof url);
    field(body, "name", name, sizeof name);
    field(body, "room", room, sizeof room);
    const char *net = pick[0] ? pick : ssid;
    if (net[0] && strcmp(net, g_cfg.wifi_ssid)) {
        snprintf(g_cfg.wifi_ssid, sizeof g_cfg.wifi_ssid, "%s", net);
        if (!pass[0])
            g_cfg.wifi_pass[0] = 0;     /* another network: the old password is no use */
    }
    if (pass[0])
        snprintf(g_cfg.wifi_pass, sizeof g_cfg.wifi_pass, "%s", pass);
    if (!strncmp(url, "ws://", 5) || !strncmp(url, "wss://", 6))
        snprintf(g_cfg.server_url, sizeof g_cfg.server_url, "%s", url);
    if (name[0])
        snprintf(g_cfg.name, sizeof g_cfg.name, "%s", name);
    snprintf(g_cfg.room, sizeof g_cfg.room, "%s", room);
    esp_err_t err = config_save_user();
    ESP_LOGI(TAG, "saved: wifi \"%s\", server %s (%s)", g_cfg.wifi_ssid, g_cfg.server_url, esp_err_to_name(err));
    httpd_resp_set_type(req, "text/html");
    httpd_resp_sendstr(req, err == ESP_OK
                                ? "<!DOCTYPE html><html><body style='font-family:sans-serif'><h2>Saved.</h2>"
                                  "<p>The Korvo restarts and joins the network. Approve it on the server's "
                                  "Devices page if it is new.</p></body></html>"
                                : "<html><body><h2>Could not save the settings.</h2></body></html>");
    if (err == ESP_OK)
        xTaskCreate(restart_task, "restart", 2048, NULL, 5, NULL);
    return ESP_OK;
}

/* every DNS question gets 192.168.4.1 */
static void dns_task(void *arg)
{
    int s = socket(AF_INET, SOCK_DGRAM, 0);
    struct sockaddr_in a = {.sin_family = AF_INET, .sin_port = htons(53), .sin_addr.s_addr = htonl(INADDR_ANY)};
    if (s < 0 || bind(s, (struct sockaddr *)&a, sizeof a) < 0) {
        ESP_LOGE(TAG, "dns: cannot bind");
        if (s >= 0)
            close(s);
        dns_handle = NULL;
        vTaskDelete(NULL);
    }
    struct timeval tv = {.tv_sec = 1};
    setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
    uint8_t q[512];
    while (dns_run) {
        struct sockaddr_in from;
        socklen_t fl = sizeof from;
        int n = recvfrom(s, q, sizeof q - 16, 0, (struct sockaddr *)&from, &fl);
        if (n < 12 || (q[2] & 0x80))
            continue;
        /* answer the first question: copy it, append one A record */
        int p = 12;
        while (p < n && q[p])
            p += q[p] + 1;
        p += 5;                         /* zero label + type + class */
        if (p > n)
            continue;
        q[2] = 0x81;                    /* response, recursion desired */
        q[3] = 0x80;                    /* recursion available, no error */
        q[6] = 0, q[7] = 1;             /* one answer */
        q[8] = q[9] = q[10] = q[11] = 0;
        static const uint8_t ans[] = {0xC0, 0x0C, 0, 1, 0, 1, 0, 0, 0, 60, 0, 4, 192, 168, 4, 1};
        memcpy(q + p, ans, sizeof ans);
        sendto(s, q, (size_t)p + sizeof ans, 0, (struct sockaddr *)&from, fl);
    }
    close(s);
    dns_handle = NULL;
    vTaskDelete(NULL);
}

void portal_stop(void)
{
    if (!active)
        return;
    if (server)
        httpd_stop(server);
    server = NULL;
    dns_run = 0;
    esp_wifi_set_mode(WIFI_MODE_STA);
    active = 0;
    leds_set_setup_mode(0);
    ESP_LOGI(TAG, "setup access point closed (connected to the network)");
}

void portal_start(int user)
{
    if (active) {
        by_user |= user;
        return;
    }
    active = 1;
    by_user = user;
    opened_us = esp_timer_get_time();
    leds_set_setup_mode(1);
    if (user)
        esp_wifi_disconnect();
    esp_wifi_set_mode(WIFI_MODE_APSTA);
    do_scan();
    uint8_t mac[6];
    esp_read_mac(mac, ESP_MAC_WIFI_SOFTAP);
    snprintf(ap_name, sizeof ap_name, "Korvo-Setup-%02X%02X", mac[4], mac[5]);
    wifi_config_t ap = {0};
    snprintf((char *)ap.ap.ssid, sizeof ap.ap.ssid, "%s", ap_name);
    ap.ap.ssid_len = (uint8_t)strlen(ap_name);
    ap.ap.channel = 1;
    ap.ap.max_connection = 4;
    ap.ap.authmode = WIFI_AUTH_OPEN;
    esp_wifi_set_config(WIFI_IF_AP, &ap);
    httpd_config_t hc = HTTPD_DEFAULT_CONFIG();
    hc.uri_match_fn = httpd_uri_match_wildcard;
    hc.lru_purge_enable = true;
    hc.stack_size = 6144;
    if (httpd_start(&server, &hc) == ESP_OK) {
        httpd_uri_t s = {.uri = "/save", .method = HTTP_POST, .handler = save};
        httpd_uri_t any = {.uri = "/*", .method = HTTP_GET, .handler = redirect};
        httpd_register_uri_handler(server, &s);
        httpd_register_uri_handler(server, &any);
    }
    dns_run = 1;
    if (!dns_handle)
        task_create_psram(dns_task, "dns", 3072, NULL, 4, &dns_handle, 0);
    ESP_LOGW(TAG, "setup: join the open Wi-Fi \"%s\" and open http://192.168.4.1/ (%d networks found)", ap_name,
             nscan);
}
