/*
 * satd - satellite audio engine. Shared constants and small helpers.
 */
#ifndef SATD_COMMON_H
#define SATD_COMMON_H

#include <complex.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <time.h>

#define SATD_VERSION "1.0.0"

/* Processing domain: 16 kHz, 16 ms hops, 32 ms FFT frames (50 % overlap). */
#define SR          16000
#define HOP         256
#define NFFT        512
#define NBIN        (NFFT / 2 + 1)

/* Capture: 8 channels at 48 kHz (6 microphones + 2 loopback of the output). */
#define CAP_RATE    48000
#define CAP_CH      8
#define DECIM       (CAP_RATE / SR)
#define NMIC        6
#define REF_L       6
#define REF_R       7

/* Output: stereo 48 kHz. */
#define OUT_RATE    48000
#define OUT_CH      2
#define OUT_PERIOD  480         /* 10 ms */

/* Direction grid shared by DOA, steering and the tracker: 4 degree steps. */
#define NANG        90
#define ANG_STEP    (360.0f / NANG)

#define MAX_BEAMS   8
#define SOUND_SPEED 343.0f

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif
#define TAU_F ((float)(2.0 * M_PI))

typedef float complex cfloat;

static inline float clampf(float x, float lo, float hi)
{
    return x < lo ? lo : x > hi ? hi : x;
}

static inline float db_to_lin(float db) { return powf(10.0f, db / 20.0f); }

static inline float lin_to_db(float x) { return 20.0f * log10f(x + 1e-9f); }

static inline float pow_to_db(float p) { return 10.0f * log10f(p + 1e-12f); }

/* Wraps an angle in degrees to [0, 360). */
static inline float wrap360(float a)
{
    a = fmodf(a, 360.0f);
    return a < 0 ? a + 360.0f : a;
}

/* Signed difference a - b in degrees, in (-180, 180]. */
static inline float angdiff(float a, float b)
{
    float d = wrap360(a - b);
    return d > 180.0f ? d - 360.0f : d;
}

static inline int64_t now_ns(clockid_t clk)
{
    struct timespec ts;
    clock_gettime(clk, &ts);
    return (int64_t)ts.tv_sec * 1000000000LL + ts.tv_nsec;
}

static inline int64_t mono_ns(void) { return now_ns(CLOCK_MONOTONIC); }
static inline int64_t real_ns(void) { return now_ns(CLOCK_REALTIME); }

extern int g_verbose;
void logmsg(int level, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
#define LOGE(...) logmsg(0, __VA_ARGS__)
#define LOGI(...) logmsg(1, __VA_ARGS__)
#define LOGD(...) logmsg(2, __VA_ARGS__)

#endif
