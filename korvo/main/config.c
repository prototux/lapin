#include "config.h"

#include <stdio.h>
#include <string.h>

#include "esp_log.h"
#include "esp_mac.h"
#include "esp_random.h"
#include "nvs.h"
#include "nvs_flash.h"

static const char *TAG = "config";
config_t g_cfg;

static void get_str(nvs_handle_t h, const char *key, char *out, size_t n, const char *def)
{
    size_t len = n;
    if (nvs_get_str(h, key, out, &len) != ESP_OK)
        snprintf(out, n, "%s", def);
}

static uint8_t get_u8(nvs_handle_t h, const char *key, uint8_t def)
{
    uint8_t v;
    return nvs_get_u8(h, key, &v) == ESP_OK ? v : def;
}

static void b64url(const uint8_t *in, int n, char *out)
{
    static const char tbl[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    int o = 0;
    uint32_t acc = 0;
    int bits = 0;
    for (int i = 0; i < n; i++) {
        acc = acc << 8 | in[i];
        bits += 8;
        while (bits >= 6) {
            bits -= 6;
            out[o++] = tbl[(acc >> bits) & 63];
        }
    }
    if (bits)
        out[o++] = tbl[(acc << (6 - bits)) & 63];
    out[o] = 0;
}

static void defaults(void)
{
    snprintf(g_cfg.server_url, sizeof g_cfg.server_url, "%s", DEFAULT_SERVER_URL);
    g_cfg.led_bright = 60;
    g_cfg.mic_gain = 30;
    g_cfg.dac_volume = 80;
    g_cfg.volume = 60;
    g_cfg.ref_lane = g_cfg.mic_lane = -1;
}

/* Never fatal: whatever fails, the device boots with defaults (and opens the
 * setup access point if it has no Wi-Fi settings). */
esp_err_t config_init(void)
{
    memset(&g_cfg, 0, sizeof g_cfg);
    defaults();
    esp_err_t err = nvs_flash_init(), ret = ESP_OK;
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_LOGW(TAG, "nvs partition reformatted (%s)", esp_err_to_name(err));
        nvs_flash_erase();
        err = nvs_flash_init();
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "nvs partition unusable (%s): identity not kept across reboots", esp_err_to_name(err));
        ret = err;
    }
    esp_err_t cerr = nvs_flash_init_partition("cfg");
    if (cerr == ESP_ERR_NVS_NO_FREE_PAGES || cerr == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase_partition("cfg");
        cerr = nvs_flash_init_partition("cfg");
    }
    if (cerr != ESP_OK)
        ESP_LOGE(TAG, "no cfg partition (%s): run flash.sh again", esp_err_to_name(cerr));

    nvs_handle_t h;
    if (cerr == ESP_OK && nvs_open_from_partition("cfg", "korvo", NVS_READONLY, &h) == ESP_OK) {
        get_str(h, "wifi_ssid", g_cfg.wifi_ssid, sizeof g_cfg.wifi_ssid, "");
        get_str(h, "wifi_pass", g_cfg.wifi_pass, sizeof g_cfg.wifi_pass, "");
        get_str(h, "server_url", g_cfg.server_url, sizeof g_cfg.server_url, DEFAULT_SERVER_URL);
        get_str(h, "name", g_cfg.name, sizeof g_cfg.name, "");
        get_str(h, "room", g_cfg.room, sizeof g_cfg.room, "");
        get_str(h, "owner", g_cfg.owner, sizeof g_cfg.owner, "");
        g_cfg.led_bright = get_u8(h, "led_bright", 60);
        g_cfg.led_idle = get_u8(h, "led_idle", 0);
        g_cfg.mic_gain = get_u8(h, "mic_gain", 30);
        g_cfg.dac_volume = get_u8(h, "dac_volume", 80);
        nvs_close(h);
    } else {
        ESP_LOGW(TAG, "no settings: using defaults");
    }
    if (!g_cfg.server_url[0])
        snprintf(g_cfg.server_url, sizeof g_cfg.server_url, "%s", DEFAULT_SERVER_URL);

    /* identity: generated once, kept forever */
    int have_nvs = err == ESP_OK && nvs_open("korvo_id", NVS_READWRITE, &h) == ESP_OK;
    if (have_nvs) {
        get_str(h, "device_id", g_cfg.device_id, sizeof g_cfg.device_id, "");
        get_str(h, "token", g_cfg.token, sizeof g_cfg.token, "");
    }
    if (!g_cfg.device_id[0] || !g_cfg.token[0]) {
        uint8_t mac[6] = {0}, rnd[32];
        esp_read_mac(mac, ESP_MAC_WIFI_STA);
        snprintf(g_cfg.device_id, sizeof g_cfg.device_id, "korvo-%02x%02x%02x%02x%02x%02x", mac[0], mac[1], mac[2],
                 mac[3], mac[4], mac[5]);
        esp_fill_random(rnd, sizeof rnd);
        b64url(rnd, sizeof rnd, g_cfg.token);
        if (have_nvs) {
            nvs_set_str(h, "device_id", g_cfg.device_id);
            nvs_set_str(h, "token", g_cfg.token);
            nvs_commit(h);
        }
        ESP_LOGI(TAG, "new identity %s", g_cfg.device_id);
    }
    if (have_nvs)
        nvs_close(h);
    if (!g_cfg.name[0])
        snprintf(g_cfg.name, sizeof g_cfg.name, "Korvo %.6s", g_cfg.device_id + strlen(g_cfg.device_id) - 6);

    if (err == ESP_OK && nvs_open("korvo_rt", NVS_READWRITE, &h) == ESP_OK) {
        g_cfg.volume = get_u8(h, "volume", 60);
        g_cfg.mic_muted = get_u8(h, "mic_muted", 0);
        int8_t v;
        if (nvs_get_i8(h, "ref_lane", &v) == ESP_OK)
            g_cfg.ref_lane = v;
        if (nvs_get_i8(h, "mic_lane", &v) == ESP_OK)
            g_cfg.mic_lane = v;
        uint32_t boots = 0;
        nvs_get_u32(h, "boot_count", &boots);
        g_cfg.boot_count = ++boots;
        nvs_set_u32(h, "boot_count", boots);
        nvs_commit(h);
        nvs_close(h);
    }
    ESP_LOGI(TAG, "%s \"%s\" (room \"%s\"), server %s, wifi \"%s\", boot #%lu", g_cfg.device_id, g_cfg.name,
             g_cfg.room, g_cfg.server_url, g_cfg.wifi_ssid, (unsigned long)g_cfg.boot_count);
    return ret;
}

esp_err_t config_save_user(void)
{
    nvs_handle_t h;
    esp_err_t err = nvs_open_from_partition("cfg", "korvo", NVS_READWRITE, &h);
    if (err != ESP_OK)
        return err;
    nvs_set_str(h, "wifi_ssid", g_cfg.wifi_ssid);
    nvs_set_str(h, "wifi_pass", g_cfg.wifi_pass);
    nvs_set_str(h, "server_url", g_cfg.server_url);
    nvs_set_str(h, "name", g_cfg.name);
    nvs_set_str(h, "room", g_cfg.room);
    nvs_set_str(h, "owner", g_cfg.owner);
    err = nvs_commit(h);
    nvs_close(h);
    return err;
}

esp_err_t config_save_runtime(void)
{
    nvs_handle_t h;
    esp_err_t err = nvs_open("korvo_rt", NVS_READWRITE, &h);
    if (err != ESP_OK)
        return err;
    nvs_set_u8(h, "volume", g_cfg.volume);
    nvs_set_u8(h, "mic_muted", g_cfg.mic_muted);
    nvs_set_i8(h, "ref_lane", g_cfg.ref_lane);
    nvs_set_i8(h, "mic_lane", g_cfg.mic_lane);
    err = nvs_commit(h);
    nvs_close(h);
    return err;
}
