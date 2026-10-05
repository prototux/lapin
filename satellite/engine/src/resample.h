#ifndef SATD_RESAMPLE_H
#define SATD_RESAMPLE_H

#include "common.h"

/* 48 kHz -> 16 kHz decimator for one channel (windowed-sinc FIR). */
#define DEC_TAPS 96
typedef struct {
    float hist[DEC_TAPS * 2];
    int pos;
} decim_t;

void resample_init(void);
/* in: n * DECIM samples (stride `stride`), out: n samples */
void decim_process(decim_t *d, const float *in, int stride, float *out, int n);

/* Integer-factor polyphase interpolator to 48 kHz (factor 1, 2 or 3). */
#define INT_TAPS 32               /* taps per phase */
typedef struct {
    int factor;
    int phase;
    float hist[OUT_CH][INT_TAPS];
} interp_t;

void interp_init(interp_t *s, int factor);
/* Produces one output sample per channel for the current phase from the history. */
void interp_output(const interp_t *s, int channels, float *out);
/* Pushes one input frame (channels samples). */
void interp_push(interp_t *s, int channels, const float *in);

#endif
