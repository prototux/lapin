#pragma once

#include <stdint.h>

#include "esp_err.h"

/* Microphone path (ES7210 -> ESP-SR AFE -> engine) and speaker path
 * (mixer -> ES8311). */
esp_err_t audio_start(int ref_lane, int mic_lane);

typedef struct {
    int ref_lane, mic_lane;
    int lanes_changed;          /* calibration picked new lanes (to persist) */
    float lane_idle_db[4], lane_ratio_db[4];
    float engine_us, engine_max_us;     /* per 16 ms hop */
    float afe_us;                       /* AFE fetch processing per chunk */
    int feed_chunk, fetch_chunk;
    uint32_t overruns;
} audio_stats_t;

audio_stats_t audio_stats(void);
/* Stops feeding the AFE (firmware update in progress), 0 resumes. */
void audio_set_paused(int on);
void audio_lanes_saved(void);
