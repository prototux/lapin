/*
 * Audio tasks.
 *
 *  feed  (core 1): ES7210 4 lanes -> [mic, reference] -> AFE feed. Also
 *                  finds which lane carries the playback reference (see
 *                  calibrate()).
 *  fetch (core 0): AFE fetch (AEC with the hardware reference, noise
 *                  suppression, VAD; WakeNet off) -> 16 ms hops -> engine
 *                  (wake word, pre-roll, uplink, state machine).
 *  play  (core 1, prio 22): mixer -> ES8311 (48 kHz). The reference the AEC gets is
 *                  the DAC output looped back into the ES7210 (MIC3 on the
 *                  schematic): exactly what drives the speaker, already time
 *                  aligned with the microphones.
 */
#include "audio.h"

#include <math.h>
#include <string.h>

#include "board.h"
#include "engine.h"
#include "esp_afe_sr_models.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_task_wdt.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "mixer.h"
#include "mem.h"

static const char *TAG = "audio";

static const esp_afe_sr_iface_t *afe;
static esp_afe_sr_data_t *afe_data;
static audio_stats_t st;

/* ------------------------------------------------- reference detection --- */
/*
 * The ES7210's four channels arrive as four 16-bit lanes packed in two
 * 32-bit I2S slots; which lane is the loopback depends on the codec's TDM
 * ordering (Espressif's own board files disagree). The reference lane is the
 * one whose level rises the most when the device plays (it is electrical:
 * near silence otherwise, while the microphones always hear the room). Every
 * loud playback (the boot chime included) re-checks it; a new decision is
 * saved to NVS.
 */
static float idle_db[4] = {-60, -60, -60, -60}, play_db[4];
static int play_n, confirmed;

static void calibrate(const int16_t *raw, int frames)
{
    /* single precision only: doubles are emulated in software on the ESP32 */
    float db[4];
    for (int l = 0; l < 4; l++) {
        float e = 0;
        for (int i = 0; i < frames; i += 2) {      /* every other frame is plenty for a level */
            float x = raw[4 * i + l] * (1.0f / 32768.0f);
            e += x * x;
        }
        db[l] = 10 * log10f(e / (frames / 2) + 1e-12f);
    }
    /* diagnosis (sent to the server): the level of each ES7210 lane, every ~10 s */
    static int diag_n;
    if (++diag_n % 312 == 0)
        ESP_LOGW(TAG, "diag: lane levels %.0f / %.0f / %.0f / %.0f dBFS (ref %d, mic %d), output %.0f dBFS",
                 db[0], db[1], db[2], db[3], st.ref_lane, st.mic_lane, mixer_out_db());
    float out = mixer_out_db();
    if (out < -80) {                    /* nothing playing: the floor of each lane */
        for (int l = 0; l < 4; l++)
            idle_db[l] += (db[l] - idle_db[l]) * 0.05f;
        /* a microphone that hears nothing (badly seated ribbon, dead capsule):
         * listen on the lane that does hear the room, every ~5 s */
        static int pick_n;
        if (++pick_n % 156 == 0) {
            int best = -1;
            for (int l = 0; l < 4; l++)
                if (l != st.ref_lane && (best < 0 || idle_db[l] > idle_db[best]))
                    best = l;
            if (best >= 0 && best != st.mic_lane && idle_db[best] > idle_db[st.mic_lane] + 12) {
                ESP_LOGW(TAG, "microphone lane %d hears nothing (%.0f dBFS): using lane %d (%.0f dBFS)",
                         st.mic_lane, idle_db[st.mic_lane], best, idle_db[best]);
                st.mic_lane = best;
                st.lanes_changed = 1;
            }
        }
        if (play_n > 0 && play_n < 8)
            play_n = 0;                 /* too short to judge */
    } else if (out > -50) {             /* playing loud enough */
        for (int l = 0; l < 4; l++)
            play_db[l] = play_n ? play_db[l] + (db[l] - play_db[l]) * 0.1f : db[l];
        play_n++;
    }
    if (play_n >= 8 && out < -80) {
        int best = 0, second = -1;
        for (int l = 0; l < 4; l++) {
            st.lane_ratio_db[l] = play_db[l] - idle_db[l];
            if (st.lane_ratio_db[l] > st.lane_ratio_db[best])
                best = l;
        }
        for (int l = 0; l < 4; l++)
            if (l != best && (second < 0 || st.lane_ratio_db[l] > st.lane_ratio_db[second]))
                second = l;
        memcpy(st.lane_idle_db, idle_db, sizeof idle_db);
        int sure = st.lane_ratio_db[best] > 15 && st.lane_ratio_db[best] - st.lane_ratio_db[second] > 6;
        if (sure && best == st.ref_lane && !confirmed) {
            ESP_LOGI(TAG, "playback reference confirmed on lane %d (+%.0f dB when playing, next +%.0f dB)", best,
                     st.lane_ratio_db[best], st.lane_ratio_db[second]);
            st.lanes_changed = 1;           /* save it: no boot chime next time */
        }
        if (sure)
            confirmed = 1;
        if (sure && best != st.ref_lane) {
            /* microphone: the liveliest other lane at rest */
            int mic = -1;
            for (int l = 0; l < 4; l++)
                if (l != best && (mic < 0 || idle_db[l] > idle_db[mic]))
                    mic = l;
            ESP_LOGW(TAG, "playback reference found on lane %d (+%.0f dB when playing, next +%.0f dB); mic lane %d",
                     best, st.lane_ratio_db[best], st.lane_ratio_db[second], mic);
            st.ref_lane = best;
            st.mic_lane = mic;
            st.lanes_changed = 1;           /* the AEC re-adapts to the new reference by itself */
        }
        play_n = 0;
    }
}

/* ------------------------------------------------------------- tasks --- */

static volatile int paused;          /* during a firmware update */
static TaskHandle_t play_handle, feed_handle, fetch_handle;

void audio_set_paused(int on) { paused = on; }

static void feed_task(void *arg)
{
    const int chunk = afe->get_feed_chunksize(afe_data);
    /* MALLOC_CAP_8BIT: plain INTERNAL may return the IRAM heap, which only
     * allows 32-bit accesses (int16 loads there are a LoadStoreError) */
    int16_t *raw = mem_alloc_internal(sizeof(int16_t) * 4 * (size_t)chunk);
    int16_t *feed = mem_alloc_internal(sizeof(int16_t) * 2 * (size_t)chunk);
    if (!raw || !feed) {
        ESP_LOGE(TAG, "feed: out of memory");
        vTaskDelete(NULL);
    }
    esp_task_wdt_add(NULL);
    for (;;) {
        esp_task_wdt_reset();
        if (paused) {
            vTaskDelay(pdMS_TO_TICKS(50));
            continue;
        }
        if (board_read_mics(raw, chunk) != ESP_OK) {
            vTaskDelay(pdMS_TO_TICKS(10));
            continue;
        }
        calibrate(raw, chunk);
        const int m = st.mic_lane, r = st.ref_lane;
        for (int i = 0; i < chunk; i++) {
            feed[2 * i] = raw[4 * i + m];
            feed[2 * i + 1] = raw[4 * i + r];
        }
        afe->feed(afe_data, feed);
    }
}

/* Is what the AFE hears only our own residual echo? The residual level
 * relative to the playback level is learned while nobody talks; a voice
 * clearly above it is double talk (someone speaking over the device). */
static int far_only_estimate(const int16_t *hop, int vad)
{
    static float resid = -30;           /* AFE output minus playback level, dB */
    float e = 0;
    for (int i = 0; i < HOP; i++) {
        float v = hop[i] * (1.0f / 32768.0f);
        e += v * v;
    }
    float lvl = 10 * log10f(e / HOP + 1e-12f);
    float out = mixer_out_db();
    if (out < -60)
        return 0;                       /* not playing */
    float rel = lvl - out;
    if (!vad)
        resid += (rel - resid) * 0.02f;
    return rel < resid + 8;
}

static void fetch_task(void *arg)
{
    int16_t hop[HOP];
    int nh = 0;
    double sum_us = 0, sum_afe = 0;
    int nsum = 0, nafe = 0;
    float max_us = 0;
    esp_task_wdt_add(NULL);
    for (;;) {
        esp_task_wdt_reset();
        int64_t t0 = esp_timer_get_time();
        afe_fetch_result_t *res = afe->fetch_with_delay(afe_data, pdMS_TO_TICKS(1000));
        if (!res || res->ret_value == ESP_FAIL || !res->data)
            continue;
        sum_afe += (double)(esp_timer_get_time() - t0);
        nafe++;
        if (res->ringbuff_free_pct < 0.2f)
            st.overruns++;
        const int vad = res->vad_state == VAD_SPEECH;
        const int16_t *d = res->data;
        int n = res->data_size / 2;
        for (int i = 0; i < n; i++) {
            hop[nh++] = d[i];
            if (nh == HOP) {
                nh = 0;
                int64_t a = esp_timer_get_time();
                engine_process(hop, vad, far_only_estimate(hop, vad));
                float us = (float)(esp_timer_get_time() - a);
                sum_us += us;
                max_us = us > max_us ? us : max_us;
                if (++nsum == 250) {
                    st.engine_us = (float)(sum_us / nsum);
                    st.engine_max_us = max_us;
                    st.afe_us = nafe ? (float)(sum_afe / nafe) : 0;
                    sum_us = sum_afe = 0;
                    nsum = nafe = 0;
                    max_us = 0;
                }
            }
        }
    }
}

static void play_task(void *arg)
{
    int16_t buf[OUT_PERIOD];
    const int64_t lat = (int64_t)board_speaker_latency_ms() * 1000000LL;
    esp_task_wdt_add(NULL);
    int64_t sum = 0, max = 0;
    int n = 0;
    for (;;) {
        esp_task_wdt_reset();
        int64_t t0 = esp_timer_get_time();
        mixer_render(buf, lat);
        int64_t dt = esp_timer_get_time() - t0;
        /* diagnosis: render time of a 10 ms period, reported every 10 s while playing */
        sum += dt;
        max = dt > max ? dt : max;
        if (++n == 1000) {
            if (mixer_out_db() > -100 || max > 2000)
                ESP_LOGW(TAG, "diag: mixer render %lld us avg, %lld max per 10 ms; speaker underflows %lu",
                         sum / n, max, (unsigned long)board_tx_underflows());
            sum = max = 0;
            n = 0;
        }
        if (board_write_speaker(buf, OUT_PERIOD) != ESP_OK)
            vTaskDelay(pdMS_TO_TICKS(10));
    }
}

esp_err_t audio_start(int ref_lane, int mic_lane)
{
    /* defaults from Espressif's board file (bsp_get_feed_data: lane 0 is the
     * reference, lane 1 a microphone); corrected by calibrate() */
    st.ref_lane = ref_lane >= 0 && ref_lane < 4 ? ref_lane : 0;
    st.mic_lane = mic_lane >= 0 && mic_lane < 4 && mic_lane != st.ref_lane ? mic_lane : (st.ref_lane == 1 ? 0 : 1);
    confirmed = ref_lane >= 0;          /* found at an earlier boot */

    afe_config_t *cfg = afe_config_init("MR", NULL, AFE_TYPE_SR, AFE_MODE_LOW_COST);
    if (!cfg)
        return ESP_FAIL;
    cfg->wakenet_init = false;          /* the wake word is our own DTW detector */
    cfg->aec_init = true;
    cfg->aec_mode = AEC_MODE_SR_LOW_COST;
    cfg->se_init = false;               /* one microphone */
    /* ESP-SR's NS calls heap_caps_check_integrity_all() on every chunk (a
     * whole-heap walk, PSRAM included): it ate a full core and tripped the
     * task watchdog. The engine runs its own suppressor (MCRA, as on the
     * ReSpeaker) on the AFE output anyway. */
    cfg->ns_init = false;
    cfg->vad_init = true;
    cfg->vad_mode = VAD_MODE_1;
    cfg->vad_model_name = NULL;         /* WebRTC VAD (no model partition) */
    cfg->vad_min_speech_ms = 64;
    cfg->vad_min_noise_ms = 160;        /* responsive: the wake word gate counts voiced frames */
    cfg->agc_init = false;              /* the engine has its own speech AGC */
    cfg->memory_alloc_mode = mem_has_psram() ? AFE_MEMORY_ALLOC_MORE_PSRAM : AFE_MEMORY_ALLOC_MORE_INTERNAL;
    cfg->afe_perferred_core = 1;
    cfg->afe_perferred_priority = 8;
    cfg->afe_ringbuf_size = 30;
    cfg->afe_linear_gain = 1.0f;
    cfg = afe_config_check(cfg);
    afe_config_print(cfg);
    afe = esp_afe_handle_from_config(cfg);
    afe_data = afe ? afe->create_from_config(cfg) : NULL;
    afe_config_free(cfg);
    if (!afe_data) {
        ESP_LOGE(TAG, "AFE creation failed");
        return ESP_FAIL;
    }
    afe->print_pipeline(afe_data);
    st.feed_chunk = afe->get_feed_chunksize(afe_data);
    st.fetch_chunk = afe->get_fetch_chunksize(afe_data);
    ESP_LOGI(TAG, "AFE: feed %d samples x %d ch, fetch %d samples; lanes: mic %d, reference %d", st.feed_chunk,
             afe->get_feed_channel_num(afe_data), st.fetch_chunk, st.mic_lane, st.ref_lane);

    /* In ESP-SR's single-mic AFE the AEC and NS run inside feed(): that is the
     * heavy task. Core 0 has Wi-Fi, the link and the mixer (and the template
     * builder at connection), so the AFE and the wake word go to core 1. */
    /* The speaker on core 1, above everything there (AFE feed 10, AFE task 8)
     * and above lwIP (18): on core 0 it shared the core with the Wi-Fi task
     * (23), whose WPA3 handshake alone held the core for > 100 ms, and with
     * the engine; its work is small (< 1 ms per 10 ms period) and it blocks
     * on the I2S DMA the rest of the time. */
    xTaskCreatePinnedToCore(play_task, "play", 6144, NULL, 22, &play_handle, 1);
    xTaskCreatePinnedToCore(feed_task, "feed", 4096, NULL, 10, &feed_handle, 1);
    /* the engine (wake word, uplink) on core 0 with Wi-Fi; 12 KB of stack
     * (the high-water mark is reported in the diag lines) */
    xTaskCreatePinnedToCore(fetch_task, "fetch", 12 * 1024, NULL, 7, &fetch_handle, 0);
    return ESP_OK;
}

audio_stats_t audio_stats(void) { return st; }
void audio_lanes_saved(void) { st.lanes_changed = 0; }
