/*
 * Korvo satellite: an ESP32-Korvo V1.1 as a satellite of the self-hosted
 * voice assistant (docs/PROTOCOL.md, kind "satellite"), behaving like the
 * ReSpeaker satellites: personal wake word on the device (the satellites'
 * "dis lapin" DTW detector), turn streaming, answers and music playback with
 * ducking, LED ring, keys.
 *
 * Boot order, so that a failure further down never leaves a dark, silent
 * board: log capture -> LED ring (white sweep) -> settings -> wake word store
 * -> Wi-Fi and the server link -> keys -> codecs / AFE. Nothing after the
 * LEDs is fatal; problems are logged and reported to the server.
 */
#include <stdio.h>
#include <string.h>

#include "audio.h"
#include "board.h"
#include "buttons.h"
#include "config.h"
#include "engine.h"
#include "esp_app_desc.h"
#include "esp_chip_info.h"
#include "esp_heap_caps.h"
#include "esp_littlefs.h"
#include "esp_log.h"
#include "esp_ota_ops.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "fft.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "kws.h"
#include "leds.h"
#include "link.h"
#include "logbuf.h"
#include "mem.h"
#include "mixer.h"
#include "ota.h"
#include "proto.h"
#include "templates.h"
#include "wifi.h"

static const char *TAG = "main";

void port_start_worker(void);
void port_save_templates(void);
void port_ota_init(void);

static volatile int settings_dirty, audio_ok;
static int64_t settings_changed_us;

static void on_ui(const char *ev, int arg) { leds_event(ev, arg); }

static void on_settings(float volume, int muted)
{
    g_cfg.volume = (uint8_t)(volume + 0.5f);
    g_cfg.mic_muted = (uint8_t)muted;
    settings_dirty = 1;
    settings_changed_us = esp_timer_get_time();
}

static void on_negative(const kws_template_t *t) { port_save_templates(); }

static volatile int user_dirty;

static void on_server_config(const char *name, const char *room)
{
    snprintf(g_cfg.name, sizeof g_cfg.name, "%s", name);
    snprintf(g_cfg.room, sizeof g_cfg.room, "%s", room);
    user_dirty = 1;                     /* saved when the audio is quiet */
    ESP_LOGI(TAG, "renamed by the server: \"%s\" (room \"%s\")", name, room);
}

/* ------------------------------------------------------- flash writes ---
 * Writing flash (NVS, the LittleFS template store, otadata) turns the cache
 * off on both cores for each erase / program step: tens of ms per erased
 * sector, during which the speaker's task, its code and the PSRAM jitter
 * buffers are frozen (the 131 ms render stall at 26 s on 1.1.10 was the
 * template store write). So they wait until nothing plays, nobody talks and
 * no answer is pending, for 3 s. */
static int64_t quiet_since;
static void track_quiet(int64_t now)
{
    engine_status_t s = engine_status();
    int busy = mixer_active_kinds() != 0 || s.state != ST_IDLE || s.alarm;
    if (busy)
        quiet_since = 0;
    else if (!quiet_since)
        quiet_since = now;
}
int port_flash_quiet(void) { return quiet_since && esp_timer_get_time() - quiet_since > 3000000LL; }

/* After the first connection (not at power-up: a USB-powered board can sag
 * when the amplifier starts), a soft chime lets the firmware find which ES7210
 * lane carries the playback reference; it is saved, so it plays only once. */
void port_reference_chime(void)
{
    if (audio_ok && g_cfg.ref_lane < 0) {
        ESP_LOGI(TAG, "reference detection chime (once)");
        mixer_earcon_gain(EC_NOTIFY, -12);
    }
}

static uint32_t idle_prev[2];
static int64_t idle_t_prev;

/* CPU load per core from the idle tasks' run time */
static void cpu_load(float *c0, float *c1)
{
    int64_t now = esp_timer_get_time();
    uint32_t i0 = ulTaskGetIdleRunTimeCounterForCore(0), i1 = ulTaskGetIdleRunTimeCounterForCore(1);
    float dt = (float)(now - idle_t_prev);
    *c0 = idle_t_prev ? 100.0f - 100.0f * (float)(i0 - idle_prev[0]) / dt : 0;
    *c1 = idle_t_prev ? 100.0f - 100.0f * (float)(i1 - idle_prev[1]) / dt : 0;
    idle_prev[0] = i0;
    idle_prev[1] = i1;
    idle_t_prev = now;
}

static void telemetry(void)
{
    char buf[1100];
    engine_status_t s = engine_status();
    audio_stats_t a = audio_stats();
    float c0, c1;
    cpu_load(&c0, &c1);
    const esp_partition_t *run = esp_ota_get_running_partition();
    snprintf(buf, sizeof buf,
             "{\"type\":\"telemetry\",\"system\":{\"uptime_s\":%lld,\"heap_internal\":%u,\"heap_internal_min\":%u,"
             "\"heap_psram\":%u,\"psram_total\":%u,\"rssi\":%d,\"cpu0\":%.0f,\"cpu1\":%.0f,\"version\":\"%s\","
             "\"partition\":\"%s\",\"reset_reason\":\"%s\",\"boot_count\":%lu,\"audio\":%s},"
             "\"counters\":{\"wakes\":%d,\"rejected\":%d},"
             "\"engine\":{\"snr_db\":%.1f,\"kws_threshold\":%.3f,\"templates\":%d,\"engine_us\":%.0f,"
             "\"engine_max_us\":%.0f,\"afe_fetch_us\":%.0f,\"ref_lane\":%d,\"mic_lane\":%d,\"afe_overruns\":%u,"
             "\"tx_dropped\":%u},\"rtt_ms\":%d}",
             (long long)(esp_timer_get_time() / 1000000),
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT),
             (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT),
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM), (unsigned)heap_caps_get_total_size(MALLOC_CAP_SPIRAM),
             wifi_rssi(), c0, c1, SAT_VERSION, run ? run->label : "?", logbuf_reset_reason(),
             (unsigned long)g_cfg.boot_count, audio_ok ? "true" : "false", s.wakes, s.rejected, s.snr_db,
             s.kws_threshold, s.templates, a.engine_us, a.engine_max_us, a.afe_us, a.ref_lane, a.mic_lane,
             (unsigned)a.overruns, (unsigned)link_tx_dropped(), proto_rtt_ms());
    if (link_online())
        sat_send_text(buf);
    ESP_LOGI(TAG, "cpu %.0f%% / %.0f%%, engine %.0f us/hop (max %.0f), heap %u internal / %u psram, lanes ref %d "
                  "mic %d, state %d link %d",
             c0, c1, a.engine_us, a.engine_max_us,
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT),
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM), a.ref_lane, a.mic_lane, s.state, s.link);
}

/* ---------------------------------------------------------- diagnosis ---
 * Every 30 s (every 10 s while audio plays), sent to the server log:
 *   diag: cpu <task>@<core> <% of one core>/<free stack bytes> ...
 *   diag: heap internal free / min / largest block, wake word data placement */
static void diag(int64_t now)
{
    static int64_t last;
    static TaskStatus_t prev[32];
    static int nprev;
    static uint32_t prev_total;
    int playing = mixer_active_kinds() != 0;
    if (now - last < (playing ? 10000000LL : 30000000LL))
        return;
    last = now;
    TaskStatus_t *cur = mem_alloc_big(sizeof(TaskStatus_t) * 32);
    if (!cur)
        return;
    uint32_t total;
    int n = uxTaskGetSystemState(cur, 32, &total);
    uint32_t dtot = total - prev_total;
    char line[160];             /* + the log prefix: under the 200 characters a log line keeps */
    int len = snprintf(line, sizeof line, "diag: cpu%s", playing ? " (playing)" : "");
    for (int i = 0; i < n && dtot && nprev; i++) {
        uint32_t before = 0;
        int found = 0;
        for (int j = 0; j < nprev; j++)
            if (prev[j].xHandle == cur[i].xHandle) {
                before = prev[j].ulRunTimeCounter;
                found = 1;
            }
        if (!found)
            continue;
        uint32_t pct10 = (uint32_t)(1000ULL * (cur[i].ulRunTimeCounter - before) / dtot);
        int core = xTaskGetCoreID(cur[i].xHandle);
        char item[48];
        int k = snprintf(item, sizeof item, " %s@%c %lu.%lu%%/%lu", cur[i].pcTaskName,
                         core == 0 ? '0' : core == 1 ? '1' : '*', (unsigned long)(pct10 / 10),
                         (unsigned long)(pct10 % 10), (unsigned long)cur[i].usStackHighWaterMark);
        if (len + k >= (int)sizeof line - 1) {
            ESP_LOGW(TAG, "%s", line);
            len = snprintf(line, sizeof line, "diag: cpu+");
        }
        memcpy(line + len, item, (size_t)k + 1);
        len += k;
    }
    if (nprev)
        ESP_LOGW(TAG, "%s", line);
    memcpy(prev, cur, sizeof(TaskStatus_t) * (size_t)n);
    nprev = n;
    prev_total = total;
    heap_caps_free(cur);
    size_t hot_i, hot_p;
    mem_hot_stats(&hot_i, &hot_p);
    ESP_LOGW(TAG, "diag: heap internal %u free, %u min, %u largest; wake word data %u B internal, %u B psram",
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT),
             (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT),
             (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT), (unsigned)hot_i,
             (unsigned)hot_p);
}

void app_main(void)
{
    logbuf_init();
    const esp_app_desc_t *app = esp_app_get_description();
    const esp_partition_t *run = esp_ota_get_running_partition();
    ESP_LOGI(TAG, "Korvo satellite %s (%s %s), IDF %s, slot %s, reset: %s", app->version, app->date, app->time,
             app->idf_ver, run ? run->label : "?", logbuf_reset_reason());
    esp_chip_info_t chip;
    esp_chip_info(&chip);
    ESP_LOGI(TAG, "chip ESP32 revision v%d.%d, %d cores", chip.revision / 100, chip.revision % 100, chip.cores);
    const int psram = mem_has_psram();
    ESP_LOGI(TAG, "PSRAM: %u KB%s, internal free %u KB", (unsigned)(heap_caps_get_total_size(MALLOC_CAP_SPIRAM) / 1024),
             psram ? "" : " (none: degraded mode, small buffers, no earcons)",
             (unsigned)(heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT) / 1024));

    /* 1. alive: LED ring with a white sweep */
    leds_start(60, 0);

    /* 2. settings (never fatal) */
    if (config_init() != ESP_OK)
        ESP_LOGE(TAG, "settings storage problem: running with what could be read");
    logbuf_set_boot_count(g_cfg.boot_count);
    leds_set_config(g_cfg.led_bright, g_cfg.led_idle);

    /* 3. core: engine + mixer memory (degraded without PSRAM) */
    fft_init();
    kws_init();
    if (!psram) {
        engine_set_ring_ms(1000);
        mixer_set_max_queued(96 * 1024);
    }
    if (mixer_init(psram) != 0)
        ESP_LOGE(TAG, "mixer: out of memory (no earcons)");
    engine_init();
    engine_set_callbacks(on_ui, on_settings, on_negative);
    engine_init_settings(g_cfg.volume, g_cfg.mic_muted);

    proto_identity_t id;
    memset(&id, 0, sizeof id);
    snprintf(id.device_id, sizeof id.device_id, "%s", g_cfg.device_id);
    snprintf(id.token, sizeof id.token, "%s", g_cfg.token);
    snprintf(id.name, sizeof id.name, "%s", g_cfg.name);
    snprintf(id.room, sizeof id.room, "%s", g_cfg.room);
    snprintf(id.owner, sizeof id.owner, "%s", g_cfg.owner);
    proto_init(&id, on_server_config);
    port_ota_init();

    /* 4. wake word store (features of the server's recordings: works offline) */
    esp_vfs_littlefs_conf_t fs = {.base_path = "/store", .partition_label = "storage",
                                  .format_if_mount_failed = true};
    if (esp_vfs_littlefs_register(&fs) != ESP_OK)
        ESP_LOGE(TAG, "storage partition not mounted: the wake word will not survive a reboot offline");
    tpl_init("/store/kws.bin");
    if (tpl_load_store() <= 0)
        ESP_LOGW(TAG, "no wake word yet: it comes from the server at the first connection");

    /* 5. network first: the server gets the log even if the audio fails */
    port_start_worker();
    link_start();
    wifi_start();

    /* 6. keys, then the audio hardware (a missing codec never blocks) */
    buttons_start();
    esp_err_t berr = board_init(g_cfg.mic_gain, g_cfg.dac_volume);
    if (berr != ESP_OK) {
        ESP_LOGE(TAG, "audio codecs failed (%s): is the mic board connected? (network still works)",
                 esp_err_to_name(berr));
        leds_event("error", 0);
    } else if (audio_start(g_cfg.ref_lane, g_cfg.mic_lane) != ESP_OK) {
        ESP_LOGE(TAG, "audio front end failed");
    } else {
        audio_ok = 1;
        if (link_online())
            port_reference_chime();
    }
    ESP_LOGI(TAG, "ready: %u KB internal, %u KB PSRAM free",
             (unsigned)(heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT) / 1024),
             (unsigned)(heap_caps_get_free_size(MALLOC_CAP_SPIRAM) / 1024));

    int64_t last_tel = 0;
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(1000));
        int64_t now = esp_timer_get_time();
        logbuf_poll(link_online());
        ota_tick(now);
        audio_stats_t a = audio_stats();
        if (a.lanes_changed) {
            g_cfg.ref_lane = (int8_t)a.ref_lane;
            g_cfg.mic_lane = (int8_t)a.mic_lane;
            audio_lanes_saved();
            settings_dirty = 1;
        }
        track_quiet(now);
        int quiet = port_flash_quiet();
        if (quiet) {
            int port_take_store_dirty(void);
            if (port_take_store_dirty()) {
                int64_t t0 = esp_timer_get_time();
                tpl_save_current();
                ESP_LOGW(TAG, "diag: wake word store saved in %lld ms (deferred until quiet)",
                         (esp_timer_get_time() - t0) / 1000);
            } else if (settings_dirty && now - settings_changed_us > 2000000) {
                settings_dirty = 0;
                config_save_runtime();
            } else if (user_dirty) {
                user_dirty = 0;
                config_save_user();
            }
        }
        diag(now);
        if (now - last_tel > 30000000LL) {
            last_tel = now;
            telemetry();
        }
        {
            void port_ota_tick(int online, int quiet);
            port_ota_tick(engine_status().link == LINK_ONLINE, quiet);
        }
    }
}
