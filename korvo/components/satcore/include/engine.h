#ifndef SAT_ENGINE_H
#define SAT_ENGINE_H

#include "kws.h"
#include "sat_common.h"

/*
 * Device state machine and wake word decision, ported from the ReSpeaker
 * satellite engine (satellite/engine/src/engine.c: start_listening,
 * finish_session, the candidate / peak picking, refractory period, speech
 * coverage gate, end-of-speech timers) and the agent's relaying
 * (satellite/agent/satagent/agent.py).
 *
 * engine_process() is fed one 16 ms hop of the clean AFE output with the
 * AFE's VAD decision; it runs the wake word detector, keeps the pre-roll and
 * streams the microphone during a turn. Server messages come in through
 * proto.c, buttons through the engine_* commands below. Everything is
 * thread-safe (one lock, as in the satellite).
 */

enum { ST_IDLE, ST_LISTENING, ST_THINKING, ST_SPEAKING };
enum { LINK_OFFLINE, LINK_PENDING, LINK_ONLINE };

typedef struct {
    int kws_enabled, kws_barge_in, kws_refractory_ms, kws_min_matches, kws_auto_threshold;
    float kws_threshold, kws_playback_margin, kws_neg_margin, kws_tts_threshold;
    float ns_floor_db, vad_threshold_db;
    int agc_enabled;
    float agc_target_db, agc_max_gain_db;
    int eos_silence_ms, listen_timeout_ms, max_listen_ms, think_timeout_ms, eos_grace_ms;
    int preroll_ms, button_preroll_ms;
    int learn_negatives;
    int earcon_wake, earcon_end;
} engine_cfg_t;

typedef struct {
    int state, muted, link, alarm, notify;
    uint32_t wake_id;
    float voice_level;      /* 0..1 while someone speaks */
    float out_db;           /* playback level */
    float volume;
    float kws_cost, kws_threshold;
    float snr_db;
    int templates, wakes, rejected;
    int64_t frame;
} engine_status_t;

/* UI events (LED ring): "boot", "wake", "error", "success", "volume" (arg
 * volume), "mute" (arg muted), "state", "link", "notify" (arg on), "alarm"
 * (arg on). */
typedef void (*engine_ui_cb_t)(const char *event, int arg);
/* Persistent settings changed (volume, mic mute). */
typedef void (*engine_settings_cb_t)(float volume, int mic_muted);
/* A negative template was learned (persist it). */
typedef void (*engine_negative_cb_t)(const kws_template_t *t);

/* Before engine_init: pre-roll ring capacity (1000-2500 ms, default 2500). */
void engine_set_ring_ms(int ms);
void engine_init(void);
void engine_get_cfg(engine_cfg_t *c);
void engine_set_cfg(const engine_cfg_t *c);
void engine_set_callbacks(engine_ui_cb_t ui, engine_settings_cb_t settings, engine_negative_cb_t neg);

/* Installs a new wake word model (ownership passes to the engine; the old
 * one is freed). suggested: kws_suggest_threshold() result, applied when
 * kws_auto_threshold is set and there are 2+ samples. */
void engine_set_model(kws_model_t *m, float suggested);
/* Keywords of the current model, as a JSON array ("[\"dis_lapin\"]"). */
void engine_wake_words_json(char *out, size_t n);
/* Deep copy of the current model (free with kws_model_free + sat_free), and
 * the threshold in use; NULL if there is no model. */
kws_model_t *engine_model_copy(float *thr);
int engine_model_count(void);

/* Audio: one hop of 16 kHz clean audio (AFE output). vad: AFE voice
 * activity. far_only: the device is playing and nobody talks over it (the
 * voice the AFE hears is its own residual echo). */
void engine_process(const int16_t *clean, int vad, int far_only);

/* Link to the server. */
void engine_link(int link);

/* Commands (buttons, server messages). */
void engine_talk_button(void);              /* wake / end of turn / stop alarm */
void engine_wake(const char *source);
void engine_listen(int timeout_ms, int earcon);
void engine_eot(void);
void engine_cancel(const char *reason, int rejected_learn);
void engine_session_end(int follow_up, int follow_up_ms);
void engine_stop(int media);
void engine_button_stop(void);              /* local stop key: stop playback, alarm */
void engine_set_muted(int muted, int quiet);
void engine_toggle_mute(void);
/* Applies a volume (0..100); plays the volume tick and reports it to the
 * server when it changed (or always with announce). */
void engine_set_volume(float volume, int announce);
/* Restores saved settings at boot (no sound, no message). */
void engine_init_settings(float volume, int mic_muted);
void engine_volume_step(float delta);
void engine_set_speaker_muted(int muted);
void engine_alarm(int on);
void engine_led_hint(const char *pattern, int on);
void engine_earcon(const char *name, int loop);
/* Playback stream control (server stream_open etc.). */
void engine_stream_open(uint32_t id, const char *kind, int rate, int channels, int64_t start_at_local_ns,
                        float gain_db, int prebuffer_ms);
void engine_stream_close(uint32_t id, int drain);

engine_status_t engine_status(void);

#endif
