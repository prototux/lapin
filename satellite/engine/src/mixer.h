#ifndef SATD_MIXER_H
#define SATD_MIXER_H

#include "common.h"

/* Output streams, by priority: alarm > tts / earcon > media. */
enum stream_kind { SK_MEDIA = 0, SK_TTS = 1, SK_EARCON = 2, SK_ALARM = 3 };

enum earcon_id { EC_WAKE, EC_END, EC_ERROR, EC_ALARM, EC_NOTIFY, EC_MUTE_ON, EC_MUTE_OFF,
                 EC_VOLUME, EC_OFFLINE, EC_DONE, EC_COUNT };

typedef struct {
    float volume;           /* 0..100 */
    int muted;              /* speaker mute */
    float duck_db;          /* media level while the assistant answers */
    float listen_duck_db;   /* media level while it listens to the user */
    float gain_db[4];       /* per kind */
    float eq_low_db, eq_mid_db, eq_high_db;
    float hpf_hz;
    float drc_threshold_db, drc_ratio, limiter_db;
    int earcons;
} mixer_cfg_t;

typedef struct {
    uint32_t id;
    int kind;
    int event;              /* MIXEV_* */
    int64_t frames;
} mixer_event_t;

enum { MIXEV_STARTED = 1, MIXEV_FINISHED, MIXEV_STOPPED, MIXEV_UNDERRUN, MIXEV_OVERFLOW };

void mixer_init(void);
void mixer_set_cfg(const mixer_cfg_t *c);
void mixer_get_cfg(mixer_cfg_t *c);

/* Streams. rate: 16000, 24000 or 48000; channels 1 or 2. start_at_ns:
 * CLOCK_REALTIME time of the first sample (0 = as soon as prebuffered). */
int mixer_open(uint32_t id, int kind, int rate, int channels, int64_t start_at_ns,
               float gain_db, int prebuffer_ms);
int mixer_write(uint32_t id, const int16_t *pcm, int samples);
void mixer_close(uint32_t id, int drain);
void mixer_pause(uint32_t id, int paused);
/* Stops every stream of the kinds in the mask (1 << kind). */
void mixer_stop_kinds(int mask);
void mixer_earcon(int which, int loop);
int mixer_active_kinds(void);     /* bitmask of kinds currently audible or pending */
/* Media ducking: 0 none, 1 answering (duck_db), 2 listening (listen_duck_db). */
void mixer_set_duck(int mode);

/* Renders one period (OUT_PERIOD stereo frames) to s16. */
void mixer_render(int16_t *out, int64_t output_latency_ns);
float mixer_out_db(void);
int mixer_poll_event(mixer_event_t *ev);
size_t mixer_queued_bytes(void);

#endif
