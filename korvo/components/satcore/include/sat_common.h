/*
 * Portable core of the Korvo satellite firmware: shared constants and small
 * helpers. Everything under components/satcore builds both for the ESP32
 * (ESP-IDF) and on a PC (host unit tests); platform services come from
 * sat_port.h.
 *
 * Adapted from the ReSpeaker satellite engine (satellite/engine/src/common.h).
 */
#ifndef SAT_COMMON_H
#define SAT_COMMON_H

#include <complex.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>

#include "sat_port.h"

/* one source of truth: the build's project version (sdkconfig) on the ESP32 */
#if defined(ESP_PLATFORM)
#include "sdkconfig.h"
#define SAT_VERSION CONFIG_APP_PROJECT_VER
#else
#define SAT_VERSION "host"
#endif

/* Processing domain: 16 kHz, 16 ms hops, 32 ms FFT frames (50 % overlap). */
#define SR          16000
#define HOP         256
#define NFFT        512
#define NBIN        (NFFT / 2 + 1)

/* Output: 48 kHz, mixed in stereo, sent mono to the ES8311 (mono DAC). */
#define OUT_RATE    48000
#define OUT_CH      2
#define OUT_PERIOD  480         /* 10 ms */

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif
#define TAU_F ((float)(2.0 * M_PI))

typedef float complex cfloat;

static inline float clampf(float x, float lo, float hi)
{
    return x < lo ? lo : x > hi ? hi : x;
}

/* min / max without libm calls (no NaN handling needed here) */
static inline float minf(float a, float b) { return a < b ? a : b; }
static inline float maxf(float a, float b) { return a > b ? a : b; }

/* 1 / x for positive normal x. The ESP32's FPU has no divider (a float
 * division is a ~100-instruction library call): seed with RECIP0.S and two
 * Newton steps (within 1-2 ulp of 1.0f / x). */
static inline float sat_recipf(float x)
{
#if defined(__XTENSA__) && defined(__XTENSA_HARD_FLOAT__)
    float r;
    __asm__("recip0.s %0, %1" : "=f"(r) : "f"(x));
    r = r * (2.0f - x * r);
    r = r * (2.0f - x * r);
    return r;
#else
    return 1.0f / x;
#endif
}

/* log2 / exp2 by exponent bits + polynomial (max error ~1e-4 in log2,
 * ~2e-5 relative in exp2): for level meters, compressors, gains, where libm's
 * powf / log10f (software, ~1-3k instructions each on the ESP32) are far too
 * slow to call per block. */
static inline float fast_log2f(float x)
{
    union {
        float f;
        uint32_t i;
    } v = {x};
    float e = (float)(int)((v.i >> 23) & 255) - 127.0f;
    v.i = (v.i & 0x007FFFFFu) | 0x3F800000u;           /* mantissa in [1, 2) */
    float m = v.f;
    /* ln(m) on [1, 2), times 1 / ln 2 */
    return e + 1.4426950f * (-1.7417939f + (2.8212026f + (-1.4699568f + (0.44717955f - 0.056570851f * m) * m) * m) * m);
}

static inline float fast_exp2f(float x)
{
    if (x < -126.0f)
        return 0.0f;
    if (x > 126.0f)
        x = 126.0f;
    float fl = floorf(x), f = x - fl;
    float p = 1.0f + f * (0.69314718f + f * (0.24022651f + f * (0.05550411f + f * (0.00961813f + f * 0.00133336f))));
    union {
        float f;
        uint32_t i;
    } v = {p};
    v.i += (uint32_t)((int)fl) << 23;
    return v.f;
}

/* amplitude <-> dB, fast versions */
static inline float fast_lin_to_db(float x) { return 6.0205999f * fast_log2f(x + 1e-9f); }
static inline float fast_db_to_lin(float db) { return fast_exp2f(db * 0.16609640f); }
static inline float fast_pow_to_db(float p) { return 3.0103f * fast_log2f(p + 1e-12f); }

static inline float db_to_lin(float db) { return powf(10.0f, db / 20.0f); }
static inline float lin_to_db(float x) { return 20.0f * log10f(x + 1e-9f); }
static inline float pow_to_db(float p) { return 10.0f * log10f(p + 1e-12f); }

/* hot code (the speaker's render, the wake word DTW): in IRAM on the ESP32,
 * so it never waits for flash cache refills while the other core streams
 * PSRAM through the shared bus */
#if defined(ESP_PLATFORM)
#include "esp_attr.h"
#define SAT_HOT IRAM_ATTR
#else
#define SAT_HOT
#endif

#define LOGE(...) sat_log(0, __VA_ARGS__)
#define LOGI(...) sat_log(1, __VA_ARGS__)
#define LOGD(...) sat_log(2, __VA_ARGS__)

#endif
