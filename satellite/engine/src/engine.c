/*
 * satd - real-time audio engine of the ReSpeaker Core v2 satellite.
 *
 * Capture path (every 16 ms):
 *   8 ch @ 48 kHz -> decimate to 16 kHz -> DC removal, mic gain calibration
 *   -> multichannel AEC (reference = hardware loopback of the output)
 *   -> STFT -> SRP-PHAT DOA -> speaker tracker
 *   -> fixed superdirective beams -> noise suppression -> VAD
 *        -> hotword detector per beam (best beam = talker direction)
 *        -> per-beam pre-roll buffers
 *   -> MVDR beam steered by the tracker -> noise suppression -> AGC -> uplink
 * Output path (every 10 ms): mixer / ducker -> EQ -> DRC -> limiter -> ALSA.
 *
 * The device state machine (idle -> listening -> thinking -> speaking) runs
 * here so the reaction to a wake word (duck the media, play the earcon, lock
 * the beam, start the uplink with its pre-roll) needs no round trip.
 * Control and audio go through a local socket (see ipc.h / docs/PROTOCOL.md).
 */
#include <alsa/asoundlib.h>
#include <errno.h>
#include <getopt.h>
#include <pthread.h>
#include <sched.h>
#include <signal.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "aec.h"
#include "beam.h"
#include "common.h"
#include "fft.h"
#include "ipc.h"
#include "json.h"
#include "kws.h"
#include "mixer.h"
#include "ns.h"
#include "res.h"
#include "sbaec.h"
#include "resample.h"
#include "track.h"

#define FRAME_NS   (1000000000LL * HOP / SR)
#define FPS        (SR / HOP)                    /* 62.5 -> 62 */
#define MS2F(ms)   ((int64_t)(ms) * SR / HOP / 1000)
#define RING       (SR * 5 / 2)                  /* 2.5 s of pre-roll */
#define DOA_HIST   64
#define MSG_MONITOR 4
#define SUB_MONITOR 4

int g_verbose = 1;

void logmsg(int level, const char *fmt, ...)
{
    if (level > g_verbose)
        return;
    va_list ap;
    va_start(ap, fmt);
    fprintf(stderr, "%s", level == 0 ? "E: " : level == 1 ? "I: " : "D: ");
    vfprintf(stderr, fmt, ap);
    fputc('\n', stderr);
    va_end(ap);
}

/* ------------------------------------------------------------ Config --- */

typedef struct {
    int aec_enabled, aec_tail_ms, res_enabled, aec_mode;
    float aec_mu;
    float res_strength, res_floor_db;
    int bf_mode, fixed_beams;
    float bf_loading;
    int ns_enabled;
    float ns_floor_db;
    int agc_enabled;
    float agc_target_db, agc_max_gain_db;
    float capture_gain_db, mic_gain_db[NMIC];
    int kws_enabled, kws_barge_in, kws_refractory_ms, kws_min_matches;
    float kws_threshold, kws_playback_margin, kws_neg_margin, kws_tts_threshold;
    float vad_threshold_db;
    int eos_silence_ms, listen_timeout_ms, max_listen_ms, think_timeout_ms, eos_grace_ms;
    int preroll_ms, button_preroll_ms;
    int mic_muted;
    float doa_min_conf, tracker_gate_deg;
    int meters_hz;
    int earcon_wake, earcon_end;
    mixer_cfg_t mix;
} cfg_t;

enum { T_F, T_I, T_B };
typedef struct {
    const char *name;
    int type;
    size_t off;
    float lo, hi;
    int reinit;
} cfg_field_t;

#define CF(n, t, lo, hi, r) {#n, t, offsetof(cfg_t, n), lo, hi, r}
#define CM(n, field, t, lo, hi) {n, t, offsetof(cfg_t, mix) + offsetof(mixer_cfg_t, field), lo, hi, 0}
static const cfg_field_t fields[] = {
    CF(aec_enabled, T_B, 0, 1, 0), CF(aec_tail_ms, T_I, 16, 400, 1),
    CF(res_enabled, T_B, 0, 1, 0), CF(aec_mu, T_F, 0.02f, 1, 0), CF(res_strength, T_F, 0.5f, 4, 0), CF(res_floor_db, T_F, -40, 0, 0),
    CF(fixed_beams, T_I, 1, MAX_BEAMS, 2), CF(bf_loading, T_F, 0.001f, 10, 2),
    CF(ns_enabled, T_B, 0, 1, 0), CF(ns_floor_db, T_F, -40, 0, 0),
    CF(agc_enabled, T_B, 0, 1, 0), CF(agc_target_db, T_F, -40, -3, 0),
    CF(agc_max_gain_db, T_F, 0, 40, 0), CF(capture_gain_db, T_F, -20, 30, 0),
    CF(kws_enabled, T_B, 0, 1, 0), CF(kws_barge_in, T_B, 0, 1, 0),
    CF(kws_refractory_ms, T_I, 200, 10000, 0), CF(kws_threshold, T_F, 0.02f, 0.8f, 0),
    CF(kws_playback_margin, T_F, -0.1f, 0.3f, 0), CF(kws_tts_threshold, T_F, 0.2f, 0.8f, 0), CF(kws_min_matches, T_I, 0, KWS_MAX_TPL, 0),
    CF(kws_neg_margin, T_F, 0, 0.3f, 0), CF(vad_threshold_db, T_F, 1, 30, 0),
    CF(eos_silence_ms, T_I, 200, 5000, 0), CF(listen_timeout_ms, T_I, 1000, 30000, 0),
    CF(max_listen_ms, T_I, 2000, 60000, 0), CF(think_timeout_ms, T_I, 2000, 120000, 0),
    CF(eos_grace_ms, T_I, 0, 5000, 0),
    CF(preroll_ms, T_I, 0, 2400, 0), CF(button_preroll_ms, T_I, 0, 2400, 0),
    CF(mic_muted, T_B, 0, 1, 0), CF(doa_min_conf, T_F, 0, 1, 0),
    CF(tracker_gate_deg, T_F, 10, 180, 0), CF(meters_hz, T_I, 1, 30, 0),
    CF(earcon_wake, T_B, 0, 1, 0), CF(earcon_end, T_B, 0, 1, 0),
    CM("volume", volume, T_F, 0, 100), CM("speaker_muted", muted, T_B, 0, 1),
    CM("duck_db", duck_db, T_F, -60, 0), CM("listen_duck_db", listen_duck_db, T_F, -80, 0), CM("media_gain_db", gain_db[SK_MEDIA], T_F, -30, 12),
    CM("tts_gain_db", gain_db[SK_TTS], T_F, -30, 12),
    CM("earcon_gain_db", gain_db[SK_EARCON], T_F, -30, 12),
    CM("alarm_gain_db", gain_db[SK_ALARM], T_F, -30, 12),
    CM("eq_low_db", eq_low_db, T_F, -12, 12), CM("eq_mid_db", eq_mid_db, T_F, -12, 12),
    CM("eq_high_db", eq_high_db, T_F, -12, 12), CM("hpf_hz", hpf_hz, T_F, 0, 400),
    CM("drc_threshold_db", drc_threshold_db, T_F, -40, 0), CM("drc_ratio", drc_ratio, T_F, 1, 20),
    CM("limiter_db", limiter_db, T_F, -12, 0), CM("earcons", earcons, T_B, 0, 1),
};
static const char *bf_names[] = {"mvdr", "superdirective", "das", "mic1"};
static const char *aec_names[] = {"subband", "speex"};

static void cfg_defaults(cfg_t *c)
{
    memset(c, 0, sizeof *c);
    c->aec_enabled = 1;
    c->aec_tail_ms = 128;
    c->aec_mode = 0;                /* subband */
    c->aec_mu = 0.3f;
    c->res_enabled = 1;
    c->res_strength = 1.0f;
    c->res_floor_db = -10;
    c->bf_mode = BF_SUPERDIRECTIVE;      /* robust; MVDR can cancel the talker */
    c->fixed_beams = 6;
    c->bf_loading = 0.05f;
    c->ns_enabled = 1;
    c->ns_floor_db = -8;            /* gentle: recognizers dislike heavy suppression */
    c->agc_enabled = 1;
    c->agc_target_db = -20;
    c->agc_max_gain_db = 24;
    c->kws_enabled = 1;
    c->kws_barge_in = 1;
    c->kws_refractory_ms = 1500;
    c->kws_threshold = 0.38f;
    c->kws_min_matches = 0;         /* 0 = auto: 2 with 3+ samples */
    c->kws_neg_margin = 0.03f;
    c->kws_playback_margin = 0.08f;     /* added to the threshold while music plays */
    c->kws_tts_threshold = 0.44f;       /* ceiling while its own voice plays */
    c->vad_threshold_db = 6;
    c->eos_silence_ms = 1200;
    c->listen_timeout_ms = 6000;
    c->max_listen_ms = 15000;
    c->think_timeout_ms = 15000;
    c->eos_grace_ms = 1500;
    c->preroll_ms = 1500;
    c->button_preroll_ms = 300;
    c->doa_min_conf = 0.12f;
    c->tracker_gate_deg = 45;
    c->meters_hz = 12;
    c->earcon_wake = 1;
    c->earcon_end = 1;
    mixer_get_cfg(&c->mix);
}

/* Applies the known keys of a JSON object; returns a mask of what needs rebuilding. */
static int cfg_apply(cfg_t *c, const char *js)
{
    int reinit = 0;
    double v;
    for (size_t i = 0; i < sizeof fields / sizeof fields[0]; i++) {
        const cfg_field_t *f = &fields[i];
        if (!json_get_num(js, f->name, &v))
            continue;
        v = clampf((float)v, f->lo, f->hi);
        char *p = (char *)c + f->off;
        if (f->type == T_F) {
            if (*(float *)p != (float)v)
                reinit |= f->reinit;
            *(float *)p = (float)v;
        } else {
            int iv = f->type == T_B ? v != 0 : (int)lrint(v);
            if (*(int *)p != iv)
                reinit |= f->reinit;
            *(int *)p = iv;
        }
    }
    char s[32];
    if (json_get_str(js, "aec_mode", s, sizeof s))
        for (int i = 0; i < 2; i++)
            if (!strcmp(s, aec_names[i]) && c->aec_mode != i) {
                c->aec_mode = i;
                reinit |= 1;
            }
    if (json_get_str(js, "bf_mode", s, sizeof s))
        for (int i = 0; i < 4; i++)
            if (!strcmp(s, bf_names[i]))
                c->bf_mode = i;
    double g[NMIC];
    int n = json_get_num_array(js, "mic_gain_db", g, NMIC);
    for (int i = 0; i < n; i++)
        c->mic_gain_db[i] = clampf((float)g[i], -20, 20);
    return reinit;
}

static size_t cfg_json(const cfg_t *c, char *out, size_t n)
{
    size_t o = (size_t)snprintf(out, n, "{");
    for (size_t i = 0; i < sizeof fields / sizeof fields[0]; i++) {
        const cfg_field_t *f = &fields[i];
        const char *p = (const char *)c + f->off;
        if (f->type == T_F)
            o += (size_t)snprintf(out + o, n - o, "\"%s\":%.4g,", f->name, *(const float *)p);
        else if (f->type == T_B)
            o += (size_t)snprintf(out + o, n - o, "\"%s\":%s,", f->name, *(const int *)p ? "true" : "false");
        else
            o += (size_t)snprintf(out + o, n - o, "\"%s\":%d,", f->name, *(const int *)p);
    }
    o += (size_t)snprintf(out + o, n - o, "\"aec_mode\":\"%s\",\"bf_mode\":\"%s\",\"mic_gain_db\":[",
                          aec_names[c->aec_mode], bf_names[c->bf_mode]);
    for (int m = 0; m < NMIC; m++)
        o += (size_t)snprintf(out + o, n - o, "%s%.2g", m ? "," : "", c->mic_gain_db[m]);
    o += (size_t)snprintf(out + o, n - o, "]}");
    return o;
}

/* ------------------------------------------------------------- State --- */

enum { ST_IDLE, ST_LISTENING, ST_THINKING, ST_SPEAKING };
static const char *state_names[] = {"idle", "listening", "thinking", "speaking"};

typedef struct {
    float az[2];
    int n;
    float conf;
    int speech;
    int64_t frame;
} doa_rec_t;

static struct {
    pthread_mutex_t lock;
    volatile int running;
    cfg_t cfg;
    int64_t frame;

    /* state machine */
    int state;
    int64_t state_frame;
    uint32_t wake_id;
    int streaming, speech_seen, eos_sent, followup_listen;
    int64_t listen_start, last_speech, eos_frame;
    int listen_timeout_f;
    int session_end_pending, followup_pending, followup_ms, early_session_end;
    int link_up;

    /* DSP */
    decim_t dec[CAP_CH];
    float dc_x[CAP_CH], dc_y[CAP_CH];
    aec_t *aec;
    beam_t *beam;
    tracker_t trk;
    stft_t stft[NMIC], stft_ref;
    res_t res;
    sbaec_t sb;
    ns_t ns_fix[MAX_BEAMS], ns_trk, ns_ref;
    istft_t ist_fix[MAX_BEAMS], ist_trk, ist_raw;
    agc_t agc;
    doa_t doa;
    int speech, hang, far_only;
    int64_t last_any_speech;
    uint8_t speech_hist[256];       /* VAD per frame, indexed by frame & 255 */
    float vad_prob, snr_db, speech_db, noise_db;

    /* hotword */
    kws_model_t *kws;
    kws_det_t det[MAX_BEAMS];
    float kws_cost[MAX_BEAMS];
    int cand_beam, cand_tpl;
    float cand_cost;
    int64_t cand_frame, refractory_until;
    int wakes, false_wakes;

    /* rings, all aligned on ring_pos */
    int16_t *ring_fix[MAX_BEAMS];
    int16_t *ring_up;
    int ring_pos;
    doa_rec_t doa_hist[DOA_HIST];
    int doa_pos;

    /* enrollment recording */
    int rec_active, rec_len, rec_cap, rec_delay;
    int64_t rec_end_frame;
    /* last keyword wake, kept in case the server rejects it (negative) */
    int16_t last_wake_pcm[SR * 2];
    int last_wake_len, last_wake_playing;
    char last_wake_kw[48];
    char kws_dir[400];
    int16_t *rec_buf;
    char rec_path[400];
    int rec_client;

    /* stats */
    float mic_db[NMIC], ref_db;
    double t_total, t_aec, t_bf, t_kws, t_max;
    int t_frames;
    int xrun_cap, xrun_play;
    int64_t out_latency_ns;
    int monitor;
} E;

static void emit_state(const char *reason)
{
    ipc_json(-1, 0, "{\"event\":\"state\",\"state\":\"%s\",\"reason\":\"%s\",\"muted\":%s,\"wake_id\":%u}",
             state_names[E.state], reason, E.cfg.mic_muted ? "true" : "false", E.wake_id);
}

static void finish_session(int follow_up, int follow_ms);

static void set_state(int s, const char *reason)
{
    if (s == E.state)
        return;
    if (s == ST_THINKING && E.early_session_end) {
        E.early_session_end = 0;
        emit_state(reason);
        finish_session(E.followup_pending, E.followup_ms);
        return;
    }
    if (s == ST_LISTENING)
        E.early_session_end = 0;
    E.state = s;
    E.state_frame = E.frame;
    mixer_set_duck(s == ST_LISTENING ? 2 : s != ST_IDLE ? 1 : 0);
    if (s != ST_SPEAKING && s != ST_THINKING) {
        E.session_end_pending = 0;
    }
    emit_state(reason);
}

static void stop_uplink(const char *reason)
{
    if (!E.streaming)
        return;
    E.streaming = 0;
    ipc_json(-1, 0, "{\"event\":\"uplink_end\",\"wake_id\":%u,\"reason\":\"%s\"}", E.wake_id, reason);
}

/* Sends the last `ms` of a ring as uplink audio, with gain applied. */
static void send_ring(const int16_t *ring, int ms, float gain)
{
    int n = SR * ms / 1000;
    if (n > RING - HOP)
        n = RING - HOP;
    if (n <= 0)
        return;
    int16_t *buf = malloc((size_t)n * 2);
    if (!buf)
        return;
    int start = (E.ring_pos - n + RING) % RING;
    for (int i = 0; i < n; i++) {
        float v = ring[(start + i) % RING] * gain;
        buf[i] = (int16_t)clampf(v, -32768, 32767);
    }
    ipc_send(-1, MSG_UPLINK, SUB_AUDIO, buf, (size_t)n * 2);
    free(buf);
}

/* Direction of the talker who said the hotword: circular mean of the speech
 * DOA peaks of the last ~1.2 s, weighted by confidence (all beams of a small
 * array hear the word; the localization tells where it came from). */
static float wake_direction(int beam)
{
    float sx = 0, sy = 0;
    for (int i = 0; i < DOA_HIST; i++) {
        doa_rec_t *r = &E.doa_hist[i];
        if (!r->speech || r->n == 0 || E.frame - r->frame > MS2F(1200) || r->conf < E.cfg.doa_min_conf)
            continue;
        if (tracker_is_static(&E.trk, r->az[0]) && r->n > 1) {
            float a = r->az[1] * (float)M_PI / 180;
            sx += r->conf * sinf(a);
            sy += r->conf * cosf(a);
        } else {
            float a = r->az[0] * (float)M_PI / 180;
            sx += r->conf * sinf(a);
            sy += r->conf * cosf(a);
        }
    }
    LOGD("wake direction: sx %.3f sy %.3f", sx, sy);
    if (sx * sx + sy * sy < 1e-6f)
        return beam >= 0 ? beam_fixed_az(E.beam, beam) : tracker_angle(&E.trk);
    return wrap360(atan2f(sx, sy) * 180 / (float)M_PI);
}

static int nearest_beam(float az)
{
    int best = 0, nb = beam_nfixed(E.beam);
    for (int b = 1; b < nb; b++)
        if (fabsf(angdiff(az, beam_fixed_az(E.beam, b))) < fabsf(angdiff(az, beam_fixed_az(E.beam, best))))
            best = b;
    return best;
}

static void start_listening(const char *source, const char *keyword, float score, int beam,
                            int preroll_ms, int timeout_ms, int earcon)
{
    if (E.cfg.mic_muted) {
        mixer_earcon(EC_MUTE_ON, 0);
        return;
    }
    float doa = beam >= 0 ? wake_direction(beam) : tracker_angle(&E.trk);
    if (beam >= 0)
        beam = nearest_beam(doa);   /* pre-roll from the beam facing the talker */
    char kw[64];
    json_escape(kw, sizeof kw, keyword);
    if (!E.link_up) {
        /* local fallback: nobody to talk to */
        mixer_earcon(EC_OFFLINE, 0);
        ipc_json(-1, 0, "{\"event\":\"wake\",\"offline\":true,\"source\":\"%s\",\"keyword\":%s,"
                 "\"score\":%.3f,\"doa\":%.1f}", source, kw, score, doa);
        return;
    }
    int barge = E.state == ST_SPEAKING || E.state == ST_THINKING;
    int playing = (mixer_active_kinds() & ((1 << SK_TTS) | (1 << SK_MEDIA) | (1 << SK_ALARM))) != 0;
    int own_voice = (mixer_active_kinds() & (1 << SK_TTS)) != 0;
    if (barge)
        mixer_stop_kinds((1 << SK_TTS) | (1 << SK_EARCON));
    if (strcmp(source, "followup") != 0)
        tracker_lock(&E.trk, doa);
    E.wake_id++;
    E.wakes++;
    E.streaming = 1;
    E.speech_seen = 0;
    E.eos_sent = 0;
    E.listen_start = E.frame;
    E.last_speech = E.frame;
    E.listen_timeout_f = (int)MS2F(timeout_ms);
    E.followup_listen = strcmp(source, "followup") == 0;
    E.session_end_pending = 0;
    E.state = -1;           /* force a state event even if already listening */
    set_state(ST_LISTENING, source);
    if (earcon)
        mixer_earcon(EC_WAKE, 0);
    ipc_json(-1, 0, "{\"event\":\"wake\",\"wake_id\":%u,\"source\":\"%s\",\"keyword\":%s,\"score\":%.3f,"
             "\"doa\":%.1f,\"beam\":%d,\"snr_db\":%.1f,\"ts\":%lld,\"preroll_ms\":%d,\"barge_in\":%s,"
             "\"playing\":%s,\"tts\":%s}",
             E.wake_id, source, kw, score, doa, beam, E.snr_db, (long long)real_ns(), preroll_ms,
             barge ? "true" : "false", playing ? "true" : "false", own_voice ? "true" : "false");
    if (preroll_ms > 0) {
        if (beam >= 0)
            send_ring(E.ring_fix[beam], preroll_ms, agc_gain(&E.agc));
        else
            send_ring(E.ring_up, preroll_ms, 1.0f);
    }
}

static void finish_session(int follow_up, int follow_ms)
{
    E.session_end_pending = 0;
    if (follow_up && !E.cfg.mic_muted)
        start_listening("followup", "", 1.0f, -1, 0, follow_ms > 0 ? follow_ms : 6000, 0);
    else
        set_state(ST_IDLE, "session_end");
}

/* ------------------------------------------------------ Frame process --- */

static void rebuild(int what)
{
    if (what & 1) {
        aec_destroy(E.aec);
        E.aec = aec_create(E.cfg.aec_tail_ms);
        sbaec_init(&E.sb, E.cfg.aec_tail_ms);
        res_init(&E.res);
    }
    if (what & 2) {
        beam_destroy(E.beam);
        E.beam = beam_create(E.cfg.fixed_beams, E.cfg.bf_loading);
        for (int b = 0; b < MAX_BEAMS; b++) {
            ns_init(&E.ns_fix[b]);
            memset(&E.ist_fix[b], 0, sizeof E.ist_fix[b]);
            kws_det_reset(&E.det[b], E.kws);
        }
    }
}

static void meters(void)
{
    cfg_t *c = &E.cfg;
    char buf[4096];
    size_t o = (size_t)snprintf(buf, sizeof buf, "{\"event\":\"meters\",\"state\":\"%s\",\"mic_db\":[",
                                state_names[E.state]);
    for (int m = 0; m < NMIC; m++)
        o += (size_t)snprintf(buf + o, sizeof buf - o, "%s%.1f", m ? "," : "", E.mic_db[m]);
    o += (size_t)snprintf(buf + o, sizeof buf - o, "],\"ref_db\":%.1f,\"out_db\":%.1f,\"srp\":[",
                          E.ref_db, mixer_out_db());
    for (int a = 0; a < NANG; a += 2)
        o += (size_t)snprintf(buf + o, sizeof buf - o, "%s%.3f", a ? "," : "", E.doa.srp[a]);
    int nb = beam_nfixed(E.beam);
    o += (size_t)snprintf(buf + o, sizeof buf - o,
                          "],\"doa\":%.1f,\"doa_conf\":%.2f,\"track\":%.1f,\"track_valid\":%s,"
                          "\"vad\":%.2f,\"speech\":%s,\"snr_db\":%.1f,\"noise_db\":%.1f,\"agc_db\":%.1f,"
                          "\"erle_db\":%.1f,\"res_db\":%.1f,\"doubletalk\":%s,\"kws\":[",
                          E.doa.npeaks ? E.doa.peak[0].az : 0, E.doa.confidence, tracker_angle(&E.trk),
                          E.trk.valid ? "true" : "false", E.vad_prob, E.speech ? "true" : "false",
                          E.snr_db, E.noise_db, lin_to_db(agc_gain(&E.agc)),
                          c->aec_mode == 0 ? E.sb.erle_db : E.aec ? aec_erle_db(E.aec) : 0, E.res.atten_db, E.res.doubletalk ? "true" : "false");
    for (int b = 0; b < nb; b++)
        o += (size_t)snprintf(buf + o, sizeof buf - o, "%s%.3f", b ? "," : "", fminf(E.kws_cost[b], 9));
    o += (size_t)snprintf(buf + o, sizeof buf - o, "],\"static\":[");
    for (int a = 0; a < NANG; a += 2)
        o += (size_t)snprintf(buf + o, sizeof buf - o, "%s%.2f", a ? "," : "", E.trk.static_map[a]);
    double nf = E.t_frames ? E.t_frames : 1;
    o += (size_t)snprintf(buf + o, sizeof buf - o,
                          "],\"cpu\":{\"frame_us\":%.0f,\"max_us\":%.0f,\"aec_us\":%.0f,\"bf_us\":%.0f,"
                          "\"kws_us\":%.0f,\"budget_us\":%lld},\"xruns\":{\"capture\":%d,\"playback\":%d},"
                          "\"queued_kb\":%zu,\"wakes\":%d}",
                          E.t_total / nf, E.t_max, E.t_aec / nf, E.t_bf / nf, E.t_kws / nf,
                          (long long)(FRAME_NS / 1000), E.xrun_cap, E.xrun_play,
                          mixer_queued_bytes() / 1024, E.wakes);
    ipc_send(-1, MSG_JSON, SUB_METERS, buf, o);
    E.t_total = E.t_aec = E.t_bf = E.t_kws = E.t_max = 0;
    E.t_frames = 0;
}

static void handle_mixer_events(void)
{
    mixer_event_t ev;
    static const char *names[] = {"", "started", "finished", "stopped", "underrun", "overflow"};
    static const char *kinds[] = {"media", "tts", "earcon", "alarm"};
    while (mixer_poll_event(&ev)) {
        if (ev.kind != SK_EARCON)
            ipc_json(-1, 0, "{\"event\":\"stream\",\"id\":%u,\"kind\":\"%s\",\"what\":\"%s\",\"ms\":%lld}",
                     ev.id, kinds[ev.kind], names[ev.event], (long long)(ev.frames * 1000 / OUT_RATE));
        if (ev.kind != SK_TTS)
            continue;
        if (ev.event == MIXEV_STARTED && E.state != ST_LISTENING)
            set_state(ST_SPEAKING, "tts");
        if ((ev.event == MIXEV_FINISHED || ev.event == MIXEV_STOPPED) &&
            !(mixer_active_kinds() & (1 << SK_TTS)) && E.state == ST_SPEAKING) {
            if (E.session_end_pending)
                finish_session(E.followup_pending, E.followup_ms);
            else
                set_state(ST_THINKING, "tts_done");
        }
    }
}

static void process_frame(float cap[CAP_CH][HOP])
{
    int64_t t0 = mono_ns(), t1, t2, t3;
    cfg_t *c = &E.cfg;
    float mic[NMIC][HOP], ref[HOP];
    E.frame++;

    /* DC removal, gains, levels */
    float g0 = db_to_lin(c->capture_gain_db);
    double eref = 0;
    for (int ch = 0; ch < CAP_CH; ch++) {
        float x1 = E.dc_x[ch], y1 = E.dc_y[ch];
        float g = ch < NMIC ? g0 * db_to_lin(c->mic_gain_db[ch]) : 1.0f;
        double e = 0;
        for (int n = 0; n < HOP; n++) {
            float x = cap[ch][n];
            float y = x - x1 + 0.985f * y1;
            x1 = x;
            y1 = y;
            cap[ch][n] = y * g;
            e += (double)cap[ch][n] * cap[ch][n];
        }
        E.dc_x[ch] = x1;
        E.dc_y[ch] = y1;
        if (ch < NMIC)
            E.mic_db[ch] = 0.7f * E.mic_db[ch] + 0.3f * pow_to_db((float)(e / HOP));
    }
    for (int n = 0; n < HOP; n++) {
        ref[n] = 0.5f * (cap[REF_L][n] + cap[REF_R][n]);
        eref += (double)ref[n] * ref[n];
        for (int m = 0; m < NMIC; m++)
            mic[m][n] = c->mic_muted ? 0 : cap[m][n];
    }
    E.ref_db = 0.7f * E.ref_db + 0.3f * pow_to_db((float)(eref / HOP));

    /* echo cancellation (full-band speexdsp variant; the default works on
     * the spectra below) */
    if (c->aec_enabled && c->aec_mode == 1 && E.aec)
        aec_process(E.aec, mic, ref, E.speech);
    t1 = mono_ns();

    /* spectra, localization */
    static cfloat X[NMIC][NBIN];
    for (int m = 0; m < NMIC; m++)
        stft_push(&E.stft[m], mic[m], X[m]);
    /* residual (non-linear) echo suppression, same gain on every mic */
    {
        cfloat Rs[NBIN];
        stft_push(&E.stft_ref, ref, Rs);
        if (c->aec_enabled && c->aec_mode == 0)
            sbaec_process(&E.sb, Rs, X, E.speech, c->aec_mu);
        if (c->res_enabled && c->aec_enabled)
            res_process(&E.res, Rs, X, E.speech, c->res_strength, c->res_floor_db);
        /* the device is playing and nobody talks over it: what the mics
         * localize now is our own speaker */
        E.far_only = c->aec_enabled && E.res.ptot_s >= 1e-4f * NFFT && E.res.frames > 120 && !E.res.doubletalk;
    }
    /* speech-presence weights from an omni reference: stationary noise
     * (fan, hum, constant interferers) is excluded from the DOA of speech */
    ns_process(&E.ns_ref, X[0], NULL, -40, c->vad_threshold_db);
    if ((E.frame & 1) == 0) {
        int near = E.speech && !E.far_only;
        beam_doa(E.beam, X, near ? E.ns_ref.G : NULL, &E.doa);
        doa_rec_t *r = &E.doa_hist[E.doa_pos];
        E.doa_pos = (E.doa_pos + 1) % DOA_HIST;
        r->n = E.doa.npeaks;
        for (int p = 0; p < r->n; p++)
            r->az[p] = E.doa.peak[p].az;
        r->conf = E.doa.confidence;
        r->speech = near;
        r->frame = E.frame;
        tracker_update(&E.trk, &E.doa, near, E.far_only || E.frame - E.last_any_speech > MS2F(500),
                       c->doa_min_conf);
    }
    tracker_predict(&E.trk, (float)HOP / SR);

    /* fixed beams: noise suppression, VAD, pre-roll, hotword features */
    const int nb = beam_nfixed(E.beam);
    const float floor_db = c->ns_enabled ? c->ns_floor_db : 0;
    const int kws_on = c->kws_enabled && E.kws && E.kws->npos > 0 && !c->mic_muted &&
                       !E.rec_active && E.frame > E.rec_end_frame + MS2F(1000) &&
                       E.state != ST_LISTENING &&
                       (E.state == ST_IDLE || c->kws_barge_in);
    float pmax = 0, snr = -99;
    static float feats[MAX_BEAMS][KWS_NFEAT];
    for (int b = 0; b < nb; b++) {
        cfloat Y[NBIN];
        float hop[HOP];
        beam_fixed(E.beam, b, X, Y);
        ns_process(&E.ns_fix[b], Y, Y, floor_db, c->vad_threshold_db);
        if (E.ns_fix[b].prob > pmax)
            pmax = E.ns_fix[b].prob;
        if (E.ns_fix[b].snr_db > snr)
            snr = E.ns_fix[b].snr_db;
        if (kws_on)
            kws_features(Y, feats[b]);
        istft_push(&E.ist_fix[b], Y, hop);
        for (int n = 0; n < HOP; n++)
            E.ring_fix[b][(E.ring_pos + n) % RING] = (int16_t)clampf(hop[n] * 32767.0f, -32768, 32767);
    }
    E.vad_prob = pmax;
    E.snr_db = snr;
    /* voiced frames for the hotword: bit 0 any voice, bit 1 not our own
     * playback alone */
    E.speech_hist[E.frame & 255] = (pmax > 0.5f) | ((pmax > 0.5f && !E.far_only) << 1);
    if (pmax > 0.5f) {
        E.speech = 1;
        E.last_any_speech = E.frame;
        E.hang = 19;            /* 300 ms hangover: short pauses are not silence */
    } else if (E.hang > 0) {
        E.hang--;
    } else {
        E.speech = 0;
    }

    /* tracked beam: MVDR noise statistics from frames without the target:
     * no speech at all, or speech coming from elsewhere (interferer). */
    float track = tracker_angle(&E.trk);
    /* MVDR noise statistics only from frames without any speech: learning
     * from "speech elsewhere" cancels the talker when the track is wrong */
    int noise_frame = !E.speech && E.frame - E.last_any_speech > MS2F(300);
    cfloat Yt[NBIN];
    float up[HOP];
    beam_tracked(E.beam, (enum bf_mode)c->bf_mode, track, X, noise_frame, Yt);
    if (E.rec_active) {
        float raw[HOP];
        istft_push(&E.ist_raw, Yt, raw);
        if (E.rec_delay > 0) {
            E.rec_delay--;
        } else {
            for (int n = 0; n < HOP && E.rec_len < E.rec_cap; n++)
                E.rec_buf[E.rec_len++] = (int16_t)clampf(raw[n] * 32767.0f, -32768, 32767);
        }
    }
    ns_process(&E.ns_trk, Yt, Yt, floor_db, c->vad_threshold_db);
    if (E.speech)
        E.speech_db = 0.95f * E.speech_db + 0.05f * (E.noise_db + E.ns_trk.snr_db);
    {
        double pn = 0;
        for (int k = 10; k <= 128; k++)
            pn += E.ns_trk.N[k];
        E.noise_db = pow_to_db((float)(pn / 119 / (NFFT * 0.5)));
    }
    istft_push(&E.ist_trk, Yt, up);
    if (E.monitor) {
        int16_t mon[HOP];
        for (int n = 0; n < HOP; n++)
            mon[n] = (int16_t)clampf(up[n] * 32767.0f, -32768, 32767);
        ipc_send(-1, MSG_MONITOR, SUB_MONITOR, mon, sizeof mon);
    }
    if (c->agc_enabled)
        agc_process(&E.agc, up, HOP, E.speech, c->agc_target_db, c->agc_max_gain_db);
    int16_t up16[HOP];
    for (int n = 0; n < HOP; n++) {
        up16[n] = (int16_t)clampf(up[n] * 32767.0f, -32768, 32767);
        E.ring_up[(E.ring_pos + n) % RING] = up16[n];
    }
    E.ring_pos = (E.ring_pos + HOP) % RING;
    t2 = mono_ns();

    /* hotword */
    if (kws_on && E.frame >= E.refractory_until) {
        float thr = c->kws_threshold;
        const int kinds = mixer_active_kinds();
        const int own_voice = (kinds & (1 << SK_TTS)) != 0;
        int playing = (kinds & ((1 << SK_MEDIA) | (1 << SK_ALARM))) != 0 && !own_voice;
        if (playing)                /* music's echo residue raises the costs: be more lenient */
            thr += c->kws_playback_margin;
        else if (own_voice && thr > c->kws_tts_threshold)
            /* its own answer playing: the residue is a voice, the one thing the
             * templates can match; never lenient then (it would wake itself) */
            thr = c->kws_tts_threshold;
        int best = -1;
        for (int b = 0; b < nb; b++) {
            E.kws_cost[b] = kws_det_push(&E.det[b], E.kws, feats[b], E.speech, thr);
            if (best < 0 || E.kws_cost[b] < E.kws_cost[best])
                best = b;
        }
        /* the match must cover speech, not noise that happens to fit */
        /* while playing, the echo residue makes the near/far decision
         * unreliable for a distant voice: any voice counts, with less of it
         * required; the server's verifier and the learned negatives take
         * care of the extra false wakes */
        int voiced = 0, span = best >= 0 ? E.det[best].last_span : 0;
        const int bit = playing ? 1 : 2, need = playing ? 4 : 6;
        for (int k = 0; k < span && k < 256; k++)
            voiced += (E.speech_hist[(E.frame - k) & 255] & bit) != 0;
        if (best >= 0 && E.kws_cost[best] < thr + 0.08f && g_verbose > 1 && E.frame % 4 == 0)
            LOGD("kws near: t=%.2fs cost %.3f voiced %d/%d matches %d far_only %d dt %d", E.frame * HOP / (float)SR,
                 E.kws_cost[best], voiced, span, E.det[best].last_matches, E.far_only, E.res.doubletalk);
        int minm = c->kws_min_matches;
        if (minm == 0)
            minm = E.kws->npos >= 3 && !playing ? 2 : 1;
        int beats_neg = best >= 0 && E.det[best].last_neg >= E.kws_cost[best] + c->kws_neg_margin;
        if (best >= 0 && E.kws_cost[best] < thr && E.det[best].last_matches >= minm && beats_neg &&
            voiced * 10 >= span * need && E.frame > MS2F(1000)) {
            if (E.cand_beam < 0 || E.kws_cost[best] < E.cand_cost) {
                if (E.cand_beam < 0)
                    E.cand_frame = E.frame;
                E.cand_beam = best;
                E.cand_cost = E.kws_cost[best];
                E.cand_tpl = E.det[best].last_tpl;
            }
        }
        /* fire once the cost stopped improving for a few frames */
        if (E.cand_beam >= 0 && E.frame - E.cand_frame >= 4) {
            int tpl = E.cand_tpl;
            const char *kw = tpl >= 0 ? E.kws->tpl[tpl].keyword : "";
            float score = clampf(1.0f - E.cand_cost / (2 * c->kws_threshold), 0, 1);
            LOGI("wake: '%s' beam %d cost %.3f at %.2fs (speech %d vad %.2f)", kw, E.cand_beam, E.cand_cost, E.frame * HOP / (float)SR, E.speech, E.vad_prob);
            /* keep the matched audio: a server rejection makes it a negative */
            {
                int n = (E.det[E.cand_beam].last_span + 24) * HOP;
                if (n > SR * 2)
                    n = SR * 2;
                int start = (E.ring_pos - n + RING) % RING;
                for (int i = 0; i < n; i++)
                    E.last_wake_pcm[i] = E.ring_fix[E.cand_beam][(start + i) % RING];
                E.last_wake_len = n;
                E.last_wake_playing = playing;
                snprintf(E.last_wake_kw, sizeof E.last_wake_kw, "%s", kw);
            }
            start_listening("kws", kw, score, E.cand_beam, c->preroll_ms, c->listen_timeout_ms,
                            c->earcon_wake);
            E.cand_beam = -1;
            E.refractory_until = E.frame + MS2F(c->kws_refractory_ms);
            for (int b = 0; b < nb; b++)
                kws_det_reset(&E.det[b], E.kws);
        }
    } else if (!kws_on) {
        E.cand_beam = -1;
        for (int b = 0; b < nb; b++)
            E.kws_cost[b] = 9;
    }
    t3 = mono_ns();

    /* state machine timers */
    if (E.state == ST_LISTENING) {
        if (E.streaming)
            ipc_send(-1, MSG_UPLINK, SUB_AUDIO, up16, sizeof up16);
        if (E.speech) {
            E.speech_seen = 1;
            E.last_speech = E.frame;
        }
        int64_t el = E.frame - E.listen_start;
        if (!E.eos_sent) {
            const char *why = NULL;
            if (E.speech_seen && E.frame - E.last_speech > MS2F(c->eos_silence_ms))
                why = "silence";
            else if (!E.speech_seen && el > E.listen_timeout_f)
                why = "no_speech";
            else if (el > MS2F(c->max_listen_ms))
                why = "max";
            if (why) {
                E.eos_sent = 1;
                E.eos_frame = E.frame;
                ipc_json(-1, 0, "{\"event\":\"eos\",\"wake_id\":%u,\"reason\":\"%s\",\"speech\":%s}",
                         E.wake_id, why, E.speech_seen ? "true" : "false");
            }
        } else if (E.frame - E.eos_frame > MS2F(c->eos_grace_ms)) {
            /* the server did not answer the end of speech: decide locally */
            stop_uplink("local_eos");
            if (!E.speech_seen)
                set_state(ST_IDLE, "no_speech");
            else
                set_state(ST_THINKING, "local_eos");
        }
    } else if (E.state == ST_THINKING) {
        if (E.frame - E.state_frame > MS2F(c->think_timeout_ms)) {
            mixer_earcon(EC_ERROR, 0);
            set_state(ST_IDLE, "timeout");
        }
    }
    handle_mixer_events();

    if (E.rec_active && E.rec_len >= E.rec_cap) {
        int ok = wav_write(E.rec_path, E.rec_buf, E.rec_len) == 0;
        char p[440];
        json_escape(p, sizeof p, E.rec_path);
        ipc_json(-1, 0, "{\"event\":\"record_done\",\"path\":%s,\"ok\":%s,\"ms\":%d}", p,
                 ok ? "true" : "false", E.rec_len * 1000 / SR);
        free(E.rec_buf);
        E.rec_buf = NULL;
        E.rec_active = 0;
        E.rec_end_frame = E.frame;
    }

    int64_t t4 = mono_ns();
    double us = (t4 - t0) / 1000.0;
    E.t_total += us;
    E.t_aec += (t1 - t0) / 1000.0;
    E.t_bf += (t2 - t1) / 1000.0;
    E.t_kws += (t3 - t2) / 1000.0;
    if (us > E.t_max)
        E.t_max = us;
    E.t_frames++;
    if (E.frame % (FPS / (c->meters_hz > 0 ? c->meters_hz : 10) + 1) == 0)
        meters();
}

/* ---------------------------------------------------------- Commands --- */

static int earcon_by_name(const char *s)
{
    static const char *names[] = {"wake", "end", "error", "alarm", "notify", "mute_on", "mute_off",
                                  "volume", "offline", "done"};
    for (int i = 0; i < EC_COUNT; i++)
        if (!strcmp(s, names[i]))
            return i;
    return -1;
}

static int kind_by_name(const char *s)
{
    if (!strcmp(s, "media"))
        return SK_MEDIA;
    if (!strcmp(s, "tts"))
        return SK_TTS;
    if (!strcmp(s, "alarm") || !strcmp(s, "alert"))
        return SK_ALARM;
    if (!strcmp(s, "earcon"))
        return SK_EARCON;
    return -1;
}

static void send_status(int client)
{
    static char cfgbuf[8192];
    cfg_json(&E.cfg, cfgbuf, sizeof cfgbuf);
    char kw[2048] = "[]";
    if (E.kws) {
        size_t o = (size_t)snprintf(kw, sizeof kw, "[");
        const char *last = "";
        int first = 1;
        for (int i = 0; i < E.kws->ntpl; i++) {
            int seen = 0;
            for (int j = 0; j < i; j++)
                seen |= !strcmp(E.kws->tpl[j].keyword, E.kws->tpl[i].keyword);
            if (seen)
                continue;
            last = E.kws->tpl[i].keyword;
            int count = 0, neg = 0;
            for (int j = 0; j < E.kws->ntpl; j++)
                if (!strcmp(E.kws->tpl[j].keyword, last)) {
                    if (E.kws->tpl[j].negative)
                        neg++;
                    else
                        count++;
                }
            char esc[100];
            json_escape(esc, sizeof esc, last);
            o += (size_t)snprintf(kw + o, sizeof kw - o, "%s{\"name\":%s,\"templates\":%d,\"negatives\":%d}",
                                  first ? "" : ",", esc, count, neg);
            first = 0;
        }
        snprintf(kw + o, sizeof kw - o, "]");
    }
    ipc_json(client, 0, "{\"event\":\"status\",\"version\":\"%s\",\"state\":\"%s\",\"wake_id\":%u,"
             "\"link_up\":%s,\"keywords\":%s,\"aec_tail_ms\":%d,\"fixed_beam_az\":%d,\"config\":%s}",
             SATD_VERSION, state_names[E.state], E.wake_id, E.link_up ? "true" : "false", kw,
             E.aec ? aec_tail_ms(E.aec) : 0, beam_nfixed(E.beam), cfgbuf);
}

static void cmd_json(int client, const char *js)
{
    char cmd[32] = "", s[400];
    double v;
    int b;
    if (!json_get_str(js, "cmd", cmd, sizeof cmd))
        return;
    LOGD("cmd %s", js);

    if (!strcmp(cmd, "subscribe")) {
        int mask = 0;
        if (json_get_bool(js, "audio", &b) && b)
            mask |= SUB_AUDIO;
        if (json_get_bool(js, "meters", &b) && b)
            mask |= SUB_METERS;
        if (json_get_bool(js, "monitor", &b) && b)
            mask |= SUB_MONITOR;
        ipc_subscribe(client, mask);
        pthread_mutex_lock(&E.lock);
        if (mask & SUB_MONITOR)
            E.monitor = 1;
        else if (json_has(js, "monitor"))
            E.monitor = 0;
        pthread_mutex_unlock(&E.lock);
        return;
    }
    if (!strcmp(cmd, "kws_negative")) {
        /* the server rejected the last wake: keep its audio as a negative */
        pthread_mutex_lock(&E.lock);
        /* a rejection during playback says little (the verifier hears the
         * music too): never learn from those */
        int n = E.last_wake_playing ? 0 : E.last_wake_len;
        char kw[48], dir[400];
        static int16_t pcm[SR * 2];
        memcpy(pcm, E.last_wake_pcm, (size_t)n * 2);
        snprintf(kw, sizeof kw, "%s", E.last_wake_kw);
        snprintf(dir, sizeof dir, "%s", E.kws_dir);
        E.last_wake_len = 0;
        pthread_mutex_unlock(&E.lock);
        if (n <= 0 || !kw[0] || !dir[0])
            return;
        char path[600];
        snprintf(path, sizeof path, "%s/%s/negatives", dir, kw);
        mkdir(path, 0775);
        snprintf(path, sizeof path, "%s/%s/negatives/neg_%lld.wav", dir, kw, (long long)(real_ns() / 1000000));
        int ok = wav_write(path, pcm, n) == 0;
        char esc[700];
        json_escape(esc, sizeof esc, path);
        ipc_json(-1, 0, "{\"event\":\"kws_negative\",\"path\":%s,\"ok\":%s,\"keyword\":\"%s\"}", esc,
                 ok ? "true" : "false", kw);
        snprintf(s, sizeof s, "%s", dir);
        goto load;
    }
    if (!strcmp(cmd, "kws_check")) {
        if (!json_get_str(js, "path", s, sizeof s))
            return;
        float cost;
        int a, b2;
        pthread_mutex_lock(&E.lock);
        int ok = E.kws ? kws_check_wav(E.kws, s, &cost, &a, &b2) : -1;
        pthread_mutex_unlock(&E.lock);
        char esc[500];
        json_escape(esc, sizeof esc, s);
        ipc_json(client, 0, "{\"event\":\"kws_check\",\"path\":%s,\"usable\":%s,\"cost\":%.3f,"
                 "\"start_ms\":%d,\"end_ms\":%d,\"threshold\":%.3f}", esc, ok == 1 ? "true" : "false",
                 cost, a, b2, E.cfg.kws_threshold);
        return;
    }
    if (!strcmp(cmd, "kws_load")) {
        /* loads outside the lock, then swaps */
        if (!json_get_str(js, "dir", s, sizeof s))
            return;
    load:;
        pthread_mutex_lock(&E.lock);
        snprintf(E.kws_dir, sizeof E.kws_dir, "%s", s);
        pthread_mutex_unlock(&E.lock);
        kws_model_t *m = calloc(1, sizeof *m);
        if (!m)
            return;
        char info[4096];
        int n = kws_load(m, s, info, sizeof info);
        float loo = 0, sug = kws_suggest_threshold(m, &loo);
        pthread_mutex_lock(&E.lock);
        kws_model_t *old = E.kws;
        E.kws = m;
        for (int i = 0; i < MAX_BEAMS; i++) {
            E.det[i].t = 0;
            kws_det_reset(&E.det[i], m);
        }
        pthread_mutex_unlock(&E.lock);
        free(old);
        ipc_json(-1, 0, "{\"event\":\"kws_loaded\",\"templates\":%d,\"keywords\":%s,"
                 "\"suggested_threshold\":%.3f,\"spread\":%.3f}", n, info, sug, loo);
        return;
    }

    pthread_mutex_lock(&E.lock);
    if (!strcmp(cmd, "config")) {
        int was_muted = E.cfg.mic_muted;
        int what = cfg_apply(&E.cfg, js);
        mixer_set_cfg(&E.cfg.mix);
        if (what)
            rebuild(what);
        if (E.cfg.mic_muted != was_muted) {
            if (E.cfg.mic_muted) {
                stop_uplink("muted");
                if (E.state == ST_LISTENING)
                    set_state(ST_IDLE, "muted");
            }
            if (!json_has(js, "quiet"))
                mixer_earcon(E.cfg.mic_muted ? EC_MUTE_ON : EC_MUTE_OFF, 0);
            emit_state(E.cfg.mic_muted ? "muted" : "unmuted");
        }
        pthread_mutex_unlock(&E.lock);
        send_status(-1);
        return;
    } else if (!strcmp(cmd, "status")) {
        pthread_mutex_unlock(&E.lock);
        send_status(client);
        return;
    } else if (!strcmp(cmd, "wake")) {
        char src[24] = "button";
        json_get_str(js, "source", src, sizeof src);
        start_listening(src, "", 1.0f, -1, E.cfg.button_preroll_ms, E.cfg.listen_timeout_ms,
                        E.cfg.earcon_wake);
    } else if (!strcmp(cmd, "listen")) {
        int ms = json_get_num(js, "timeout_ms", &v) ? (int)v : 6000;
        int ec = json_get_bool(js, "earcon", &b) ? b : 0;
        start_listening("followup", "", 1.0f, -1, 0, ms, ec);
    } else if (!strcmp(cmd, "eot")) {
        if (E.state == ST_LISTENING) {
            stop_uplink("eot");
            set_state(ST_THINKING, "eot");
            if (E.cfg.earcon_end && !E.followup_listen)
                mixer_earcon(EC_END, 0);
        }
    } else if (!strcmp(cmd, "cancel")) {
        char why[32] = "cancel";
        json_get_str(js, "reason", why, sizeof why);
        stop_uplink(why);
        if (!strcmp(why, "rejected") || !strcmp(why, "arbitration"))
            E.false_wakes += !strcmp(why, "rejected");
        if (E.state != ST_IDLE) {
            if (E.state == ST_SPEAKING)
                mixer_stop_kinds(1 << SK_TTS);
            set_state(ST_IDLE, why);
        }
    } else if (!strcmp(cmd, "session_end")) {
        int fu = json_get_bool(js, "follow_up", &b) ? b : 0;
        int ms = json_get_num(js, "follow_up_ms", &v) ? (int)v : 6000;
        if (mixer_active_kinds() & (1 << SK_TTS)) {
            E.session_end_pending = 1;
            E.followup_pending = fu;
            E.followup_ms = ms;
        } else if (E.state != ST_LISTENING) {
            finish_session(fu, ms);
        } else {
            /* the answer overtook the end of turn: apply it when listening ends */
            E.early_session_end = 1;
            E.followup_pending = fu;
            E.followup_ms = ms;
        }
    } else if (!strcmp(cmd, "stop")) {
        int media = json_get_bool(js, "media", &b) ? b : 1;
        mixer_stop_kinds((1 << SK_TTS) | (1 << SK_ALARM) | (1 << SK_EARCON) | (media ? 1 << SK_MEDIA : 0));
        if (E.state == ST_SPEAKING)
            set_state(ST_IDLE, "stop");
    } else if (!strcmp(cmd, "stream_open")) {
        char kind[16] = "tts";
        json_get_str(js, "kind", kind, sizeof kind);
        double id = 0, rate = 24000, ch = 1, at = 0, gain = 0, pre = -1;
        json_get_num(js, "id", &id);
        json_get_num(js, "rate", &rate);
        json_get_num(js, "channels", &ch);
        json_get_num(js, "start_at_ns", &at);
        json_get_num(js, "gain_db", &gain);
        json_get_num(js, "prebuffer_ms", &pre);
        int k = kind_by_name(kind);
        if (pre < 0)
            pre = k == SK_MEDIA ? 1000 : 120;
        if (k < 0 || mixer_open((uint32_t)id, k, (int)rate, (int)ch, (int64_t)at, (float)gain, (int)pre) < 0)
            ipc_json(client, 0, "{\"event\":\"error\",\"what\":\"stream_open\",\"id\":%u}", (uint32_t)id);
    } else if (!strcmp(cmd, "stream_close")) {
        double id = 0;
        int drain = 1;
        json_get_num(js, "id", &id);
        json_get_bool(js, "drain", &drain);
        mixer_close((uint32_t)id, drain);
    } else if (!strcmp(cmd, "stream_pause")) {
        double id = 0;
        int p = 1;
        json_get_num(js, "id", &id);
        json_get_bool(js, "paused", &p);
        mixer_pause((uint32_t)id, p);
    } else if (!strcmp(cmd, "earcon")) {
        int loop = 0;
        json_get_bool(js, "loop", &loop);
        if (json_get_str(js, "name", s, sizeof s))
            mixer_earcon(earcon_by_name(s), loop);
    } else if (!strcmp(cmd, "link")) {
        if (json_get_bool(js, "up", &b)) {
            int was = E.link_up;
            E.link_up = b;
            if (!b && E.state != ST_IDLE) {
                stop_uplink("link_down");
                mixer_stop_kinds(1 << SK_TTS);
                set_state(ST_IDLE, "link_down");
            }
            if (was != b)
                ipc_json(-1, 0, "{\"event\":\"link\",\"up\":%s}", b ? "true" : "false");
        }
    } else if (!strcmp(cmd, "record")) {
        double ms = 3000;
        json_get_num(js, "ms", &ms);
        if (json_get_str(js, "path", s, sizeof s) && !E.rec_active) {
            E.rec_cap = (int)(clampf((float)ms, 500, 10000) * SR / 1000);
            E.rec_buf = malloc((size_t)E.rec_cap * 2);
            if (E.rec_buf) {
                snprintf(E.rec_path, sizeof E.rec_path, "%s", s);
                E.rec_len = 0;
                E.rec_delay = (int)MS2F(450);
                E.rec_active = 1;
                memset(&E.ist_raw, 0, sizeof E.ist_raw);
                mixer_earcon(EC_WAKE, 0);
            }
        }
    } else if (!strcmp(cmd, "reset_noise")) {
        beam_reset_noise(E.beam);
        memset(E.trk.static_map, 0, sizeof E.trk.static_map);
        if (E.aec)
            aec_reset(E.aec);
        sbaec_init(&E.sb, E.cfg.aec_tail_ms);
        res_init(&E.res);
    } else if (!strcmp(cmd, "quit")) {
        E.running = 0;
    }
    pthread_mutex_unlock(&E.lock);
}

static void on_ipc(int client, int type, const uint8_t *data, size_t len)
{
    if (type == 0) {
        ipc_json(client, 0, "{\"event\":\"hello\",\"version\":\"%s\",\"rate\":%d,\"hop\":%d}",
                 SATD_VERSION, SR, HOP);
        send_status(client);
    } else if (type == MSG_JSON) {
        cmd_json(client, (const char *)data);
    } else if (type == MSG_DOWNLINK && len >= 4) {
        uint32_t id;
        memcpy(&id, data, 4);
        size_t n = (len - 4) / 2;
        int16_t *pcm = malloc(n * 2 + 2);
        if (pcm) {
            memcpy(pcm, data + 4, n * 2);
            mixer_write(id, pcm, (int)n);
            free(pcm);
        }
    }
}

/* ------------------------------------------------------------- Audio --- */

static struct {
    const char *capture_dev, *playback_dev, *input_file, *sock;
    int fast, no_playback, loop;
} opt = {"mics", "speaker", NULL, "/run/satellite/engine.sock", 0, 0, 0};

static void set_rt(int prio)
{
    struct sched_param sp = {.sched_priority = prio};
    if (pthread_setschedparam(pthread_self(), SCHED_FIFO, &sp) != 0)
        LOGD("no real-time priority (prio %d)", prio);
}

static void *capture_thread(void *arg)
{
    (void)arg;
    const int nf = HOP * DECIM;
    int32_t *raw32 = malloc((size_t)nf * CAP_CH * 4);
    int16_t *raw16 = malloc((size_t)nf * CAP_CH * 2);
    float *fl = malloc((size_t)nf * CAP_CH * sizeof(float));
    static float cap[CAP_CH][HOP];
    snd_pcm_t *pcm = NULL;
    FILE *in = NULL;
    set_rt(60);

    if (opt.input_file) {
        in = fopen(opt.input_file, "rb");
        if (!in) {
            LOGE("cannot open %s", opt.input_file);
            E.running = 0;
            return NULL;
        }
    } else {
        int err = snd_pcm_open(&pcm, opt.capture_dev, SND_PCM_STREAM_CAPTURE, 0);
        if (err >= 0)
            err = snd_pcm_set_params(pcm, SND_PCM_FORMAT_S32_LE, SND_PCM_ACCESS_RW_INTERLEAVED, CAP_CH,
                                     CAP_RATE, 1, 128000);
        if (err < 0) {
            LOGE("capture %s: %s", opt.capture_dev, snd_strerror(err));
            E.running = 0;
            return NULL;
        }
    }
    int64_t next = mono_ns();
    while (E.running) {
        if (in) {
            size_t got = fread(raw16, (size_t)CAP_CH * 2, (size_t)nf, in);
            if (got < (size_t)nf) {
                if (opt.loop) {
                    rewind(in);
                    continue;
                }
                LOGI("end of input file");
                E.running = 0;
                break;
            }
            for (int i = 0; i < nf * CAP_CH; i++)
                fl[i] = raw16[i] * (1.0f / 32768.0f);
            if (!opt.fast) {
                next += FRAME_NS;
                int64_t d = next - mono_ns();
                if (d > 0) {
                    struct timespec ts = {d / 1000000000LL, d % 1000000000LL};
                    nanosleep(&ts, NULL);
                }
            }
        } else {
            snd_pcm_sframes_t r = snd_pcm_readi(pcm, raw32, (snd_pcm_uframes_t)nf);
            if (r < 0) {
                E.xrun_cap++;
                LOGD("capture xrun: %s", snd_strerror((int)r));
                if (snd_pcm_recover(pcm, (int)r, 1) < 0) {
                    LOGE("capture failed: %s", snd_strerror((int)r));
                    usleep(100000);
                }
                continue;
            }
            if (r < nf)
                continue;
            for (int i = 0; i < nf * CAP_CH; i++)
                fl[i] = raw32[i] * (1.0f / 2147483648.0f);
        }
        for (int ch = 0; ch < CAP_CH; ch++)
            decim_process(&E.dec[ch], fl + ch, CAP_CH, cap[ch], HOP);
        pthread_mutex_lock(&E.lock);
        process_frame(cap);
        pthread_mutex_unlock(&E.lock);
    }
    if (pcm)
        snd_pcm_close(pcm);
    if (in)
        fclose(in);
    free(raw32);
    free(raw16);
    free(fl);
    return NULL;
}

static void *playback_thread(void *arg)
{
    (void)arg;
    int16_t buf[OUT_PERIOD * OUT_CH];
    snd_pcm_t *pcm = NULL;
    set_rt(55);
    if (!opt.no_playback) {
        int err = snd_pcm_open(&pcm, opt.playback_dev, SND_PCM_STREAM_PLAYBACK, 0);
        if (err >= 0)
            err = snd_pcm_set_params(pcm, SND_PCM_FORMAT_S16_LE, SND_PCM_ACCESS_RW_INTERLEAVED, OUT_CH,
                                     OUT_RATE, 1, 80000);
        if (err < 0) {
            LOGE("playback %s: %s (continuing without output)", opt.playback_dev, snd_strerror(err));
            pcm = NULL;
        }
    }
    int64_t next = mono_ns();
    while (E.running) {
        snd_pcm_sframes_t delay = 0;
        if (pcm && snd_pcm_delay(pcm, &delay) < 0)
            delay = 0;
        int64_t lat = (int64_t)delay * 1000000000LL / OUT_RATE;
        E.out_latency_ns = lat;
        mixer_render(buf, lat);
        if (pcm) {
            snd_pcm_sframes_t w = snd_pcm_writei(pcm, buf, OUT_PERIOD);
            if (w < 0) {
                E.xrun_play++;
                if (snd_pcm_recover(pcm, (int)w, 1) < 0) {
                    LOGE("playback failed: %s", snd_strerror((int)w));
                    usleep(100000);
                }
            }
        } else {
            next += 1000000000LL * OUT_PERIOD / OUT_RATE;
            int64_t d = next - mono_ns();
            if (d > 0) {
                struct timespec ts = {d / 1000000000LL, d % 1000000000LL};
                nanosleep(&ts, NULL);
            }
        }
    }
    if (pcm) {
        snd_pcm_drop(pcm);
        snd_pcm_close(pcm);
    }
    return NULL;
}

static void on_signal(int sig)
{
    (void)sig;
    E.running = 0;
}

static void usage(void)
{
    fprintf(stderr,
            "satd %s - satellite audio engine\n"
            "  -c DEV     capture device (default mics: 8 ch, see /etc/asound.conf)\n"
            "  -p DEV     playback device (default speaker)\n"
            "  -s PATH    control socket (default /run/satellite/engine.sock)\n"
            "  -i FILE    read capture from a raw file (s16le, 8 ch, 48 kHz) instead of ALSA\n"
            "  -f         with -i: as fast as possible      -l  with -i: loop\n"
            "  -n         no playback device\n"
            "  -k DIR     wake word directory to load at start\n"
            "  -v / -q    more / less logging\n",
            SATD_VERSION);
}

int main(int argc, char **argv)
{
    const char *kws_dir = NULL;
    int c;
    while ((c = getopt(argc, argv, "c:p:s:i:flnk:vqh")) != -1) {
        switch (c) {
        case 'c': opt.capture_dev = optarg; break;
        case 'p': opt.playback_dev = optarg; break;
        case 's': opt.sock = optarg; break;
        case 'i': opt.input_file = optarg; break;
        case 'f': opt.fast = 1; break;
        case 'l': opt.loop = 1; break;
        case 'n': opt.no_playback = 1; break;
        case 'k': kws_dir = optarg; break;
        case 'v': g_verbose++; break;
        case 'q': g_verbose = 0; break;
        default: usage(); return c == 'h' ? 0 : 2;
        }
    }
    setvbuf(stderr, NULL, _IOLBF, 0);
    fft_init();
    resample_init();
    kws_init();
    mixer_init();

    pthread_mutex_init(&E.lock, NULL);
    cfg_defaults(&E.cfg);
    E.aec = aec_create(E.cfg.aec_tail_ms);
    E.beam = beam_create(E.cfg.fixed_beams, E.cfg.bf_loading);
    sbaec_init(&E.sb, E.cfg.aec_tail_ms);
    if (!E.aec || !E.beam) {
        LOGE("out of memory");
        return 1;
    }
    tracker_init(&E.trk, E.cfg.tracker_gate_deg);
    for (int b = 0; b < MAX_BEAMS; b++) {
        ns_init(&E.ns_fix[b]);
        E.ring_fix[b] = calloc(RING, 2);
        E.kws_cost[b] = 9;
    }
    ns_init(&E.ns_trk);
    ns_init(&E.ns_ref);
    res_init(&E.res);
    agc_init(&E.agc);
    E.ring_up = calloc(RING, 2);
    E.cand_beam = -1;
    E.noise_db = -90;
    E.speech_db = -40;

    if (kws_dir) {
        E.kws = calloc(1, sizeof *E.kws);
        char info[4096];
        LOGI("kws: %d templates %s", kws_load(E.kws, kws_dir, info, sizeof info), info);
    }

    char dir[256];
    snprintf(dir, sizeof dir, "%s", opt.sock);
    char *slash = strrchr(dir, '/');
    if (slash) {
        *slash = 0;
        mkdir(dir, 0775);
    }
    if (ipc_listen(opt.sock, on_ipc) < 0)
        return 1;

    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);
    signal(SIGPIPE, SIG_IGN);
    E.running = 1;
    pthread_t tc, tp;
    pthread_create(&tp, NULL, playback_thread, NULL);
    pthread_create(&tc, NULL, capture_thread, NULL);
    LOGI("satd %s: capture %s, playback %s, socket %s", SATD_VERSION,
         opt.input_file ? opt.input_file : opt.capture_dev, opt.no_playback ? "none" : opt.playback_dev,
         opt.sock);
    while (E.running)
        ipc_poll(50);
    pthread_join(tc, NULL);
    pthread_join(tp, NULL);
    ipc_close();
    LOGI("satd stopped");
    return 0;
}
