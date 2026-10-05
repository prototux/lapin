/*
 * LED ring animations: port of satellite/agent/satagent/plugins/leds/anim.py.
 * Positions are in LED units (float, wrapping), LED 0 at the top, clockwise.
 */
#include "anim.h"

#include <math.h>
#include <string.h>

#define N ANIM_N
#define TAU 6.28318530718f

static const rgb_t PAL[4] = {{0.00f, 0.80f, 1.00f}, {0.20f, 0.35f, 1.00f},
                             {0.62f, 0.22f, 1.00f}, {1.00f, 0.30f, 0.62f}};   /* "aurora" */
static const rgb_t BLACK = {0, 0, 0}, WHITE = {1, 1, 1}, RED = {1.0f, 0.07f, 0.04f},
                   AMBER = {1.0f, 0.50f, 0.04f}, WARM = {1.0f, 0.38f, 0.04f}, GREEN = {0.10f, 1.0f, 0.45f};

static float clamp01(float x) { return x < 0 ? 0 : x > 1 ? 1 : x; }
static float smooth(float x)
{
    x = clamp01(x);
    return x * x * (3 - 2 * x);
}
static float ease_out(float x)
{
    x = clamp01(x);
    return 1 - (1 - x) * (1 - x) * (1 - x);
}
static rgb_t mix(rgb_t a, rgb_t b, float k) { return (rgb_t){a.r + (b.r - a.r) * k, a.g + (b.g - a.g) * k, a.b + (b.b - a.b) * k}; }
static rgb_t scale(rgb_t c, float k) { return (rgb_t){c.r * k, c.g * k, c.b * k}; }
static rgb_t add(rgb_t a, rgb_t b) { return (rgb_t){a.r + b.r, a.g + b.g, a.b + b.b}; }
static float wrapn(float a)
{
    a = fmodf(a, N);
    return a < 0 ? a + N : a;
}
static float cdist(float a, float b)
{
    float d = wrapn(a - b);
    return d < N - d ? d : N - d;
}
static float signedd(float a, float b)
{
    float d = wrapn(a - b);
    return d > N / 2.0f ? d - N : d;
}
static float gauss(float d, float s) { return expf(-0.5f * (d / s) * (d / s)); }
static rgb_t pal_at(float x)
{
    x = (x - floorf(x)) * 4;
    int i = (int)x;
    return mix(PAL[i % 4], PAL[(i + 1) % 4], smooth(x - i));
}

/* ---------------------------------------------------------------- bases */

static void b_idle(float t, const anim_ctx_t *c, rgb_t *px)
{
    for (int i = 0; i < N; i++)
        px[i] = c->idle_breathe ? scale(pal_at(i / (float)N + t * 0.02f),
                                        0.035f + 0.025f * sinf(TAU * t / 6.0f))
                                : BLACK;
}

/* No talker direction on this board: the whole ring breathes, brightens with
 * the voice, and two sparks orbit slowly. */
static void b_listening(float t, const anim_ctx_t *c, rgb_t *px)
{
    float breathe = 0.5f + 0.5f * sinf(TAU * t * 0.35f);
    float amp = 0.40f + 0.12f * breathe + 0.48f * c->level;
    for (int i = 0; i < N; i++)
        px[i] = scale(mix(PAL[0], PAL[1], 0.5f + 0.5f * sinf(TAU * (i / (float)N + t * 0.08f))), amp);
    for (int s = 0; s < 2; s++) {
        float pos = N * (t * 0.12f + s * 0.5f);
        for (int i = 0; i < N; i++)
            px[i] = add(px[i], scale(PAL[2], gauss(cdist(i, pos), 0.6f) * (0.25f + 0.2f * breathe)));
    }
}

static void b_thinking(float t, const anim_ctx_t *c, rgb_t *px)
{
    (void)c;
    float phase = t * 0.62f + 0.16f * sinf(TAU * t * 0.42f);
    float spread = 1.0f + 0.32f * sinf(TAU * t * 0.55f);
    for (int i = 0; i < N; i++)
        px[i] = BLACK;
    for (int k = 0; k < 4; k++) {
        float pos = N * phase + (k - 1.5f) * (N / 4.0f) * spread;
        for (int i = 0; i < N; i++) {
            float d = signedd(i, pos), w = gauss(d, 0.55f);
            if (d < 0)                      /* tail behind the orb */
                w = fmaxf(w, 0.55f * expf(d / 1.1f));
            px[i] = add(px[i], scale(PAL[k], 0.85f * w));
        }
    }
}

static void b_speaking(float t, const anim_ctx_t *c, rgb_t *px)
{
    float e = c->out;
    for (int i = 0; i < N; i++) {
        float amp = 0.16f + 0.84f * e * 0.8f + 0.04f * sinf(TAU * (t * 0.6f + i / (float)N));
        px[i] = scale(pal_at(i / (float)N + t * 0.11f), clamp01(amp));
    }
}

static void b_muted(float t, const anim_ctx_t *c, rgb_t *px)
{
    (void)c;
    float k = 0.20f + 0.05f * sinf(TAU * t / 4.0f);
    for (int i = 0; i < N; i++)
        px[i] = scale(RED, k);
}

static void b_offline(float t, const anim_ctx_t *c, rgb_t *px)
{
    (void)c;
    float f = fmodf(t / 3.2f, 1.0f);
    float pos = N * (f + 0.5f * (smooth(f) - f));
    for (int i = 0; i < N; i++) {
        float d = signedd(i, pos);
        float w = d >= 0 ? gauss(d, 0.6f) : fmaxf(gauss(d, 0.6f), 0.6f * expf(d / 1.4f));
        px[i] = scale(AMBER, 0.45f * w);
    }
}

static void b_pending(float t, const anim_ctx_t *c, rgb_t *px)
{
    (void)c;
    float k = 0.35f + 0.15f * sinf(TAU * t * 0.5f);
    for (int i = 0; i < N; i++)
        px[i] = BLACK;
    for (int j = 0; j < 2; j++) {
        float pos = N * (t * 0.22f + j * 0.5f);
        for (int i = 0; i < N; i++)
            px[i] = add(px[i], scale(PAL[1], k * gauss(cdist(i, pos), 0.7f)));
    }
}

static void b_alarm(float t, const anim_ctx_t *c, rgb_t *px)
{
    (void)c;
    float pulse = 0.5f + 0.5f * sinf(TAU * t * 1.3f), hi = N * t * 0.7f;
    for (int i = 0; i < N; i++)
        px[i] = add(scale(WARM, 0.25f + 0.55f * pulse), scale(WHITE, 0.35f * gauss(cdist(i, hi), 0.9f)));
}

static void b_notify(float t, const anim_ctx_t *c, rgb_t *px)
{
    (void)c;
    float k = 0.15f + 0.45f * (0.5f + 0.5f * sinf(TAU * t * 0.5f));
    for (int i = 0; i < N; i++)
        px[i] = scale(PAL[0], k * gauss(cdist(i, 0), 1.8f));
}

/* Wi-Fi setup (access point open): a slow white/blue chase. */
static void b_setup(float t, const anim_ctx_t *c, rgb_t *px)
{
    (void)c;
    for (int i = 0; i < N; i++) {
        float w = gauss(cdist(i, N * fmodf(t * 0.25f, 1.0f)), 1.2f);
        px[i] = add(scale(PAL[1], 0.12f), scale(WHITE, 0.5f * w));
    }
}

/* Firmware update: an amber arc grows with the progress, a white tip moves. */
static void b_update(float t, const anim_ctx_t *c, rgb_t *px)
{
    float fill = clamp01(c->progress) * N;
    for (int i = 0; i < N; i++) {
        float k = clamp01(fill - i);
        px[i] = add(scale(AMBER, 0.08f + 0.4f * k), scale(WHITE, 0.4f * gauss(cdist(i, fill), 0.6f) *
                                                               (0.6f + 0.4f * sinf(TAU * t))));
    }
}

typedef void (*base_fn)(float, const anim_ctx_t *, rgb_t *);
static const base_fn BASES[AB_COUNT] = {b_idle, b_listening, b_thinking, b_speaking, b_muted,
                                        b_offline, b_pending, b_alarm, b_notify, b_setup, b_update};

/* ------------------------------------------------------------- overlays */
/* Each returns 0 when finished; sets alpha and mode (1 add, 0 over). */

static int o_wake(float t, float arg, rgb_t *px, float *alpha, int *add_mode)
{
    (void)arg;
    if (t > 0.8f)
        return 0;
    float front = (N / 2.0f) * ease_out(t / 0.6f), fade = 1 - smooth(t / 0.8f), flash = fmaxf(0, 1 - t / 0.3f);
    for (int i = 0; i < N; i++) {
        float d = cdist(i, 0);
        float wave = gauss(d - front, 0.75f) * fade;
        rgb_t col = mix(WHITE, pal_at(d / N), 0.35f + 0.65f * (d / (N / 2.0f)));
        px[i] = add(scale(col, 0.9f * wave), scale(WHITE, 0.8f * flash * gauss(d, 0.9f)));
    }
    *alpha = 1;
    *add_mode = 1;
    return 1;
}

static int o_volume(float t, float arg, rgb_t *px, float *alpha, int *add_mode)
{
    if (t > 2.0f)
        return 0;
    float v = clamp01(arg / 100), fill = v * N, grow = ease_out(t / 0.25f);
    for (int i = 0; i < N; i++) {
        float pos = (float)i, k = clamp01(fill * grow - pos);
        rgb_t col = scale(mix(PAL[1], PAL[0], pos / N), 0.18f + 0.5f * k);
        if (k <= 0)
            col = scale(PAL[2], 0.04f);
        float tip = gauss(pos - (fill * grow - 0.5f), 0.5f) * 0.6f;
        px[i] = add(col, scale(WHITE, v > 0 ? tip : 0));
    }
    *alpha = 1 - smooth((t - 1.5f) / 0.5f);
    *add_mode = 0;
    return 1;
}

static int o_mute(float t, float arg, rgb_t *px, float *alpha, int *add_mode)
{
    if (t > 0.7f)
        return 0;
    float f = ease_out(t / 0.5f);
    for (int i = 0; i < N; i++) {
        float pos = i / (float)N;
        int lit = arg != 0 ? pos <= f : pos > f;
        px[i] = scale(RED, lit ? 0.6f : 0.0f);
    }
    *alpha = 1 - smooth((t - 0.5f) / 0.2f);
    *add_mode = 0;
    return 1;
}

static int o_error(float t, float arg, rgb_t *px, float *alpha, int *add_mode)
{
    (void)arg;
    if (t > 0.8f)
        return 0;
    float a = t < 0.35f ? fmaxf(0, sinf(3.14159265f * t / 0.35f))
              : t > 0.42f ? fmaxf(0, sinf(3.14159265f * (t - 0.42f) / 0.35f)) : 0;
    for (int i = 0; i < N; i++)
        px[i] = scale(RED, 0.7f);
    *alpha = a;
    *add_mode = 0;
    return 1;
}

static int o_success(float t, float arg, rgb_t *px, float *alpha, int *add_mode)
{
    (void)arg;
    if (t > 0.9f)
        return 0;
    for (int i = 0; i < N; i++)
        px[i] = scale(GREEN, 0.55f);
    *alpha = sinf(3.14159265f * t / 0.9f);
    *add_mode = 1;
    return 1;
}

/* Boot: a white sweep around the ring (the device is alive), then it lets go. */
static int o_boot(float t, float arg, rgb_t *px, float *alpha, int *add_mode)
{
    (void)arg;
    if (t > 2.0f)
        return 0;
    float head = ease_out(t / 1.2f) * (N + 2);
    for (int i = 0; i < N; i++) {
        float d = head - i;             /* > 0: already swept */
        float lit = d > 0 ? 0.35f + 0.65f * expf(-d / 1.5f) : gauss(d, 0.5f);
        px[i] = scale(WHITE, 0.8f * lit);
    }
    *alpha = 1 - smooth((t - 1.3f) / 0.7f);
    *add_mode = 0;
    return 1;
}

typedef int (*ov_fn)(float, float, rgb_t *, float *, int *);
static const ov_fn OVS[AO_COUNT] = {o_wake, o_volume, o_mute, o_error, o_success, o_boot};

/* ------------------------------------------------------------- animator */

#define FADE 0.4f

static struct {
    int base, prev;
    float base_t, prev_t, fade;
    int ov_on[AO_COUNT];
    float ov_t[AO_COUNT], ov_arg[AO_COUNT];
    float level, out;
} A;

void anim_init(void)
{
    memset(&A, 0, sizeof A);
    A.prev = -1;
    A.fade = 1;
}

void anim_set_base(int b)
{
    if (b == A.base || b < 0 || b >= AB_COUNT)
        return;
    A.prev = A.base;
    A.prev_t = A.base_t;
    A.base = b;
    A.base_t = 0;
    A.fade = 0;
}

int anim_get_base(void) { return A.base; }

void anim_overlay(int ov, float arg)
{
    if (ov < 0 || ov >= AO_COUNT)
        return;
    A.ov_on[ov] = 1;
    A.ov_t[ov] = 0;
    A.ov_arg[ov] = arg;
}

void anim_render(float dt, const anim_ctx_t *c_in, rgb_t out[N])
{
    /* smooth the inputs (they come at the audio rate, jittery) */
    float k = c_in->level > A.level ? 0.06f : 0.25f;
    A.level += (c_in->level - A.level) * (1 - expf(-dt / k));
    k = c_in->out > A.out ? 0.03f : 0.18f;
    A.out += (c_in->out - A.out) * (1 - expf(-dt / k));
    anim_ctx_t c = *c_in;
    c.level = A.level;
    c.out = A.out;

    rgb_t frame[N], tmp[N];
    A.base_t += dt;
    BASES[A.base](A.base_t, &c, frame);
    if (A.fade < 1 && A.prev >= 0) {
        A.prev_t += dt;
        A.fade = fminf(1, A.fade + dt / FADE);
        BASES[A.prev](A.prev_t, &c, tmp);
        float s = smooth(A.fade);
        for (int i = 0; i < N; i++)
            frame[i] = mix(tmp[i], frame[i], s);
    }
    for (int o = 0; o < AO_COUNT; o++) {
        if (!A.ov_on[o])
            continue;
        A.ov_t[o] += dt;
        float alpha;
        int add_mode;
        if (!OVS[o](A.ov_t[o], A.ov_arg[o], tmp, &alpha, &add_mode)) {
            A.ov_on[o] = 0;
            continue;
        }
        for (int i = 0; i < N; i++)
            frame[i] = add_mode ? add(frame[i], scale(tmp[i], alpha)) : mix(frame[i], tmp[i], alpha);
    }
    for (int i = 0; i < N; i++)
        out[i] = (rgb_t){clamp01(frame[i].r), clamp01(frame[i].g), clamp01(frame[i].b)};
}
