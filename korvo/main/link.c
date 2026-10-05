/*
 * WebSocket link to the server (docs/PROTOCOL.md): one socket, hello first,
 * reconnect with a 1 / 2 / 5 / 10 s backoff, application pings for the
 * clock and to detect a dead link, a send queue so the audio and engine
 * tasks never block on the network.
 */
#include "link.h"

#include <string.h>

#include "config.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_websocket_client.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "mem.h"
#include "proto.h"

static const char *TAG = "link";

typedef struct {
    uint8_t op;             /* 1 text, 2 binary */
    size_t len;
    uint8_t data[];
} txmsg_t;

static esp_websocket_client_handle_t ws;
static QueueHandle_t txq;
static volatile int sock_up, online, wifi_up, started;
static volatile int64_t last_rx_us, online_since_us;
static int backoff_i;
static const int backoff_ms[] = {1000, 2000, 5000, 10000};
static uint8_t *rx_buf;
static size_t rx_len, rx_cap;
static int rx_op;
static volatile uint32_t tx_dropped;

static void *alloc_big(size_t n) { return mem_alloc_big(n); }

static void flush_queue(void)
{
    txmsg_t *m;
    while (txq && xQueueReceive(txq, &m, 0) == pdTRUE)
        heap_caps_free(m);
}

static int enqueue(uint8_t op, const void *a, size_t alen, const void *b, size_t blen, int front)
{
    if (!sock_up || !txq)
        return -1;
    txmsg_t *m = alloc_big(sizeof *m + alen + blen);
    if (!m)
        return -1;
    m->op = op;
    m->len = alen + blen;
    memcpy(m->data, a, alen);
    if (blen)
        memcpy(m->data + alen, b, blen);
    BaseType_t ok = front ? xQueueSendToFront(txq, &m, 0) : xQueueSend(txq, &m, 0);
    if (ok != pdTRUE) {
        heap_caps_free(m);
        tx_dropped++;
        return -1;
    }
    return 0;
}

int link_send_text(const char *json)
{
    /* before the welcome only the hello and pings go out */
    return enqueue(1, json, strlen(json), NULL, 0, 0);
}

int link_send_audio(const int16_t *pcm, int samples)
{
    if (!online)
        return -1;
    uint8_t h = 0x01;
    return enqueue(2, &h, 1, pcm, (size_t)samples * 2, 0);
}

static void tx_task(void *arg)
{
    txmsg_t *m;
    for (;;) {
        if (xQueueReceive(txq, &m, portMAX_DELAY) != pdTRUE)
            continue;
        if (sock_up && esp_websocket_client_is_connected(ws)) {
            int r = m->op == 1 ? esp_websocket_client_send_text(ws, (const char *)m->data, (int)m->len, pdMS_TO_TICKS(2000))
                               : esp_websocket_client_send_bin(ws, (const char *)m->data, (int)m->len, pdMS_TO_TICKS(2000));
            if (r < 0)
                ESP_LOGW(TAG, "send failed (%d bytes)", (int)m->len);
        }
        heap_caps_free(m);
    }
}

static void dispatch(void)
{
    if (rx_op == 1) {
        char err[160] = "";
        rx_buf[rx_len] = 0;
        int r = proto_on_text((const char *)rx_buf, rx_len, err, sizeof err);
        if (r == PROTO_WELCOME) {
            online = 1;
            online_since_us = esp_timer_get_time();
            backoff_i = 0;
            esp_websocket_client_set_reconnect_timeout(ws, backoff_ms[0]);
        } else if (r == PROTO_PENDING) {
            ESP_LOGW(TAG, "waiting for approval: open the server's Devices page and approve %s", g_cfg.device_id);
        } else if (r == PROTO_ERROR) {
            ESP_LOGE(TAG, "server refused the device: %s", err);
            backoff_i = 3;
        }
    } else if (rx_op == 2) {
        proto_on_binary(rx_buf, rx_len);
    }
    /* do not keep a template-sized buffer forever */
    if (rx_cap > 64 * 1024) {
        heap_caps_free(rx_buf);
        rx_buf = NULL;
        rx_cap = 0;
    }
}

static void on_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    esp_websocket_event_data_t *d = data;
    switch (id) {
    case WEBSOCKET_EVENT_CONNECTED: {
        ESP_LOGI(TAG, "connected to %s", g_cfg.server_url);
        flush_queue();
        sock_up = 1;
        online = 0;
        last_rx_us = esp_timer_get_time();
        char *hello = malloc(2048);
        if (hello && proto_hello(hello, 2048) > 0)
            enqueue(1, hello, strlen(hello), NULL, 0, 1);
        free(hello);
        break;
    }
    case WEBSOCKET_EVENT_DATA: {
        last_rx_us = esp_timer_get_time();
        int op = d->op_code;
        if (op != 0 && op != 1 && op != 2)
            break;                      /* ping / pong / close: handled by the client */
        if ((op == 1 || op == 2) && d->payload_offset == 0) {
            rx_op = op;
            rx_len = 0;
        }
        size_t need = rx_len + (size_t)d->data_len + 1;
        if (op != 0 && d->payload_offset == 0 && (size_t)d->payload_len + 1 > need)
            need = (size_t)d->payload_len + 1;
        if (need > rx_cap) {
            uint8_t *nb = mem_realloc_big(rx_buf, need);
            if (!nb) {
                ESP_LOGE(TAG, "no memory for a %d byte message", d->payload_len);
                rx_len = 0;
                rx_op = 0;
                break;
            }
            rx_buf = nb;
            rx_cap = need;
        }
        memcpy(rx_buf + rx_len, d->data_ptr, (size_t)d->data_len);
        rx_len += (size_t)d->data_len;
        if (d->payload_offset + d->data_len >= d->payload_len && d->fin && rx_op) {
            dispatch();
            rx_len = 0;
            rx_op = 0;
        }
        break;
    }
    case WEBSOCKET_EVENT_DISCONNECTED:
    case WEBSOCKET_EVENT_CLOSED:
    case WEBSOCKET_EVENT_ERROR:
        if (sock_up || online)
            ESP_LOGW(TAG, "disconnected (event %d)", (int)id);
        sock_up = 0;
        online = 0;
        flush_queue();
        proto_on_disconnect();
        rx_len = 0;
        rx_op = 0;
        if (id != WEBSOCKET_EVENT_ERROR) {     /* one step per failed attempt */
            esp_websocket_client_set_reconnect_timeout(ws, backoff_ms[backoff_i]);
            if (backoff_i < 3)
                backoff_i++;
        }
        break;
    default:
        break;
    }
}

static void link_task(void *arg)
{
    int64_t last_ping = 0;
    int pings = 0;
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(1000));
        if (!started) {
            if (!wifi_up)
                continue;
            esp_websocket_client_config_t c = {
                .uri = g_cfg.server_url,
                .buffer_size = 8192,
                .task_stack = 12 * 1024,
                .task_prio = 6,
                .task_core_id_set = true,
                .task_core_id = 0,
                .reconnect_timeout_ms = backoff_ms[0],
                .network_timeout_ms = 8000,
                .ping_interval_sec = 10,
                .pingpong_timeout_sec = 40,
                .enable_close_reconnect = true,     /* the server closes on a refused token: retry */
                .keep_alive_enable = true,
            };
            ws = esp_websocket_client_init(&c);
            if (!ws) {
                ESP_LOGE(TAG, "bad server url %s", g_cfg.server_url);
                vTaskDelay(pdMS_TO_TICKS(10000));
                continue;
            }
            esp_websocket_register_events(ws, WEBSOCKET_EVENT_ANY, on_event, NULL);
            esp_websocket_client_start(ws);
            started = 1;
            ESP_LOGI(TAG, "connecting to %s as %s", g_cfg.server_url, g_cfg.device_id);
            continue;
        }
        int64_t now = esp_timer_get_time();
        if (online) {
            /* clock sync and liveness: quick pings first, then every 20 s */
            if (now - last_ping > (pings < 5 ? 2000000LL : 20000000LL)) {
                proto_send_ping();
                last_ping = now;
                pings++;
            }
        } else {
            pings = 0;
        }
        /* nothing at all for 75 s (the server pings every 20 s): dead link */
        if (sock_up && now - last_rx_us > 75000000LL) {
            ESP_LOGW(TAG, "no traffic for 75 s: reconnecting");
            last_rx_us = now;
            esp_websocket_client_stop(ws);
            sock_up = online = 0;
            proto_on_disconnect();
            vTaskDelay(pdMS_TO_TICKS(backoff_ms[backoff_i]));
            esp_websocket_client_start(ws);
        }
    }
}

void link_start(void)
{
    txq = xQueueCreate(128, sizeof(txmsg_t *));
    /* neither writes flash: stacks in PSRAM */
    task_create_psram(tx_task, "ws_tx", 4096, NULL, 6, NULL, 0);
    task_create_psram(link_task, "link", 4096, NULL, 4, NULL, 0);
}

void link_set_wifi(int up) { wifi_up = up; }
int link_online(void) { return online; }
int link_socket_up(void) { return sock_up; }
uint32_t link_tx_dropped(void) { return tx_dropped; }
int64_t link_online_since_us(void) { return online ? online_since_us : 0; }
