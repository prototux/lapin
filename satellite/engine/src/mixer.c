/*
 * Output mixer and DSP.
 *
 * Streams arrive from the server (TTS, media, alerts) as chunks of PCM and
 * are queued per stream (jitter buffer), optionally started at a given wall
 * clock time (multi-room sync), resampled to 48 kHz and mixed with per-kind
 * priority: media is ducked while the assistant listens / talks, TTS under
 * alarms. Earcons are synthesized locally so they play with no latency.
 *
 * Output chain: master volume -> high-pass -> 3-band EQ -> compressor ->
 * peak limiter -> s16. Keeping the amplifier out of clipping keeps the echo
 * path linear, which the AEC needs.
 */
#include "mixer.h"

#include <pthread.h>
#include <stdlib.h>
#include <string.h>

#include "resample.h"

#define MAX_STREAMS 8
#define MAX_QUEUED  (24u * 1024 * 1024)

typedef struct chunk {
    struct chunk *next;
    int n, pos;             /* samples (interleaved) */
    int16_t d[];
} chunk_t;

typedef struct {
    int used;
    uint32_t id;
    int kind, rate, ch;
    interp_t ip;
    chunk_t *head, *tail;
    size_t bytes;
    int closed, started, paused, stopping, in_underrun;
    int64_t start_at;
    int prebuf;             /* input frames */
    int64_t frames_in, frames_out;
    float gain_db, cur;
    /* earcon source */
    const float *ec;
    int ec_len, ec_pos, ec_loop;
} stream_t;

typedef struct {
    float b0, b1, b2, a1, a2;
    float z1[OUT_CH], z2[OUT_CH];
} biquad_t;

static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static stream_t streams[MAX_STREAMS];
static mixer_cfg_t cfg;
static biquad_t eq[4];          /* hpf, low shelf, peak, high shelf */
static int eq_dirty = 1;
static float master_cur, comp_env_db, lim_gain = 1;
static int duck_flag;
static float out_db = -120;
static size_t queued;
static uint32_t ec_seq = 0xE0000000u;

static mixer_event_t evq[128];
static int evq_head, evq_tail;

static float *earcon[EC_COUNT];
static int earcon_len[EC_COUNT];

static void push_event(uint32_t id, int kind, int event, int64_t frames)
{
    int next = (evq_head + 1) % 128;
    if (next == evq_tail)
        return;
    evq[evq_head] = (mixer_event_t){id, kind, event, frames};
    evq_head = next;
}

int mixer_poll_event(mixer_event_t *ev)
{
    pthread_mutex_lock(&lock);
    int ok = evq_tail != evq_head;
    if (ok) {
        *ev = evq[evq_tail];
        evq_tail = (evq_tail + 1) % 128;
    }
    pthread_mutex_unlock(&lock);
    return ok;
}

/* ------------------------------------------------------------ Earcons --- */

typedef struct {
    float t, f, dur, amp;
} note_t;

static void synth(int which, const note_t *notes, int n, float total)
{
    int len = (int)(total * OUT_RATE);
    float *buf = calloc((size_t)len, sizeof(float));
    for (int i = 0; i < n; i++) {
        const note_t *nt = &notes[i];
        int s0 = (int)(nt->t * OUT_RATE), ns = (int)(nt->dur * OUT_RATE);
        for (int k = 0; k < ns && s0 + k < len; k++) {
            float t = (float)k / OUT_RATE;
            float att = fminf(1.0f, t / 0.004f);                 /* 4 ms attack */
            float dec = expf(-t / (nt->dur * 0.35f));
            float rel = fminf(1.0f, (nt->dur - t) / 0.01f);
            float ph = TAU_F * nt->f * t;
            /* sine with a touch of 2nd and 3rd harmonics: soft, bell-like */
            float v = sinf(ph) + 0.18f * sinf(2 * ph) * expf(-t / 0.05f) + 0.06f * sinf(3 * ph);
            buf[s0 + k] += nt->amp * att * dec * rel * v;
        }
    }
    earcon[which] = buf;
    earcon_len[which] = len;
}

static void make_earcons(void)
{
    const float a = 0.22f;
    synth(EC_WAKE, (note_t[]){{0, 880, 0.18f, a}, {0.075f, 1318.5f, 0.28f, a}}, 2, 0.4f);
    synth(EC_END, (note_t[]){{0, 1318.5f, 0.14f, a * 0.6f}, {0.07f, 880, 0.22f, a * 0.6f}}, 2, 0.32f);
    synth(EC_ERROR, (note_t[]){{0, 415.3f, 0.2f, a}, {0.16f, 311.1f, 0.32f, a}}, 2, 0.5f);
    synth(EC_ALARM, (note_t[]){{0, 1046.5f, 0.12f, a * 1.3f}, {0.18f, 1046.5f, 0.12f, a * 1.3f},
                               {0.36f, 1046.5f, 0.12f, a * 1.3f}, {0.54f, 1318.5f, 0.3f, a * 1.3f}},
          4, 1.4f);
    synth(EC_NOTIFY, (note_t[]){{0, 659.3f, 0.2f, a}, {0.09f, 880, 0.2f, a}, {0.18f, 1174.7f, 0.35f, a}},
          3, 0.6f);
    synth(EC_MUTE_ON, (note_t[]){{0, 659.3f, 0.12f, a}, {0.08f, 440, 0.2f, a}}, 2, 0.32f);
    synth(EC_MUTE_OFF, (note_t[]){{0, 440, 0.12f, a}, {0.08f, 659.3f, 0.2f, a}}, 2, 0.32f);
    synth(EC_VOLUME, (note_t[]){{0, 1174.7f, 0.06f, a * 0.7f}}, 1, 0.08f);
    /* action done: two quick soft notes, rising a fifth */
    synth(EC_DONE, (note_t[]){{0, 987.8f, 0.09f, a * 0.75f}, {0.07f, 1480.0f, 0.16f, a * 0.75f}}, 2, 0.25f);
    synth(EC_OFFLINE, (note_t[]){{0, 659.3f, 0.16f, a}, {0.14f, 523.3f, 0.16f, a}, {0.28f, 392, 0.3f, a}},
          3, 0.62f);
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
        free(c);
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

void mixer_init(void)
{
    cfg = (mixer_cfg_t){.volume = 60, .duck_db = -18, .listen_duck_db = -40, .gain_db = {0, 0, -4, 0},
                        .hpf_hz = 90, .drc_threshold_db = -14, .drc_ratio = 3,
                        .limiter_db = -1, .earcons = 1};
    make_earcons();
}

void mixer_set_cfg(const mixer_cfg_t *c)
{
    pthread_mutex_lock(&lock);
    cfg = *c;
    eq_dirty = 1;
    pthread_mutex_unlock(&lock);
}

void mixer_get_cfg(mixer_cfg_t *c)
{
    pthread_mutex_lock(&lock);
    *c = cfg;
    pthread_mutex_unlock(&lock);
}

int mixer_open(uint32_t id, int kind, int rate, int channels, int64_t start_at_ns,
               float gain_db, int prebuffer_ms)
{
    if ((rate != 16000 && rate != 24000 && rate != 48000) || channels < 1 || channels > 2 ||
        kind < 0 || kind > SK_ALARM)
        return -1;
    pthread_mutex_lock(&lock);
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
    pthread_mutex_unlock(&lock);
    return 0;
}

int mixer_write(uint32_t id, const int16_t *pcm, int samples)
{
    if (samples <= 0)
        return 0;
    chunk_t *c = malloc(sizeof(chunk_t) + (size_t)samples * 2);
    if (!c)
        return -1;
    c->next = NULL;
    c->n = samples;
    c->pos = 0;
    memcpy(c->d, pcm, (size_t)samples * 2);
    pthread_mutex_lock(&lock);
    stream_t *s = find(id);
    if (!s || s->closed || queued + (size_t)samples * 2 > MAX_QUEUED) {
        if (s && !s->closed)
            push_event(id, s->kind, MIXEV_OVERFLOW, s->frames_out);
        pthread_mutex_unlock(&lock);
        free(c);
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
    pthread_mutex_unlock(&lock);
    return 0;
}

void mixer_close(uint32_t id, int drain)
{
    pthread_mutex_lock(&lock);
    stream_t *s = find(id);
    if (s) {
        if (drain)
            s->closed = 1;
        else
            s->stopping = 1;
    }
    pthread_mutex_unlock(&lock);
}

void mixer_pause(uint32_t id, int paused)
{
    pthread_mutex_lock(&lock);
    stream_t *s = find(id);
    if (s)
        s->paused = paused;
    pthread_mutex_unlock(&lock);
}

void mixer_stop_kinds(int mask)
{
    pthread_mutex_lock(&lock);
    for (int i = 0; i < MAX_STREAMS; i++)
        if (streams[i].used && (mask & (1 << streams[i].kind)))
            streams[i].stopping = 1;
    pthread_mutex_unlock(&lock);
}

void mixer_earcon(int which, int loop)
{
    if (which < 0 || which >= EC_COUNT)
        return;
    pthread_mutex_lock(&lock);
    if (!cfg.earcons && which != EC_ALARM) {
        pthread_mutex_unlock(&lock);
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
    s->rate = OUT_RATE;
    s->ch = 1;
    interp_init(&s->ip, 1);
    s->ec = earcon[which];
    s->ec_len = earcon_len[which];
    s->ec_loop = loop;
    s->closed = 1;
    s->cur = 1;
    pthread_mutex_unlock(&lock);
}

int mixer_active_kinds(void)
{
    int mask = 0;
    pthread_mutex_lock(&lock);
    for (int i = 0; i < MAX_STREAMS; i++)
        if (streams[i].used && !streams[i].paused)
            mask |= 1 << streams[i].kind;
    pthread_mutex_unlock(&lock);
    return mask;
}

void mixer_set_duck(int mode) { duck_flag = mode; }
float mixer_out_db(void) { return out_db; }

size_t mixer_queued_bytes(void)
{
    pthread_mutex_lock(&lock);
    size_t q = queued;
    pthread_mutex_unlock(&lock);
    return q;
}

/* Next input frame of a stream into `in`; 0 if none available. */
static int pop_frame(stream_t *s, float *in)
{
    if (s->ec) {
        if (s->ec_pos >= s->ec_len) {
            if (!s->ec_loop)
                return 0;
            s->ec_pos = 0;
        }
        in[0] = s->ec[s->ec_pos++];
        return 1;
    }
    chunk_t *c = s->head;
    while (c && c->pos + s->ch > c->n) {
        s->head = c->next;
        if (!s->head)
            s->tail = NULL;
        s->bytes -= (size_t)c->n * 2;
        queued -= (size_t)c->n * 2;
        free(c);
        c = s->head;
    }
    if (!c)
        return 0;
    for (int k = 0; k < s->ch; k++)
        in[k] = c->d[c->pos + k] * (1.0f / 32768.0f);
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

static inline float biquad_run(biquad_t *b, int c, float x)
{
    float y = b->b0 * x + b->z1[c];
    b->z1[c] = b->b1 * x - b->a1 * y + b->z2[c];
    b->z2[c] = b->b2 * x - b->a2 * y;
    return y;
}

void mixer_render(int16_t *out, int64_t output_latency_ns)
{
    float mix[OUT_PERIOD][OUT_CH];
    memset(mix, 0, sizeof mix);
    pthread_mutex_lock(&lock);

    if (eq_dirty) {
        biquad_set(&eq[0], 0, fmaxf(cfg.hpf_hz, 20), 0.707f, 0);
        biquad_set(&eq[1], 1, 150, 0.7f, cfg.eq_low_db);
        biquad_set(&eq[2], 2, 1200, 0.9f, cfg.eq_mid_db);
        biquad_set(&eq[3], 3, 6000, 0.7f, cfg.eq_high_db);
        eq_dirty = 0;
    }

    int active = 0;
    for (int i = 0; i < MAX_STREAMS; i++)
        if (streams[i].used && streams[i].started && !streams[i].paused)
            active |= 1 << streams[i].kind;
    const int64_t now = real_ns();
    const float fast = 1 - expf(-1.0f / (0.012f * OUT_RATE));
    const float slow = 1 - expf(-1.0f / (0.30f * OUT_RATE));

    for (int i = 0; i < MAX_STREAMS; i++) {
        stream_t *s = &streams[i];
        if (!s->used || s->paused)
            continue;
        if (!s->started) {
            int64_t avail = available_frames(s);
            if (!s->closed && avail < s->prebuf)
                continue;
            if (s->start_at && now + output_latency_ns < s->start_at)
                continue;
            if (avail <= 0 && s->closed) {
                push_event(s->id, s->kind, MIXEV_FINISHED, 0);
                free_stream(s);
                continue;
            }
            /* late for a synchronized start: drop what should have played */
            if (s->start_at && !s->ec) {
                int64_t late = now + output_latency_ns - s->start_at;
                int64_t skip = late > 30000000LL ? late * s->rate / 1000000000LL : 0;
                float tmp[2];
                while (skip-- > 0 && pop_frame(s, tmp))
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
        float target = s->stopping ? 0 : db_to_lin(s->gain_db + cfg.gain_db[s->kind] + duck);
        float coef = target < s->cur ? fast : slow;
        if (s->cur < 0)
            s->cur = target;

        int done = 0;
        const int L = s->ip.factor;
        for (int n = 0; n < OUT_PERIOD; n++) {
            if (s->ip.phase == 0) {
                float in[2];
                if (!pop_frame(s, in)) {
                    if (s->closed) {
                        done = 1;
                        break;
                    }
                    if (!s->in_underrun) {
                        s->in_underrun = 1;
                        push_event(s->id, s->kind, MIXEV_UNDERRUN, s->frames_out);
                    }
                    break;
                }
                s->in_underrun = 0;
                interp_push(&s->ip, s->ch, in);
            }
            float o[2];
            interp_output(&s->ip, s->ch, o);
            s->ip.phase = (s->ip.phase + 1) % L;
            s->frames_out++;
            s->cur += (target - s->cur) * coef;
            if (s->ch == 1) {
                mix[n][0] += o[0] * s->cur;
                mix[n][1] += o[0] * s->cur;
            } else {
                mix[n][0] += o[0] * s->cur;
                mix[n][1] += o[1] * s->cur;
            }
        }
        if (done) {
            push_event(s->id, s->kind, MIXEV_FINISHED, s->frames_out);
            free_stream(s);
        } else if (s->stopping && s->cur < 0.001f) {
            push_event(s->id, s->kind, MIXEV_STOPPED, s->frames_out);
            free_stream(s);
        }
    }
    mixer_cfg_t c = cfg;
    pthread_mutex_unlock(&lock);

    /* master volume, perceptual taper */
    float v = c.muted ? 0 : clampf(c.volume, 0, 100) / 100.0f;
    float master = v * v;
    const float thr = c.drc_threshold_db, ratio = fmaxf(c.drc_ratio, 1);
    const float ceil = db_to_lin(c.limiter_db);
    const float att = 1 - expf(-1.0f / (0.005f * OUT_RATE)), rel = 1 - expf(-1.0f / (0.15f * OUT_RATE));
    const float lrel = 1 - expf(-1.0f / (0.08f * OUT_RATE));
    double energy = 0;
    for (int n = 0; n < OUT_PERIOD; n++) {
        master_cur += (master - master_cur) * 0.002f;
        float l = mix[n][0] * master_cur, r = mix[n][1] * master_cur;
        for (int b = 0; b < 4; b++) {
            if (b == 0 && c.hpf_hz <= 0)
                continue;
            l = biquad_run(&eq[b], 0, l);
            r = biquad_run(&eq[b], 1, r);
        }
        /* compressor on the peak envelope */
        float pk_db = lin_to_db(fmaxf(fabsf(l), fabsf(r)));
        comp_env_db += (pk_db - comp_env_db) * (pk_db > comp_env_db ? att : rel);
        float gr = comp_env_db > thr ? (comp_env_db - thr) * (1 - 1 / ratio) : 0;
        float g = db_to_lin(-gr);
        l *= g;
        r *= g;
        /* peak limiter: instant attack, smooth release */
        float pk = fmaxf(fabsf(l), fabsf(r));
        if (pk * lim_gain > ceil)
            lim_gain = ceil / pk;
        else
            lim_gain += (1 - lim_gain) * lrel;
        l *= lim_gain;
        r *= lim_gain;
        energy += 0.5 * ((double)l * l + (double)r * r);
        out[2 * n] = (int16_t)lrintf(clampf(l, -1, 1) * 32767.0f);
        out[2 * n + 1] = (int16_t)lrintf(clampf(r, -1, 1) * 32767.0f);
    }
    out_db = pow_to_db((float)(energy / OUT_PERIOD));
}
