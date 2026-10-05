/*
 * LED ring driver: renders the satellite's animations (anim.c) at 50 fps on
 * the 12 WS2812 (RMT, GPIO 33). The base animation follows the engine state,
 * like the satellite's LED plugin: alarm > listening / thinking / speaking >
 * muted > pending approval > offline > notification > idle.
 */
#include "leds.h"

#include <math.h>
#include <string.h>

#include "anim.h"
#include "board.h"
#include "engine.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "mem.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "led_strip.h"

static const char *TAG = "leds";
static led_strip_handle_t strip;
static float bright = 0.6f;
static int idle_mode;
static volatile int setup_mode, updating;
static volatile float progress;
static float last_volume = 60;

void leds_set_setup_mode(int on) { setup_mode = on; }
void leds_set_progress(int active, float f)
{
    updating = active;
    progress = f;
}
void leds_set_config(int brightness, int idle_breathe)
{
    bright = brightness / 100.0f;
    idle_mode = idle_breathe;
}

void leds_event(const char *ev, int arg)
{
    if (!strcmp(ev, "wake"))
        anim_overlay(AO_WAKE, 0);
    else if (!strcmp(ev, "error"))
        anim_overlay(AO_ERROR, 0);
    else if (!strcmp(ev, "success"))
        anim_overlay(AO_SUCCESS, 0);
    else if (!strcmp(ev, "volume")) {
        last_volume = (float)arg;
        anim_overlay(AO_VOLUME, (float)arg);
    } else if (!strcmp(ev, "mute"))
        anim_overlay(AO_MUTE, (float)arg);
    else if (!strcmp(ev, "boot"))
        anim_overlay(AO_BOOT, 0);
}

static int pick_base(const engine_status_t *s)
{
    if (updating)
        return AB_UPDATE;
    if (setup_mode)
        return AB_SETUP;
    if (s->alarm)
        return AB_ALARM;
    if (s->state == ST_LISTENING)
        return AB_LISTENING;
    if (s->state == ST_THINKING)
        return AB_THINKING;
    if (s->state == ST_SPEAKING)
        return AB_SPEAKING;
    if (s->muted)
        return AB_MUTED;
    if (s->link == LINK_PENDING)
        return AB_PENDING;
    if (s->link != LINK_ONLINE)
        return AB_OFFLINE;
    if (s->notify)
        return AB_NOTIFY;
    return AB_IDLE;
}

static void led_task(void *arg)
{
    rgb_t px[ANIM_N];
    TickType_t last = xTaskGetTickCount();
    float think_t = 0;
    for (;;) {
        vTaskDelayUntil(&last, pdMS_TO_TICKS(20));
        engine_status_t s = engine_status();
        int base = pick_base(&s);
        /* safety net (as the satellite): never "thinking" for long without news */
        think_t = base == AB_THINKING ? think_t + 0.02f : 0;
        if (think_t > 20)
            base = AB_IDLE;
        anim_set_base(base);
        anim_ctx_t c = {.level = s.voice_level, .out = fminf(fmaxf((s.out_db + 50) / 38, 0), 1),
                        .volume = last_volume, .idle_breathe = idle_mode, .progress = progress};
        anim_render(0.02f, &c, px);
        float g = bright * bright;      /* perceptual brightness */
        for (int i = 0; i < ANIM_N; i++) {
            /* linear light -> WS2812 PWM (gamma 2.2) */
            uint8_t r = (uint8_t)(255 * powf(px[i].r, 2.2f) * g + 0.5f);
            uint8_t gg = (uint8_t)(255 * powf(px[i].g, 2.2f) * g + 0.5f);
            uint8_t b = (uint8_t)(255 * powf(px[i].b, 2.2f) * g + 0.5f);
            led_strip_set_pixel(strip, i, r, gg, b);
        }
        esp_err_t re = led_strip_refresh(strip);
        /* diagnosis (warning level: sent to the server) */
        static int frames, errors;
        frames++;
        errors += re != ESP_OK;
        if (frames % 500 == 0)
            ESP_LOGW(TAG, "diag: %d frames on GPIO %d, %d refresh errors (last: %s), state %d, brightness %.2f, "
                     "pixel0 %.2f/%.2f/%.2f", frames, BOARD_LED_GPIO, errors, esp_err_to_name(re), base, bright,
                     px[0].r, px[0].g, px[0].b);
    }
}

void leds_start(int brightness, int idle_breathe)
{
    bright = brightness / 100.0f;
    idle_mode = idle_breathe;
    /* Espressif's QEMU has no RMT (a refresh never completes); its efuse MAC
     * is all zeros, real chips always have one */
    uint8_t mac[6] = {0};
    esp_efuse_mac_get_default(mac);
    if (!(mac[0] | mac[1] | mac[2] | mac[3] | mac[4] | mac[5])) {
        ESP_LOGW(TAG, "no factory MAC (emulator?): LED ring disabled");
        return;
    }
    led_strip_config_t sc = {
        .strip_gpio_num = BOARD_LED_GPIO,
        .max_leds = BOARD_LED_COUNT,
        .led_model = LED_MODEL_WS2812,
        .color_component_format = LED_STRIP_COLOR_COMPONENT_FMT_GRB,
    };
    led_strip_rmt_config_t rc = {.clk_src = RMT_CLK_SRC_DEFAULT, .resolution_hz = 10 * 1000 * 1000};
    if (led_strip_new_rmt_device(&sc, &rc, &strip) != ESP_OK) {
        ESP_LOGE(TAG, "LED strip init failed");
        return;
    }
    anim_init();
    anim_overlay(AO_BOOT, 0);
    /* diagnosis: the whole ring white for 2 s at boot (does it light at all?) */
    for (int i = 0; i < BOARD_LED_COUNT; i++)
        led_strip_set_pixel(strip, i, 60, 60, 60);
    esp_err_t te = led_strip_refresh(strip);
    ESP_LOGW(TAG, "diag: boot test, ring white on GPIO %d (refresh: %s)", BOARD_LED_GPIO, esp_err_to_name(te));
    vTaskDelay(pdMS_TO_TICKS(2000));
    task_create_psram(led_task, "leds", 4096, NULL, 4, NULL, 0);
}
