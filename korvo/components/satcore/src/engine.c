/*
 * Device state machine (idle -> listening -> thinking -> speaking) and the
 * wake word decision, ported from the ReSpeaker satellite engine
 * (satellite/engine/src/engine.c) with the agent's relaying folded in
 * (satellite/agent/satagent/agent.py): here the engine talks to the server
 * directly through sat_send_text() / sat_send_audio().
 *
 * Per 16 ms hop of the clean AFE output:
 *   STFT -> gentle noise suppression (estimates + SNR, as the satellite)
 *   -> MFCC -> DTW detector (one instance) -> candidate / peak picking
 *   -> pre-roll ring, AGC -> uplink while listening, end-of-speech timers.
 */
#include "engine.h"

#include <stdarg.h>
#include <stdlib.h>
#include <string.h>

#include "fft.h"
#include "mixer.h"
#include "ns.h"

#define FPS        (SR / HOP)                    /* 62.5 -> 62 */
#define MS2F(ms)   ((int64_t)(ms) * SR / HOP / 1000)
#define UP_FRAME   512                           /* uplink frame: 32 ms */

/* pre-roll ring length in samples: 2.5 s (less without PSRAM) */
static int RING = SR * 5 / 2;

void engine_set_ring_ms(int ms) { RING = clampf((float)ms, 1000, 2500) * SR / 1000; }

static const char *state_names[] = {"idle", "listening", "thinking", "speaking"};

static struct {
    sat_lock_t *lock;
    engine_cfg_t cfg;
    int64_t frame;
    int mic_muted, alarm_on, notify_on;
    float volume;

    /* state machine */
    int state;
    int64_t state_frame;
    uint32_t wake_id;
    int streaming, speech_seen, eos_sent, followup_listen;
    int64_t listen_start, last_speech, eos_frame;
    int listen_timeout_f;
    int session_end_pending, followup_pending, followup_ms, early_session_end;
    int link;

    /* DSP */
    stft_t stft;
    istft_t istft;              /* the noise-suppressed uplink */
    ns_t ns;
    agc_t agc;
    int speech, hang;
    uint8_t speech_hist[256];       /* VAD per frame, indexed by frame & 255 */
    float snr_db, voice_level;

    /* hotword */
    kws_model_t *kws;
    kws_det_t *det;
    float kws_cost;
    int cand, cand_tpl, cand_span;
    float cand_cost;
    int64_t cand_frame, refractory_until;
    int wakes, false_wakes;

    /* pre-roll ring of the clean stream (before AGC) */
    int16_t *ring;
    int ring_pos;
    /* last keyword wake, kept in case the server rejects it (negative) */
    int16_t *last_wake_pcm;
    int last_wake_len, last_wake_playing;
    char last_wake_kw[48];

    /* uplink */
    int16_t up[UP_FRAME];
    int up_n;

    engine_ui_cb_t ui;
    engine_settings_cb_t settings_cb;
    engine_negative_cb_t neg_cb;
} E;

static void ui(const char *ev, int arg)
{
    if (E.ui)
        E.ui(ev, arg);
}

static void send_json(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void send_json(const char *fmt, ...)
{
    char buf[512];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    if (n > 0 && n < (int)sizeof buf)
        sat_send_text(buf);
}

static void json_str(char *out, size_t n, const char *s)
{
    size_t o = 0;
    if (n < 3)
        return;
    out[o++] = '"';
    for (; *s && o + 7 < n; s++) {
        unsigned char ch = (unsigned char)*s;
        if (ch == '"' || ch == '\\') {
            out[o++] = '\\';
            out[o++] = (char)ch;
        } else if (ch < 0x20) {
            o += (size_t)snprintf(out + o, n - o, "\\u%04x", ch);
        } else {
            out[o++] = (char)ch;
        }
    }
    out[o++] = '"';
    out[o] = 0;
}

/* ------------------------------------------------------------ Config --- */

void engine_init(void)
{
    memset(&E, 0, sizeof E);
    E.lock = sat_lock_new();
    engine_cfg_t *c = &E.cfg;
    c->kws_enabled = 1;
    c->kws_barge_in = 1;
    c->kws_refractory_ms = 1500;
    c->kws_threshold = 0.38f;
    c->kws_auto_threshold = 1;
    c->kws_min_matches = 0;         /* 0 = auto: 2 with 3+ samples */
    c->kws_neg_margin = 0.03f;
    c->kws_playback_margin = 0.08f;     /* added to the threshold while music plays */
    c->kws_tts_threshold = 0.44f;       /* ceiling while its own voice plays */
    c->ns_floor_db = -8;            /* gentle: recognizers dislike heavy suppression */
    c->vad_threshold_db = 6;
    c->agc_enabled = 1;
    c->agc_target_db = -20;
    c->agc_max_gain_db = 24;
    c->eos_silence_ms = 1200;
    c->listen_timeout_ms = 6000;
    c->max_listen_ms = 15000;
    c->think_timeout_ms = 15000;
    c->eos_grace_ms = 1500;
    c->preroll_ms = 1500;
    c->button_preroll_ms = 300;
    c->learn_negatives = 1;
    c->earcon_wake = 1;
    c->earcon_end = 1;
    E.volume = 60;
    ns_init(&E.ns);
    agc_init(&E.agc);
    E.det = sat_calloc_fast(sizeof *E.det);
    E.ring = sat_calloc_big(RING * sizeof(int16_t));
    E.last_wake_pcm = sat_calloc_big((size_t)(RING < SR * 2 ? RING : SR * 2) * sizeof(int16_t));
    E.cand = -1;
    E.kws_cost = 9;
    if (!E.det || !E.ring || !E.last_wake_pcm)
        LOGE("engine: out of memory");
}

void engine_get_cfg(engine_cfg_t *c)
{
    sat_lock(E.lock);
    *c = E.cfg;
    sat_unlock(E.lock);
}

void engine_set_cfg(const engine_cfg_t *c)
{
    sat_lock(E.lock);
    E.cfg = *c;
    sat_unlock(E.lock);
}

void engine_set_callbacks(engine_ui_cb_t uicb, engine_settings_cb_t settings, engine_negative_cb_t neg)
{
    E.ui = uicb;
    E.settings_cb = settings;
    E.neg_cb = neg;
}

/* ------------------------------------------------------------- Model --- */

void engine_set_model(kws_model_t *m, float suggested)
{
    /* the old model's features go first, then the new ones move to fast
     * memory: both at once would not fit in internal RAM (~23 KB each) and
     * the new model would land in PSRAM (3 ms more per hop) */
    sat_lock(E.lock);
    kws_model_t *old = E.kws;
    E.kws = NULL;
    if (old && old != m) {
        kws_model_free(old);
        sat_free(old);
    }
    if (m)
        kws_model_compact(m);
    E.kws = m;
    if (m && E.cfg.kws_auto_threshold && m->npos >= 2 && suggested > 0)
        E.cfg.kws_threshold = suggested;
    if (E.det) {
        E.det->t = 0;
        if (kws_det_setup(E.det, m) != 0)
            LOGE("kws: no memory for the detector state");
    }
    E.cand = -1;
    sat_unlock(E.lock);
    LOGI("kws: %d templates (%d positive), threshold %.3f", m ? m->ntpl : 0, m ? m->npos : 0,
         E.cfg.kws_threshold);
}

void engine_wake_words_json(char *out, size_t n)
{
    size_t o = (size_t)snprintf(out, n, "[");
    sat_lock(E.lock);
    if (E.kws)
        for (int i = 0; i < E.kws->ntpl; i++) {
            int seen = E.kws->tpl[i].negative;
            for (int j = 0; j < i && !seen; j++)
                seen |= !E.kws->tpl[j].negative && !strcmp(E.kws->tpl[j].keyword, E.kws->tpl[i].keyword);
            if (seen)
                continue;
            char esc[110];
            json_str(esc, sizeof esc, E.kws->tpl[i].keyword);
            o += (size_t)snprintf(out + o, n > o ? n - o : 0, "%s%s", o > 1 ? "," : "", esc);
        }
    sat_unlock(E.lock);
    if (o < n)
        snprintf(out + o, n - o, "]");
}

kws_model_t *engine_model_copy(float *thr)
{
    sat_lock(E.lock);
    kws_model_t *c = NULL;
    if (E.kws && (c = sat_calloc_big(sizeof *c))) {
        *c = *E.kws;
        for (int i = 0; i < c->ntpl; i++) {
            size_t b = KWS_TPL_BYTES(c->tpl[i].len);
            c->tpl[i].f = sat_calloc_big(b);
            if (!c->tpl[i].f) {
                c->ntpl = i;
                break;
            }
            memcpy(c->tpl[i].f, E.kws->tpl[i].f, b);
        }
    }
    if (thr)
        *thr = E.cfg.kws_threshold;
    sat_unlock(E.lock);
    return c;
}

int engine_model_count(void)
{
    sat_lock(E.lock);
    int n = E.kws ? E.kws->ntpl : 0;
    sat_unlock(E.lock);
    return n;
}

/* ------------------------------------------------------------- State --- */

static void emit_state(const char *reason)
{
    send_json("{\"type\":\"state\",\"state\":\"%s\",\"reason\":\"%s\",\"muted\":%s,\"wake_id\":%u}",
              state_names[E.state], reason, E.mic_muted ? "true" : "false", (unsigned)E.wake_id);
    ui("state", E.state);
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
    if (s != ST_SPEAKING && s != ST_THINKING)
        E.session_end_pending = 0;
    emit_state(reason);
}

static void flush_uplink(void)
{
    if (E.up_n > 0)
        sat_send_audio(E.up, E.up_n);
    E.up_n = 0;
}

static void stop_uplink(const char *reason)
{
    if (!E.streaming)
        return;
    flush_uplink();
    E.streaming = 0;
    /* the satellite agent forwards every end of the uplink as audio_end */
    send_json("{\"type\":\"audio_end\",\"wake_id\":%u,\"reason\":\"%s\"}", (unsigned)E.wake_id, reason);
}

/* Sends the last `ms` of the pre-roll ring as uplink audio, with gain. */
static void send_ring(int ms, float gain)
{
    int n = SR * ms / 1000;
    if (n > RING - HOP)
        n = RING - HOP;
    if (n <= 0 || !E.ring)
        return;
    int16_t buf[UP_FRAME];
    int start = (E.ring_pos - n + RING) % RING;
    for (int i = 0; i < n; i += UP_FRAME) {
        int k = n - i < UP_FRAME ? n - i : UP_FRAME;
        for (int j = 0; j < k; j++)
            buf[j] = (int16_t)clampf(E.ring[(start + i + j) % RING] * gain, -32768, 32767);
        sat_send_audio(buf, k);
    }
}

static void start_listening(const char *source, const char *keyword, float score, int kws,
                            int preroll_ms, int timeout_ms, int earcon)
{
    if (E.mic_muted) {
        mixer_earcon(EC_MUTE_ON, 0);
        ui("mute", 1);
        return;
    }
    if (E.link != LINK_ONLINE) {
        /* local fallback: nobody to talk to */
        mixer_earcon(EC_OFFLINE, 0);
        ui("error", 0);
        LOGI("wake (%s) while offline", source);
        return;
    }
    int barge = E.state == ST_SPEAKING || E.state == ST_THINKING;
    int kinds = mixer_active_kinds();
    int playing = (kinds & ((1 << SK_TTS) | (1 << SK_MEDIA) | (1 << SK_ALARM))) != 0;
    int own_voice = (kinds & (1 << SK_TTS)) != 0;
    if (barge)
        mixer_stop_kinds((1 << SK_TTS) | (1 << SK_EARCON));
    E.wake_id++;
    E.wakes++;
    E.streaming = 1;
    E.up_n = 0;
    E.speech_seen = 0;
    E.eos_sent = 0;
    E.listen_start = E.frame;
    E.last_speech = E.frame;
    E.listen_timeout_f = (int)MS2F(timeout_ms);
    E.followup_listen = strcmp(source, "followup") == 0;
    E.session_end_pending = 0;
    memset(&E.istft, 0, sizeof E.istft);   /* fresh overlap-add for the new uplink */
    E.state = -1;           /* force a state event even if already listening */
    set_state(ST_LISTENING, source);
    if (earcon)
        mixer_earcon(EC_WAKE, 0);
    if (strcmp(source, "followup") != 0)
        ui("wake", kws);
    /* the server splits pre-roll from live audio by preroll_ms: report what is sent */
    if (preroll_ms > (RING - HOP) * 1000 / SR)
        preroll_ms = (RING - HOP) * 1000 / SR;
    char kw[64];
    json_str(kw, sizeof kw, keyword);
    send_json("{\"type\":\"wake\",\"wake_id\":%u,\"source\":\"%s\",\"keyword\":%s,\"score\":%.3f,"
              "\"snr_db\":%.1f,\"ts\":%lld,\"preroll_ms\":%d,\"barge_in\":%s,\"playing\":%s,\"tts\":%s}",
              (unsigned)E.wake_id, source, kw, score, E.snr_db, (long long)sat_real_ns(), preroll_ms,
              barge ? "true" : "false", playing ? "true" : "false", own_voice ? "true" : "false");
    if (preroll_ms > 0)
        send_ring(preroll_ms, agc_gain(&E.agc));
}

static void finish_session(int follow_up, int follow_ms)
{
    E.session_end_pending = 0;
    if (follow_up && !E.mic_muted)
        start_listening("followup", "", 1.0f, 0, 0, follow_ms > 0 ? follow_ms : 6000, 0);
    else
        set_state(ST_IDLE, "session_end");
}

static void handle_mixer_events(void)
{
    mixer_event_t ev;
    static const char *names[] = {"", "started", "finished", "stopped", "underrun", "overflow"};
    static const char *kinds[] = {"media", "tts", "earcon", "alarm"};
    while (mixer_poll_event(&ev)) {
        if (ev.kind != SK_EARCON && ev.event <= MIXEV_STOPPED) {
            const char *what = names[ev.event];
            if (ev.event == MIXEV_STOPPED)
                send_json("{\"type\":\"playback\",\"id\":%u,\"kind\":\"%s\",\"what\":\"%s\",\"ms\":%lld}",
                          (unsigned)ev.id, kinds[ev.kind], what, (long long)(ev.frames * 1000 / OUT_RATE));
            else
                send_json("{\"type\":\"playback\",\"id\":%u,\"kind\":\"%s\",\"event\":\"%s\",\"what\":\"%s\","
                          "\"ms\":%lld}", (unsigned)ev.id, kinds[ev.kind], what, what,
                          (long long)(ev.frames * 1000 / OUT_RATE));
        }
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

/* ------------------------------------------------------- Frame process --- */

void engine_process(const int16_t *clean, int vad, int far_only)
{
    sat_lock(E.lock);
    engine_cfg_t *c = &E.cfg;
    E.frame++;

    float x[HOP];
    for (int n = 0; n < HOP; n++)
        x[n] = E.mic_muted ? 0 : clean[n] * (1.0f / 32768.0f);

    /* spectrum, gentle noise suppression (also gives the SNR), features */
    cfloat Y[NBIN];
    float feat[KWS_NFEAT];
    stft_push(&E.stft, x, Y);
    ns_process(&E.ns, Y, Y, c->ns_floor_db, c->vad_threshold_db);
    E.snr_db = E.ns.snr_db;
    const int kws_on = c->kws_enabled && E.kws && E.kws->npos > 0 && E.det && !E.mic_muted &&
                       E.state != ST_LISTENING && (E.state == ST_IDLE || c->kws_barge_in);
    if (kws_on)
        kws_features(Y, feat);

    /* pre-roll ring: the clean stream as the AFE gave it */
    if (E.ring) {
        for (int n = 0; n < HOP; n++)
            E.ring[(E.ring_pos + n) % RING] = E.mic_muted ? 0 : clean[n];
        E.ring_pos = (E.ring_pos + HOP) % RING;
    }

    /* voiced frames for the hotword: bit 0 any voice, bit 1 not our own
     * playback alone */
    vad = vad && !E.mic_muted;
    E.speech_hist[E.frame & 255] = (vad ? 1 : 0) | ((vad && !far_only) ? 2 : 0);
    if (vad) {
        E.speech = 1;
        E.hang = 19;            /* 300 ms hangover: short pauses are not silence */
    } else if (E.hang > 0) {
        E.hang--;
    } else {
        E.speech = 0;
    }
    float lvl = vad && !far_only ? clampf(E.snr_db / 22, 0, 1) : 0;
    E.voice_level += (lvl - E.voice_level) * (lvl > E.voice_level ? 0.3f : 0.08f);

    /* uplink audio: our noise suppression (as on the ReSpeaker: the AFE's is
     * off), resynthesized, then AGC'd */
    if (E.state == ST_LISTENING) {     /* only the uplink needs it: ~80k instructions a hop */
        istft_push(&E.istft, Y, x);
        if (c->agc_enabled)
            agc_process(&E.agc, x, HOP, E.speech, c->agc_target_db, c->agc_max_gain_db);
    }

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
        E.kws_cost = kws_det_push(E.det, E.kws, feat, E.speech, thr);
        /* the match must cover speech, not noise that happens to fit; while
         * playing, any voice counts, with less of it required (the server's
         * verifier and the learned negatives take care of extra false wakes) */
        int voiced = 0, span = E.det->last_span;
        const int bit = playing ? 1 : 2, need = playing ? 4 : 6;
        for (int k = 0; k < span && k < 256; k++)
            voiced += (E.speech_hist[(E.frame - k) & 255] & bit) != 0;
        int minm = c->kws_min_matches;
        if (minm == 0)
            minm = E.kws->npos >= 3 && !playing ? 2 : 1;
        int ok = E.det->last_tpl >= 0 && E.det->last_neg >= E.kws_cost + c->kws_neg_margin;
        if (ok && E.kws_cost < thr && E.det->last_matches >= minm && voiced * 10 >= span * need &&
            E.frame > MS2F(1000)) {
            if (E.cand < 0 || E.kws_cost < E.cand_cost) {
                if (E.cand < 0)
                    E.cand_frame = E.frame;
                E.cand = 0;
                E.cand_cost = E.kws_cost;
                E.cand_tpl = E.det->last_tpl;
                E.cand_span = span;
            }
        }
        /* fire once the cost stopped improving for a few frames */
        if (E.cand >= 0 && E.frame - E.cand_frame >= 4) {
            int tpl = E.cand_tpl;
            const char *kw = tpl >= 0 ? E.kws->tpl[tpl].keyword : "";
            float score = clampf(1.0f - E.cand_cost / (2 * c->kws_threshold), 0, 1);
            LOGI("wake: '%s' cost %.3f thr %.3f at %.2fs (speech %d)", kw, E.cand_cost, thr,
                 E.frame * HOP / (float)SR, E.speech);
            /* keep the matched audio: a server rejection makes it a negative */
            if (E.ring && E.last_wake_pcm) {
                int n = (E.cand_span + 24) * HOP;
                if (n > SR * 2)
                    n = SR * 2;
                if (n > RING - HOP)
                    n = RING - HOP;
                int start = (E.ring_pos - n + RING) % RING;
                for (int i = 0; i < n; i++)
                    E.last_wake_pcm[i] = E.ring[(start + i) % RING];
                E.last_wake_len = n;
                E.last_wake_playing = playing;
                snprintf(E.last_wake_kw, sizeof E.last_wake_kw, "%s", kw);
            }
            char kwc[48];
            snprintf(kwc, sizeof kwc, "%s", kw);
            start_listening("kws", kwc, score, 1, c->preroll_ms, c->listen_timeout_ms, c->earcon_wake);
            E.cand = -1;
            E.refractory_until = E.frame + MS2F(c->kws_refractory_ms);
            kws_det_reset(E.det, E.kws);
        }
    } else if (!kws_on) {
        E.cand = -1;
        E.kws_cost = 9;
    }

    /* state machine timers */
    if (E.state == ST_LISTENING) {
        if (E.streaming) {
            for (int n = 0; n < HOP; n++) {
                E.up[E.up_n++] = (int16_t)clampf(x[n] * 32767.0f, -32768, 32767);
                if (E.up_n == UP_FRAME)
                    flush_uplink();
            }
        }
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
                send_json("{\"type\":\"eos\",\"wake_id\":%u,\"reason\":\"%s\",\"speech\":%s}",
                          (unsigned)E.wake_id, why, E.speech_seen ? "true" : "false");
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
            ui("error", 0);
            set_state(ST_IDLE, "timeout");
        }
    }
    handle_mixer_events();
    sat_unlock(E.lock);
}

/* ---------------------------------------------------------- Commands --- */

void engine_link(int link)
{
    sat_lock(E.lock);
    int was = E.link;
    E.link = link;
    if (link != LINK_ONLINE && E.state != ST_IDLE) {
        stop_uplink("link_down");
        mixer_stop_kinds(1 << SK_TTS);
        set_state(ST_IDLE, "link_down");
    }
    sat_unlock(E.lock);
    if (was != link)
        ui("link", link);
}

void engine_wake(const char *source)
{
    sat_lock(E.lock);
    start_listening(source, "", 1.0f, 0, E.cfg.button_preroll_ms, E.cfg.listen_timeout_ms, E.cfg.earcon_wake);
    sat_unlock(E.lock);
}

void engine_talk_button(void)
{
    sat_lock(E.lock);
    if (E.alarm_on) {
        E.alarm_on = 0;
        mixer_stop_kinds((1 << SK_ALARM) | (1 << SK_EARCON));
        send_json("{\"type\":\"button\",\"action\":\"stop\"}");
        ui("alarm", 0);
    } else if (E.state == ST_LISTENING && E.streaming) {
        /* push-to-talk end: the server transcribes what it got */
        stop_uplink("button");
        set_state(ST_THINKING, "button");
        if (E.cfg.earcon_end)
            mixer_earcon(EC_END, 0);
    } else {
        start_listening("button", "", 1.0f, 0, E.cfg.button_preroll_ms, E.cfg.listen_timeout_ms,
                        E.cfg.earcon_wake);
    }
    sat_unlock(E.lock);
}

void engine_listen(int timeout_ms, int earcon)
{
    sat_lock(E.lock);
    start_listening("followup", "", 1.0f, 0, 0, timeout_ms > 0 ? timeout_ms : 6000, earcon);
    sat_unlock(E.lock);
}

void engine_eot(void)
{
    sat_lock(E.lock);
    if (E.state == ST_LISTENING) {
        stop_uplink("eot");
        set_state(ST_THINKING, "eot");
        if (E.cfg.earcon_end && !E.followup_listen)
            mixer_earcon(EC_END, 0);
    }
    sat_unlock(E.lock);
}

static void learn_negative(void)
{
    /* a rejection during playback says little (the verifier hears the music
     * too): never learn from those */
    int n = E.last_wake_playing ? 0 : E.last_wake_len;
    E.last_wake_len = 0;
    if (n <= 0 || !E.last_wake_kw[0] || !E.kws || !E.cfg.learn_negatives)
        return;
    kws_template_t t;
    memset(&t, 0, sizeof t);
    if (kws_make_template(&t, E.last_wake_pcm, n) != 0)
        return;
    snprintf(t.keyword, sizeof t.keyword, "%s", E.last_wake_kw);
    snprintf(t.name, sizeof t.name, "neg_%lld", (long long)(sat_mono_us() / 1000));
    t.negative = 1;
    /* keep the newest KWS_MAX_NEG negatives of this keyword */
    int nneg = 0, oldest = -1;
    for (int i = 0; i < E.kws->ntpl; i++)
        if (E.kws->tpl[i].negative && !strcmp(E.kws->tpl[i].keyword, t.keyword)) {
            nneg++;
            if (oldest < 0)
                oldest = i;
        }
    if ((nneg >= KWS_MAX_NEG || E.kws->ntpl >= KWS_MAX_TPL) && oldest >= 0) {
        kws_template_free(&E.kws->tpl[oldest]);
        memmove(&E.kws->tpl[oldest], &E.kws->tpl[oldest + 1],
                sizeof(kws_template_t) * (size_t)(E.kws->ntpl - oldest - 1));
        E.kws->ntpl--;
    }
    if (kws_model_add(E.kws, &t) != 0) {
        kws_template_free(&t);
        return;
    }
    kws_model_finish(E.kws);
    kws_det_setup(E.det, E.kws);
    LOGI("kws: learned a negative for '%s' (%d frames)", t.keyword, t.len);
    if (E.neg_cb)
        E.neg_cb(&E.kws->tpl[E.kws->ntpl - 1]);
}

void engine_cancel(const char *reason, int rejected_learn)
{
    sat_lock(E.lock);
    stop_uplink(reason);
    if (!strcmp(reason, "rejected")) {
        E.false_wakes++;
        if (rejected_learn)
            learn_negative();
    }
    if (E.state != ST_IDLE) {
        if (E.state == ST_SPEAKING)
            mixer_stop_kinds(1 << SK_TTS);
        set_state(ST_IDLE, reason);
    }
    sat_unlock(E.lock);
}

void engine_session_end(int fu, int ms)
{
    sat_lock(E.lock);
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
    sat_unlock(E.lock);
}

void engine_stop(int media)
{
    sat_lock(E.lock);
    mixer_stop_kinds((1 << SK_TTS) | (1 << SK_ALARM) | (1 << SK_EARCON) | (media ? 1 << SK_MEDIA : 0));
    if (E.alarm_on) {
        E.alarm_on = 0;
        ui("alarm", 0);
    }
    if (E.state == ST_SPEAKING)
        set_state(ST_IDLE, "stop");
    sat_unlock(E.lock);
}

void engine_button_stop(void)
{
    sat_lock(E.lock);
    int was_alarm = E.alarm_on;
    E.alarm_on = 0;
    mixer_stop_kinds((1 << SK_TTS) | (1 << SK_ALARM) | (1 << SK_EARCON) | (1 << SK_MEDIA));
    if (E.state == ST_LISTENING || E.state == ST_THINKING) {
        stop_uplink("button");
        send_json("{\"type\":\"button\",\"action\":\"cancel\"}");
        set_state(ST_IDLE, "button");
    } else if (E.state == ST_SPEAKING) {
        set_state(ST_IDLE, "stop");
    }
    send_json("{\"type\":\"button\",\"action\":\"stop\"}");
    if (was_alarm)
        ui("alarm", 0);
    sat_unlock(E.lock);
}

static void settings_changed(int vol, int mute)
{
    char buf[96];
    size_t o = (size_t)snprintf(buf, sizeof buf, "{\"type\":\"settings\"");
    if (vol)
        o += (size_t)snprintf(buf + o, sizeof buf - o, ",\"volume\":%d", (int)lrintf(E.volume));
    if (mute)
        o += (size_t)snprintf(buf + o, sizeof buf - o, ",\"mic_muted\":%s", E.mic_muted ? "true" : "false");
    snprintf(buf + o, sizeof buf - o, "}");
    sat_send_text(buf);
    if (E.settings_cb)
        E.settings_cb(E.volume, E.mic_muted);
}

void engine_set_muted(int muted, int quiet)
{
    sat_lock(E.lock);
    muted = muted != 0;
    if (muted != E.mic_muted) {
        E.mic_muted = muted;
        if (muted) {
            stop_uplink("muted");
            if (E.state == ST_LISTENING)
                set_state(ST_IDLE, "muted");
        }
        if (!quiet)
            mixer_earcon(muted ? EC_MUTE_ON : EC_MUTE_OFF, 0);
        emit_state(muted ? "muted" : "unmuted");
        ui("mute", muted);
        settings_changed(0, 1);
    }
    sat_unlock(E.lock);
}

void engine_toggle_mute(void) { engine_set_muted(!engine_status().muted, 0); }

void engine_set_volume(float volume, int announce)
{
    mixer_cfg_t mc;
    sat_lock(E.lock);
    volume = clampf(volume, 0, 100);
    int changed = fabsf(volume - E.volume) > 0.01f;
    E.volume = volume;
    mixer_get_cfg(&mc);
    mc.volume = volume;
    mixer_set_cfg(&mc);
    if (changed || announce) {
        mixer_earcon(EC_VOLUME, 0);
        ui("volume", (int)lrintf(volume));
        settings_changed(1, 0);
    }
    sat_unlock(E.lock);
}

void engine_init_settings(float volume, int mic_muted)
{
    mixer_cfg_t mc;
    sat_lock(E.lock);
    E.volume = clampf(volume, 0, 100);
    E.mic_muted = mic_muted != 0;
    mixer_get_cfg(&mc);
    mc.volume = E.volume;
    mixer_set_cfg(&mc);
    sat_unlock(E.lock);
}

void engine_volume_step(float delta) { engine_set_volume(engine_status().volume + delta, 1); }

void engine_set_speaker_muted(int muted)
{
    mixer_cfg_t mc;
    mixer_get_cfg(&mc);
    mc.muted = muted != 0;
    mixer_set_cfg(&mc);
}

void engine_alarm(int on)
{
    sat_lock(E.lock);
    on = on != 0;
    if (on != E.alarm_on) {
        E.alarm_on = on;
        if (on)
            mixer_earcon(EC_ALARM, 1);
        else
            mixer_stop_kinds(1 << SK_ALARM);
        ui("alarm", on);
    }
    sat_unlock(E.lock);
}

void engine_led_hint(const char *pattern, int on)
{
    if (!strcmp(pattern, "notify")) {
        sat_lock(E.lock);
        E.notify_on = on;
        sat_unlock(E.lock);
        ui("notify", on);
    } else {
        ui(pattern, on);
    }
}

void engine_earcon(const char *name, int loop)
{
    static const char *names[] = {"wake", "end", "error", "alarm", "notify", "mute_on", "mute_off",
                                  "volume", "offline", "done"};
    for (int i = 0; i < EC_COUNT; i++)
        if (!strcmp(name, names[i])) {
            mixer_earcon(i, loop);
            if (i == EC_DONE)
                ui("success", 0);
            else if (i == EC_ERROR)
                ui("error", 0);
            return;
        }
}

static int kind_by_name(const char *s)
{
    if (!strcmp(s, "media"))
        return SK_MEDIA;
    if (!strcmp(s, "tts"))
        return SK_TTS;
    if (!strcmp(s, "alarm") || !strcmp(s, "alert"))
        return SK_ALARM;
    if (!strcmp(s, "earcon") || !strcmp(s, "chime"))
        return SK_EARCON;
    return -1;
}

void engine_stream_open(uint32_t id, const char *kind, int rate, int channels, int64_t start_at,
                        float gain_db, int pre)
{
    int k = kind_by_name(kind);
    if (pre < 0)
        pre = k == SK_MEDIA ? 1000 : 300;     /* speech: 300 ms absorbs Wi-Fi jitter */
    if (k < 0 || mixer_open(id, k, rate, channels, start_at, gain_db, pre) < 0) {
        LOGE("stream_open %u failed (%s %d Hz %d ch)", (unsigned)id, kind, rate, channels);
        send_json("{\"type\":\"playback\",\"id\":%u,\"what\":\"error\"}", (unsigned)id);
    }
}

void engine_stream_close(uint32_t id, int drain) { mixer_close(id, drain); }

engine_status_t engine_status(void)
{
    engine_status_t s;
    memset(&s, 0, sizeof s);
    if (!E.lock) {                      /* not initialized yet (early boot) */
        s.kws_cost = 9;
        s.out_db = -120;
        s.volume = 60;
        return s;
    }
    sat_lock(E.lock);
    s.state = E.state;
    s.muted = E.mic_muted;
    s.link = E.link;
    s.alarm = E.alarm_on;
    s.notify = E.notify_on;
    s.wake_id = E.wake_id;
    s.voice_level = E.voice_level;
    s.volume = E.volume;
    s.kws_cost = E.kws_cost;
    s.kws_threshold = E.cfg.kws_threshold;
    s.snr_db = E.snr_db;
    s.templates = E.kws ? E.kws->npos : 0;
    s.wakes = E.wakes;
    s.rejected = E.false_wakes;
    s.frame = E.frame;
    sat_unlock(E.lock);
    s.out_db = mixer_out_db();
    return s;
}
