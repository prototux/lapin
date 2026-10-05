/*
 * Integer-factor polyphase interpolator to 48 kHz (factor 1, 2 or 3):
 * 24 kHz speech -> x2, 16 kHz -> x3, 48 kHz music passes through.
 * From the ReSpeaker satellite (satellite/engine/src/resample.c); the
 * 48 -> 16 kHz decimator is not needed here (the ES7210 runs at 16 kHz).
 */
#ifndef SAT_RESAMPLE_H
#define SAT_RESAMPLE_H

#include "sat_common.h"

#define INT_TAPS 16               /* taps per phase (32 overloaded the ESP32 during playback) */
typedef struct {
    int factor;
    int phase;
    int pos;                            /* newest sample in ring[c][pos] */
    float ring[OUT_CH][2 * INT_TAPS];   /* doubled ring: no memmove per sample */
} interp_t;

void resample_init(void);
void interp_init(interp_t *s, int factor);
extern float interp_h[4][INT_TAPS * 3];   /* [factor][taps] prototype, factor 2 and 3 */
/* the same, split per phase: interp_hp[factor][phase][t] = interp_h[factor][phase + t * factor] */
extern float interp_hp[4][3][INT_TAPS];

/* Pushes one input frame (channels samples). Inline: called per sample. */
static inline void interp_push(interp_t *s, int channels, const float *in)
{
    s->pos = s->pos ? s->pos - 1 : INT_TAPS - 1;
    for (int c = 0; c < channels; c++) {
        s->ring[c][s->pos] = in[c];
        s->ring[c][s->pos + INT_TAPS] = in[c];
    }
}

/* Produces one output sample per channel for the current phase from the history. */
static inline void interp_output(const interp_t *s, int channels, float *out)
{
    const int L = s->factor;
    if (L == 1) {
        for (int c = 0; c < channels; c++)
            out[c] = s->ring[c][s->pos];
        return;
    }
    const float *h = interp_h[L] + s->phase;
    for (int c = 0; c < channels; c++) {
        const float *w = s->ring[c] + s->pos;   /* w[0] newest */
        float acc = 0;
        for (int t = 0; t < INT_TAPS; t++)
            acc += h[t * L] * w[t];
        out[c] = acc;
    }
}

/* Mono output for an explicit phase (the render loop keeps the phase in a
 * register). */
static inline float interp_out1(const interp_t *s, int phase)
{
    const int L = s->factor;
    const float *w = s->ring[0] + s->pos;
    if (L == 1)
        return w[0];
    const float *h = interp_hp[L][phase];      /* contiguous: sequential loads */
    float acc = 0;
#if INT_TAPS == 16
    acc = h[0] * w[0] + h[1] * w[1] + h[2] * w[2] + h[3] * w[3] + h[4] * w[4] + h[5] * w[5] + h[6] * w[6] +
          h[7] * w[7] + h[8] * w[8] + h[9] * w[9] + h[10] * w[10] + h[11] * w[11] + h[12] * w[12] +
          h[13] * w[13] + h[14] * w[14] + h[15] * w[15];
#else
    for (int t = 0; t < INT_TAPS; t++)
        acc += h[t] * w[t];
#endif
    return acc;
}

#endif
