/*
 * Output mixer and DSP, ported from satellite/engine/src/mixer.c.
 *
 * Streams arrive from the server (TTS, media, alerts) as chunks of PCM and
 * are queued per stream (jitter buffer, in PSRAM), optionally started at a
 * given wall clock time (multi-room sync), resampled to 48 kHz and mixed
 * with per-kind priority: media is ducked while the assistant listens /
 * talks, TTS under alarms. Earcons are synthesized locally so they play with
 * no latency.
 *
 * Differences from the satellite: the mix is folded to mono (one speaker,
 * mono DAC) before the output chain, earcons are stored as 24 kHz int16 in
 * PSRAM, and the compressor computes its gain per 16-sample block (log/pow
 * per sample is too slow on the ESP32). Output chain: master volume ->
 * high-pass -> 3-band EQ -> compressor -> peak limiter -> s16. Keeping the
 * amplifier out of clipping keeps the echo path linear, which the AEC needs.
 */
#include "mixer.h"

#include <stdlib.h>
#include <string.h>

#include "resample.h"

#define MAX_STREAMS 6
#define EC_RATE     24000
#define DRC_BLOCK   16

typedef struct chunk {
    struct chunk *next;
    int n, pos;             /* samples (interleaved) */
    int16_t d[];
} chunk_t;

typedef struct {
    int used;
    uint32_t id;
    int kind, rate, ch;
    interp_t ip;            /* mono (stereo is folded before interpolation) */
    chunk_t *head, *tail;
    size_t bytes;
    int closed, started, paused, stopping, in_underrun;
    int64_t start_at;
    int prebuf;             /* input frames */
    int64_t frames_in, frames_out;
    float gain_db, cur;
    /* earcon source */
    const int16_t *ec;
    int ec_len, ec_pos, ec_loop;
} stream_t;

typedef struct {
    float b0, b1, b2, a1, a2;
    float z1, z2;
} biquad_t;

static sat_lock_t *lock;
/* kinds of the used, unpaused streams: kept up to date at every unlock so the
 * engine's per-hop query never waits for the play task's lock */
static volatile int kinds_mask;
static stream_t streams[MAX_STREAMS];

static void unlock(void)
{
    int mask = 0;
    for (int i = 0; i < MAX_STREAMS; i++)
        if (streams[i].used && !streams[i].paused)
            mask |= 1 << streams[i].kind;
    kinds_mask = mask;
    sat_unlock(lock);
}
static mixer_cfg_t cfg;
static biquad_t eq[4];          /* hpf, low shelf, peak, high shelf */
static int eq_dirty = 1;
static float master_cur, comp_env_db = -120, comp_gain = 1, lim_gain = 1;
static volatile int duck_flag;
static int eq_on[4];
static float out_db = -120;
static size_t queued, max_queued = 3u * 1024 * 1024;
static uint32_t ec_seq = 0xE0000000u;

static mixer_event_t evq[64];
static int evq_head, evq_tail;

static int16_t *earcon[EC_COUNT];
static int earcon_len[EC_COUNT];

static void push_event(uint32_t id, int kind, int event, int64_t frames)
{
    int next = (evq_head + 1) % 64;
    if (next == evq_tail)
        return;
    evq[evq_head] = (mixer_event_t){id, kind, event, frames};
    evq_head = next;
}

int mixer_poll_event(mixer_event_t *ev)
{
    sat_lock(lock);
    int ok = evq_tail != evq_head;
    if (ok) {
        *ev = evq[evq_tail];
        evq_tail = (evq_tail + 1) % 64;
    }
    unlock();
    return ok;
}

/* ------------------------------------------------------------ Earcons --- */

typedef struct {
    float t, f, dur, amp;
} note_t;

static int synth(int which, const note_t *notes, int n, float total)
{
    int len = (int)(total * EC_RATE);
    float *buf = sat_calloc_big((size_t)len * sizeof(float));
    int16_t *out = sat_calloc_big((size_t)len * sizeof(int16_t));
    if (!buf || !out) {
        sat_free(buf);
        sat_free(out);
        return -1;
    }
    for (int i = 0; i < n; i++) {
        const note_t *nt = &notes[i];
        int s0 = (int)(nt->t * EC_RATE), ns = (int)(nt->dur * EC_RATE);
        for (int k = 0; k < ns && s0 + k < len; k++) {
            float t = (float)k / EC_RATE;
            float att = fminf(1.0f, t / 0.004f);                 /* 4 ms attack */
            float dec = expf(-t / (nt->dur * 0.35f));
            float rel = fminf(1.0f, (nt->dur - t) / 0.01f);
            float ph = TAU_F * nt->f * t;
            /* sine with a touch of 2nd and 3rd harmonics: soft, bell-like */
            float v = sinf(ph) + 0.18f * sinf(2 * ph) * expf(-t / 0.05f) + 0.06f * sinf(3 * ph);
            buf[s0 + k] += nt->amp * att * dec * rel * v;
        }
    }
    for (int i = 0; i < len; i++)
        out[i] = (int16_t)lrintf(clampf(buf[i], -1, 1) * 32767.0f);
    sat_free(buf);
    earcon[which] = out;
    earcon_len[which] = len;
    return 0;
}

static int make_earcons(void)
{
    const float a = 0.22f;
    int r = 0;
    r |= synth(EC_WAKE, (note_t[]){{0, 880, 0.18f, a}, {0.075f, 1318.5f, 0.28f, a}}, 2, 0.4f);
    r |= synth(EC_END, (note_t[]){{0, 1318.5f, 0.14f, a * 0.6f}, {0.07f, 880, 0.22f, a * 0.6f}}, 2, 0.32f);
    r |= synth(EC_ERROR, (note_t[]){{0, 415.3f, 0.2f, a}, {0.16f, 311.1f, 0.32f, a}}, 2, 0.5f);
    r |= synth(EC_ALARM, (note_t[]){{0, 1046.5f, 0.12f, a * 1.3f}, {0.18f, 1046.5f, 0.12f, a * 1.3f},
                                    {0.36f, 1046.5f, 0.12f, a * 1.3f}, {0.54f, 1318.5f, 0.3f, a * 1.3f}},
               4, 1.4f);
    r |= synth(EC_NOTIFY, (note_t[]){{0, 659.3f, 0.2f, a}, {0.09f, 880, 0.2f, a}, {0.18f, 1174.7f, 0.35f, a}},
               3, 0.6f);
    r |= synth(EC_MUTE_ON, (note_t[]){{0, 659.3f, 0.12f, a}, {0.08f, 440, 0.2f, a}}, 2, 0.32f);
    r |= synth(EC_MUTE_OFF, (note_t[]){{0, 440, 0.12f, a}, {0.08f, 659.3f, 0.2f, a}}, 2, 0.32f);
    r |= synth(EC_VOLUME, (note_t[]){{0, 1174.7f, 0.06f, a * 0.7f}}, 1, 0.08f);
    /* action done: two quick soft notes, rising a fifth */
    r |= synth(EC_DONE, (note_t[]){{0, 987.8f, 0.09f, a * 0.75f}, {0.07f, 1480.0f, 0.16f, a * 0.75f}}, 2, 0.25f);
    r |= synth(EC_OFFLINE, (note_t[]){{0, 659.3f, 0.16f, a}, {0.14f, 523.3f, 0.16f, a}, {0.28f, 392, 0.3f, a}},
               3, 0.62f);
    return r;
}

/* ------------------------------------------------------------ Streams --- */

static stream_t *find(uint32_t id)
{
    for (int i = 0; i < MAX_STREAMS; i++)
        if (streams[i].used && streams[i].id == id)
            return &streams[i];
    return NULL;
}

static void free_stream(stream_t *s)
{
    chunk_t *c = s->head;
    while (c) {
        chunk_t *n = c->next;
        sat_free(c);
        c = n;
    }
    queued -= s->bytes;
    memset(s, 0, sizeof *s);
}

static stream_t *alloc_stream(void)
{
    for (int i = 0; i < MAX_STREAMS; i++)
        if (!streams[i].used)
            return &streams[i];
    /* full: evict the lowest priority stream */
    stream_t *victim = &streams[0];
    for (int i = 1; i < MAX_STREAMS; i++)
        if (streams[i].kind < victim->kind)
            victim = &streams[i];
    push_event(victim->id, victim->kind, MIXEV_STOPPED, victim->frames_out);
    free_stream(victim);
    return victim;
}

void mixer_set_max_queued(size_t bytes) { max_queued = bytes; }

int mixer_init(int with_earcons)
{
    lock = sat_lock_new();
    cfg = (mixer_cfg_t){.volume = 60, .duck_db = -18, .listen_duck_db = -40, .gain_db = {0, 0, -4, 0},
                        .hpf_hz = 120, .drc_threshold_db = -14, .drc_ratio = 3,
                        .limiter_db = -1, .earcons = 1};
    resample_init();
    if (!lock)
        return -1;
    return with_earcons ? make_earcons() : 0;
}

void mixer_set_cfg(const mixer_cfg_t *c)
{
    sat_lock(lock);
    cfg = *c;
    eq_dirty = 1;
    unlock();
}

void mixer_get_cfg(mixer_cfg_t *c)
{
    sat_lock(lock);
    *c = cfg;
    unlock();
}

int mixer_open(uint32_t id, int kind, int rate, int channels, int64_t start_at_ns,
               float gain_db, int prebuffer_ms)
{
    if ((rate != 16000 && rate != 24000 && rate != 48000) || channels < 1 || channels > 2 ||
        kind < 0 || kind > SK_ALARM)
        return -1;
    sat_lock(lock);
    stream_t *s = find(id);
    if (s) {
        push_event(s->id, s->kind, MIXEV_STOPPED, s->frames_out);
        free_stream(s);
    }
    s = alloc_stream();
    s->used = 1;
    s->id = id;
    s->kind = kind;
    s->rate = rate;
    s->ch = channels;
    interp_init(&s->ip, OUT_RATE / rate);
    s->start_at = start_at_ns;
    s->prebuf = rate * prebuffer_ms / 1000;
    s->gain_db = gain_db;
    s->cur = 0;
    unlock();
    return 0;
}

int mixer_write(uint32_t id, const void *pcm, int samples)
{
    if (samples <= 0)
        return 0;
    chunk_t *c = sat_calloc_big(sizeof(chunk_t) + (size_t)samples * 2);
    if (!c)
        return -1;
    c->next = NULL;
    c->n = samples;
    c->pos = 0;
    memcpy(c->d, pcm, (size_t)samples * 2);
    sat_lock(lock);
    stream_t *s = find(id);
    if (!s || s->closed || queued + (size_t)samples * 2 > max_queued) {
        if (s && !s->closed)
            push_event(id, s->kind, MIXEV_OVERFLOW, s->frames_out);
        unlock();
        sat_free(c);
        return -1;
    }
    if (s->tail)
        s->tail->next = c;
    else
        s->head = c;
    s->tail = c;
    s->bytes += (size_t)samples * 2;
    queued += (size_t)samples * 2;
    s->frames_in += samples / s->ch;
    unlock();
    return 0;
}

void mixer_close(uint32_t id, int drain)
{
    sat_lock(lock);
    stream_t *s = find(id);
    if (s) {
        if (drain)
            s->closed = 1;
        else
            s->stopping = 1;
    }
    unlock();
}

void mixer_pause(uint32_t id, int paused)
{
    sat_lock(lock);
    stream_t *s = find(id);
    if (s)
        s->paused = paused;
    unlock();
}

void mixer_stop_kinds(int mask)
{
    sat_lock(lock);
    for (int i = 0; i < MAX_STREAMS; i++)
        if (streams[i].used && (mask & (1 << streams[i].kind)))
            streams[i].stopping = 1;
    unlock();
}

void mixer_stop_all(void) { mixer_stop_kinds(0xF); }

static void earcon_start(int which, int loop, float gain_db);

void mixer_earcon(int which, int loop) { earcon_start(which, loop, 0); }
void mixer_earcon_gain(int which, float gain_db) { earcon_start(which, 0, gain_db); }

static void earcon_start(int which, int loop, float gain_db)
{
    if (which < 0 || which >= EC_COUNT || !earcon[which])
        return;
    sat_lock(lock);
    if (!cfg.earcons && which != EC_ALARM) {
        unlock();
        return;
    }
    int kind = which == EC_ALARM ? SK_ALARM : SK_EARCON;
    if (kind == SK_EARCON)      /* a new earcon replaces the previous one */
        for (int i = 0; i < MAX_STREAMS; i++)
            if (streams[i].used && streams[i].ec && streams[i].kind == SK_EARCON)
                free_stream(&streams[i]);
    stream_t *s = alloc_stream();
    s->used = 1;
    s->id = ec_seq++;
    s->kind = kind;
    s->rate = EC_RATE;
    s->ch = 1;
    interp_init(&s->ip, OUT_RATE / EC_RATE);
    s->ec = earcon[which];
    s->ec_len = earcon_len[which];
    s->ec_loop = loop;
    s->closed = 1;
    s->cur = 1;
    s->gain_db = gain_db;
    unlock();
}

int mixer_active_kinds(void) { return kinds_mask; }

void mixer_set_duck(int mode) { duck_flag = mode; }
float mixer_out_db(void) { return out_db; }

size_t mixer_queued_bytes(void)
{
    sat_lock(lock);
    size_t q = queued;
    unlock();
    return q;
}

/* Next input frame of a stream, folded to mono, into *in; 0 if none. */
SAT_HOT static int pop_frame(stream_t *s, float *in)
{
    if (s->ec) {
        if (s->ec_pos >= s->ec_len) {
            if (!s->ec_loop)
                return 0;
            s->ec_pos = 0;
        }
        *in = s->ec[s->ec_pos++] * (1.0f / 32768.0f);
        return 1;
    }
    chunk_t *c = s->head;
    while (c && c->pos + s->ch > c->n) {
        s->head = c->next;
        if (!s->head)
            s->tail = NULL;
        s->bytes -= (size_t)c->n * 2;
        queued -= (size_t)c->n * 2;
        sat_free(c);
        c = s->head;
    }
    if (!c)
        return 0;
    if (s->ch == 1)
        *in = c->d[c->pos] * (1.0f / 32768.0f);
    else
        *in = ((float)c->d[c->pos] + (float)c->d[c->pos + 1]) * (0.5f / 32768.0f);
    c->pos += s->ch;
    return 1;
}

static int64_t available_frames(const stream_t *s)
{
    if (s->ec)
        return s->ec_len - s->ec_pos;
    return s->frames_in - s->frames_out / (OUT_RATE / s->rate);
}

/* --------------------------------------------------------------- DSP --- */

static void biquad_set(biquad_t *b, int type, float f, float q, float gain_db)
{
    float A = powf(10, gain_db / 40), w = TAU_F * f / OUT_RATE;
    float cw = cosf(w), sw = sinf(w), al = sw / (2 * q);
    float b0, b1, b2, a0, a1, a2;
    switch (type) {
    case 0: /* high-pass */
        b0 = (1 + cw) / 2; b1 = -(1 + cw); b2 = (1 + cw) / 2;
        a0 = 1 + al; a1 = -2 * cw; a2 = 1 - al;
        break;
    case 1: { /* low shelf */
        float s = 2 * sqrtf(A) * al;
        b0 = A * ((A + 1) - (A - 1) * cw + s); b1 = 2 * A * ((A - 1) - (A + 1) * cw);
        b2 = A * ((A + 1) - (A - 1) * cw - s); a0 = (A + 1) + (A - 1) * cw + s;
        a1 = -2 * ((A - 1) + (A + 1) * cw); a2 = (A + 1) + (A - 1) * cw - s;
        break;
    }
    case 2: /* peaking */
        b0 = 1 + al * A; b1 = -2 * cw; b2 = 1 - al * A;
        a0 = 1 + al / A; a1 = -2 * cw; a2 = 1 - al / A;
        break;
    default: { /* high shelf */
        float s = 2 * sqrtf(A) * al;
        b0 = A * ((A + 1) + (A - 1) * cw + s); b1 = -2 * A * ((A - 1) + (A + 1) * cw);
        b2 = A * ((A + 1) + (A - 1) * cw - s); a0 = (A + 1) - (A - 1) * cw + s;
        a1 = 2 * ((A - 1) - (A + 1) * cw); a2 = (A + 1) - (A - 1) * cw - s;
        break;
    }
    }
    b->b0 = b0 / a0; b->b1 = b1 / a0; b->b2 = b2 / a0; b->a1 = a1 / a0; b->a2 = a2 / a0;
}

static inline float biquad_run(biquad_t *b, float x)
{
    float y = b->b0 * x + b->z1;
    b->z1 = b->b1 * x - b->a1 * y + b->z2;
    b->z2 = b->b2 * x - b->a2 * y;
    return y;
}

SAT_HOT void mixer_render(int16_t *out, int64_t output_latency_ns)
{
    float mix[OUT_PERIOD];
    memset(mix, 0, sizeof mix);
    sat_lock(lock);

    if (eq_dirty) {
        biquad_set(&eq[0], 0, fmaxf(cfg.hpf_hz, 20), 0.707f, 0);
        biquad_set(&eq[1], 1, 150, 0.7f, cfg.eq_low_db);
        biquad_set(&eq[2], 2, 1200, 0.9f, cfg.eq_mid_db);
        biquad_set(&eq[3], 3, 6000, 0.7f, cfg.eq_high_db);
        /* a band at 0 dB does nothing: skip it (4 biquads a sample at 48 kHz
         * were a good part of the playback CPU on the ESP32) */
        eq_on[0] = cfg.hpf_hz > 0;
        eq_on[1] = fabsf(cfg.eq_low_db) > 0.05f;
        eq_on[2] = fabsf(cfg.eq_mid_db) > 0.05f;
        eq_on[3] = fabsf(cfg.eq_high_db) > 0.05f;
        eq_dirty = 0;
    }

    int active = 0;
    for (int i = 0; i < MAX_STREAMS; i++)
        if (streams[i].used && streams[i].started && !streams[i].paused)
            active |= 1 << streams[i].kind;
    const int64_t now = sat_real_ns();
    static float fast, slow;        /* gain smoothing coefficients, computed once */
    if (!fast) {
        fast = 1 - expf(-1.0f / (0.012f * OUT_RATE));
        slow = 1 - expf(-1.0f / (0.30f * OUT_RATE));
    }

    for (int i = 0; i < MAX_STREAMS; i++) {
        stream_t *s = &streams[i];
        if (!s->used || s->paused)
            continue;
        if (!s->started) {
            int64_t avail = available_frames(s);
            if (!s->closed && avail < s->prebuf && !s->stopping)
                continue;
            if (s->start_at && now && now + output_latency_ns < s->start_at && !s->stopping)
                continue;
            if ((avail <= 0 && s->closed) || s->stopping) {
                push_event(s->id, s->kind, s->stopping ? MIXEV_STOPPED : MIXEV_FINISHED, 0);
                free_stream(s);
                continue;
            }
            /* late for a synchronized start: drop what should have played */
            if (s->start_at && now && !s->ec) {
                int64_t late = now + output_latency_ns - s->start_at;
                int64_t skip = late > 30000000LL ? late * s->rate / 1000000000LL : 0;
                float tmp;
                while (skip-- > 0 && pop_frame(s, &tmp))
                    s->frames_out += OUT_RATE / s->rate;
            }
            s->started = 1;
            s->cur = -1;            /* starts at its target gain */
            push_event(s->id, s->kind, MIXEV_STARTED, 0);
        }

        float duck = 0;
        if (s->kind == SK_MEDIA && duck_flag == 2)
            duck = cfg.listen_duck_db;
        else if (s->kind == SK_MEDIA && (duck_flag || (active & ((1 << SK_TTS) | (1 << SK_EARCON) | (1 << SK_ALARM)))))
            duck = cfg.duck_db;
        else if (s->kind == SK_TTS && (active & (1 << SK_ALARM)))
            duck = -12;
        float target = s->stopping ? 0 : fast_db_to_lin(s->gain_db + cfg.gain_db[s->kind] + duck);
        float coef = target < s->cur ? fast : slow;
        if (s->cur < 0)
            s->cur = target;

        /* the sample loop works on local copies (registers, not memory) */
        int done = 0, n = 0;
        const int L = s->ip.factor;
        int phase = s->ip.phase;
        float cur = s->cur;
        for (; n < OUT_PERIOD; n++) {
            if (phase == 0) {
                float in;
                if (!pop_frame(s, &in)) {
                    if (s->closed) {
                        done = 1;
                        break;
                    }
                    if (!s->in_underrun) {
                        s->in_underrun = 1;
                        push_event(s->id, s->kind, MIXEV_UNDERRUN, s->frames_out + n);
                    }
                    break;
                }
                s->in_underrun = 0;
                interp_push(&s->ip, 1, &in);
            }
            float o = interp_out1(&s->ip, phase);
            if (++phase == L)
                phase = 0;
            cur += (target - cur) * coef;
            mix[n] += o * cur;
        }
        s->ip.phase = phase;
        s->cur = cur;
        s->frames_out += n;
        if (done) {
            push_event(s->id, s->kind, MIXEV_FINISHED, s->frames_out);
            free_stream(s);
        } else if (s->stopping && s->cur < 0.001f) {
            push_event(s->id, s->kind, MIXEV_STOPPED, s->frames_out);
            free_stream(s);
        }
    }
    mixer_cfg_t c = cfg;
    int any = 0;
    for (int i = 0; i < MAX_STREAMS; i++)
        any |= streams[i].used;
    unlock();

    /* nothing to play: silence without running the output chain (it ran
     * 48000 times a second for nothing, on the core Wi-Fi needs) */
    if (!any && out_db < -100) {
        memset(out, 0, sizeof(int16_t) * OUT_PERIOD);
        return;
    }

    /* master volume, perceptual taper */
    float v = c.muted ? 0 : clampf(c.volume, 0, 100) / 100.0f;
    const float master = v * v;
    const float thr = c.drc_threshold_db, slope = 1 - 1 / maxf(c.drc_ratio, 1);
    const float ceil = fast_db_to_lin(c.limiter_db);
    /* time constants: computed once (5 expf per period were ~10k instructions) */
    static float att, rel, lrel;
    if (!att) {
        att = 1 - expf(-(float)DRC_BLOCK / (0.005f * OUT_RATE));
        rel = 1 - expf(-(float)DRC_BLOCK / (0.15f * OUT_RATE));
        lrel = 1 - expf(-1.0f / (0.08f * OUT_RATE));
    }
    /* pass 1: master gain ramp, then the active EQ bands, one band at a time
     * over the whole period (coefficients and state stay in registers) */
    float mc = master_cur;
    for (int n = 0; n < OUT_PERIOD; n++) {
        mc += (master - mc) * 0.002f;
        mix[n] *= mc;
    }
    master_cur = mc;
    for (int b = 0; b < 4; b++) {
        if (!eq_on[b])
            continue;
        biquad_t q = eq[b];
        for (int n = 0; n < OUT_PERIOD; n++) {
            float x = mix[n], y = q.b0 * x + q.z1;
            q.z1 = q.b1 * x - q.a1 * y + q.z2;
            q.z2 = q.b2 * x - q.a2 * y;
            mix[n] = y;
        }
        eq[b].z1 = q.z1;
        eq[b].z2 = q.z2;
    }
    /* pass 2: compressor on the block peak envelope, gain ramped over the
     * block; peak limiter (instant attack, smooth release); s16 */
    float env = comp_env_db, cg = comp_gain, lg = lim_gain, energy = 0;
    for (int n0 = 0; n0 < OUT_PERIOD; n0 += DRC_BLOCK) {
        float pk = 0;
        for (int k = 0; k < DRC_BLOCK; k++)
            pk = maxf(pk, fabsf(mix[n0 + k]));
        float pk_db = fast_lin_to_db(pk);
        env += (pk_db - env) * (pk_db > env ? att : rel);
        float gr = env > thr ? (env - thr) * slope : 0;
        float g1 = gr > 0 ? fast_db_to_lin(-gr) : 1.0f, dg = (g1 - cg) * (1.0f / DRC_BLOCK);
        for (int k = 0; k < DRC_BLOCK; k++) {
            cg += dg;
            float x = mix[n0 + k] * cg;
            float a = fabsf(x);
            if (a * lg > ceil)
                lg = ceil * sat_recipf(a);
            else
                lg += (1 - lg) * lrel;
            x *= lg;
            energy += x * x;
            /* round half away from zero with a truncating conversion (lrintf
             * is a library call) */
            float y = clampf(x, -1, 1) * 32767.0f;
            out[n0 + k] = (int16_t)(int)(y + (y >= 0 ? 0.5f : -0.5f));
        }
        cg = g1;
    }
    comp_env_db = env;
    comp_gain = cg;
    lim_gain = lg;
    out_db = fast_pow_to_db(energy / OUT_PERIOD);
}
