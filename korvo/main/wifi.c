#include "wifi.h"

#include <string.h>

#include "config.h"
#include "esp_event.h"
#include "esp_log.h"
#include "mem.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_netif_sntp.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "link.h"
#include "portal.h"

static const char *TAG = "wifi";
static volatile int connected, sntp_started;


static void on_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        if (g_cfg.wifi_ssid[0])
            esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        wifi_event_sta_disconnected_t *d = data;
        const char *why = d->reason == WIFI_REASON_NO_AP_FOUND ? "network not found"
                          : d->reason == WIFI_REASON_AUTH_FAIL || d->reason == WIFI_REASON_4WAY_HANDSHAKE_TIMEOUT ||
                                    d->reason == WIFI_REASON_HANDSHAKE_TIMEOUT
                              ? "wrong password?"
                          : d->reason == WIFI_REASON_ASSOC_FAIL ? "association refused"
                          : d->reason == WIFI_REASON_BEACON_TIMEOUT ? "signal lost"
                                                                    : "";
        if (connected)
            ESP_LOGW(TAG, "disconnected (reason %d %s)", d->reason, why);
        else
            ESP_LOGW(TAG, "cannot join \"%s\": reason %d %s", g_cfg.wifi_ssid, d->reason, why);
        connected = 0;
        link_set_wifi(0);
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *e = data;
        ESP_LOGI(TAG, "connected to \"%s\", ip " IPSTR, g_cfg.wifi_ssid, IP2STR(&e->ip_info.ip));
        connected = 1;
        link_set_wifi(1);
        if (!sntp_started) {
            esp_sntp_config_t sc = ESP_NETIF_SNTP_DEFAULT_CONFIG("pool.ntp.org");
            esp_netif_sntp_init(&sc);
            sntp_started = 1;
        }
    }
}

/* Reconnects with a growing delay. Without a connection for 30 s it opens the
 * setup access point, and keeps trying the configured network meanwhile
 * (every 60 s, when nobody is on the setup page): after a power cut the
 * router often boots slower than the Korvo. Once connected, the portal closes. */
#define AP_AFTER_US (30 * 1000000LL)

static void wifi_task(void *arg)
{
    int delay_s = 2;
    int64_t down_since = esp_timer_get_time(), last_try = 0;
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(1000));
        int64_t now = esp_timer_get_time();
        if (connected) {
            down_since = now;
            delay_s = 2;
            if (portal_active() && portal_auto_closable())
                portal_stop();
            continue;
        }
        if (!g_cfg.wifi_ssid[0])
            continue;                   /* nothing to join: the portal is open */
        if (!portal_active() && now - down_since > AP_AFTER_US) {
            ESP_LOGW(TAG, "cannot join \"%s\" for 30 s: opening the setup access point (still retrying)",
                     g_cfg.wifi_ssid);
            portal_start(0);
            last_try = now;
            continue;
        }
        int wait = portal_active() ? 60 : delay_s;
        if (now - last_try < (int64_t)wait * 1000000LL)
            continue;
        if (portal_active() && portal_clients() > 0)
            continue;                   /* someone is using the setup page */
        last_try = now;
        ESP_LOGI(TAG, "joining \"%s\"...", g_cfg.wifi_ssid);
        esp_wifi_connect();
        delay_s = delay_s < 16 ? delay_s * 2 : 16;
    }
}

void wifi_start(void)
{
    /* Espressif's QEMU has no radio (esp_phy_enable asserts); its efuse MAC
     * is all zeros. Real chips always have a factory MAC. */
    uint8_t mac[6] = {0};
    esp_efuse_mac_get_default(mac);
    if (!(mac[0] | mac[1] | mac[2] | mac[3] | mac[4] | mac[5])) {
        ESP_LOGW(TAG, "no factory MAC (emulator?): Wi-Fi disabled");
        return;
    }
    esp_netif_init();
    esp_event_loop_create_default();
    esp_netif_t *sta = esp_netif_create_default_wifi_sta();
    esp_netif_create_default_wifi_ap();
    char host[48];
    snprintf(host, sizeof host, "%s", g_cfg.device_id);
    esp_netif_set_hostname(sta, host);
    wifi_init_config_t ic = WIFI_INIT_CONFIG_DEFAULT();
    esp_wifi_init(&ic);
    esp_wifi_set_storage(WIFI_STORAGE_RAM);     /* credentials live in our own cfg partition */
    esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, on_event, NULL);
    esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, on_event, NULL);
    wifi_config_t wc = {0};
    memcpy(wc.sta.ssid, g_cfg.wifi_ssid, strnlen(g_cfg.wifi_ssid, sizeof wc.sta.ssid));
    memcpy(wc.sta.password, g_cfg.wifi_pass, strnlen(g_cfg.wifi_pass, sizeof wc.sta.password));
    wc.sta.threshold.authmode = g_cfg.wifi_pass[0] ? WIFI_AUTH_WEP : WIFI_AUTH_OPEN;
    wc.sta.scan_method = WIFI_ALL_CHANNEL_SCAN;
    wc.sta.sort_method = WIFI_CONNECT_AP_BY_SIGNAL;
    esp_wifi_set_mode(WIFI_MODE_STA);
    esp_wifi_set_config(WIFI_IF_STA, &wc);
    esp_wifi_start();
    esp_wifi_set_ps(WIFI_PS_NONE);              /* audio streaming: no modem sleep */
    if (!g_cfg.wifi_ssid[0]) {
        ESP_LOGW(TAG, "no Wi-Fi configured: opening the setup access point");
        portal_start(1);
    }
    task_create_psram(wifi_task, "wifi_mgr", 3072, NULL, 3, NULL, 0);
}

int wifi_rssi(void)
{
    wifi_ap_record_t ap;
    return connected && esp_wifi_sta_get_ap_info(&ap) == ESP_OK ? ap.rssi : 0;
}

int wifi_connected(void) { return connected; }
