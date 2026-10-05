#include <stdlib.h>
/*
 * Keys (ADC ladder on GPIO 39, levels from Espressif's Korvo button driver):
 *   REC   talk: start a turn (wake source "button"); while listening, end it
 *         (audio_end); while an alarm rings, stop it
 *   MODE  microphone mute on / off
 *   PLAY  stop: stop the music / answer / alarm, or cancel the turn
 *   VOL+ / VOL-  volume (repeats while held)
 *   SET   hold 3 s: Wi-Fi setup (access point + web page)
 */
#include "buttons.h"

#include "engine.h"
#include "esp_adc/adc_cali.h"
#include "esp_adc/adc_cali_scheme.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_log.h"
#include "mem.h"
#include "esp_mac.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "portal.h"

static const char *TAG = "buttons";

enum { B_NONE, B_VOLUP, B_VOLDN, B_SET, B_PLAY, B_MODE, B_REC };
static const char *names[] = {"none", "vol+", "vol-", "set", "play", "mode", "rec"};

static adc_oneshot_unit_handle_t adc;
static adc_cali_handle_t cali;

/* nominal levels: VOL+ 0.38 V, VOL- 0.82, SET 1.11, PLAY 1.65, MODE 1.98,
 * REC 2.41; idle ~3.1 V (pull-up) */
static int decode(int mv)
{
    if (mv < 200)
        return B_NONE;          /* shorted / not connected: ignore */
    if (mv < 600)
        return B_VOLUP;
    if (mv < 965)
        return B_VOLDN;
    if (mv < 1380)
        return B_SET;
    if (mv < 1815)
        return B_PLAY;
    if (mv < 2200)
        return B_MODE;
    if (mv < 2750)
        return B_REC;
    return B_NONE;
}

static int read_mv(void)
{
    int raw = 0, mv = 0, acc = 0;
    for (int i = 0; i < 4; i++) {
        adc_oneshot_read(adc, ADC_CHANNEL_3, &raw);
        acc += raw;
    }
    raw = acc / 4;
    if (cali && adc_cali_raw_to_voltage(cali, raw, &mv) == ESP_OK)
        return mv;
    return raw * 3300 / 4095;   /* uncalibrated fallback */
}

static void on_press(int b)
{
    ESP_LOGI(TAG, "%s", names[b]);
    switch (b) {
    case B_REC:
        engine_talk_button();
        break;
    case B_MODE:
        engine_toggle_mute();
        break;
    case B_PLAY:
        engine_button_stop();
        break;
    case B_VOLUP:
        engine_volume_step(10);
        break;
    case B_VOLDN:
        engine_volume_step(-10);
        break;
    default:
        break;
    }
}

static void button_task(void *arg)
{
    int stable = B_NONE, cand = B_NONE, count = 0, held_ms = 0, long_done = 0;
    int last_logged = -1000, since = 0;
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(20));
        int mv = read_mv();
        int b = decode(mv);
        /* diagnosis (sent to the server: warning level): the ladder's voltage
         * whenever it moves, and every 30 s */
        since += 20;
        if (abs(mv - last_logged) > 80 || since >= 30000) {
            ESP_LOGW(TAG, "diag: key input %d mV -> %s (calibrated %s)", mv, b == B_NONE ? "none" : names[b],
                     cali ? "yes" : "no");
            last_logged = mv;
            since = 0;
        }
        if (b != cand) {
            cand = b;
            count = 0;
            continue;
        }
        if (++count < 3 && b != stable)
            continue;               /* 60 ms debounce */
        if (b != stable) {
            stable = b;
            held_ms = 0;
            long_done = 0;
            if (b != B_NONE && b != B_SET)
                on_press(b);
            continue;
        }
        if (stable == B_NONE)
            continue;
        held_ms += 20;
        if ((stable == B_VOLUP || stable == B_VOLDN) && held_ms >= 600 && held_ms % 300 == 0)
            on_press(stable);      /* auto-repeat */
        if (stable == B_SET && held_ms >= 3000 && !long_done) {
            long_done = 1;
            ESP_LOGW(TAG, "SET held 3 s: Wi-Fi setup");
            portal_start(1);
        }
    }
}

void buttons_start(void)
{
    /* Espressif's QEMU has no ADC (a read never completes and trips the
     * interrupt watchdog); its efuse MAC is all zeros */
    uint8_t mac[6] = {0};
    esp_efuse_mac_get_default(mac);
    if (!(mac[0] | mac[1] | mac[2] | mac[3] | mac[4] | mac[5])) {
        ESP_LOGW(TAG, "no factory MAC (emulator?): keys disabled");
        return;
    }
    adc_oneshot_unit_init_cfg_t uc = {.unit_id = ADC_UNIT_1};
    if (adc_oneshot_new_unit(&uc, &adc) != ESP_OK) {
        ESP_LOGE(TAG, "ADC init failed");
        return;
    }
    adc_oneshot_chan_cfg_t cc = {.atten = ADC_ATTEN_DB_12, .bitwidth = ADC_BITWIDTH_12};
    adc_oneshot_config_channel(adc, ADC_CHANNEL_3, &cc);
    adc_cali_line_fitting_config_t lc = {.unit_id = ADC_UNIT_1, .atten = ADC_ATTEN_DB_12,
                                         .bitwidth = ADC_BITWIDTH_12};
    if (adc_cali_create_scheme_line_fitting(&lc, &cali) != ESP_OK)
        cali = NULL;
    task_create_psram(button_task, "buttons", 3072, NULL, 5, NULL, 0);
}
