#ifndef SATD_FFT_H
#define SATD_FFT_H

#include "common.h"

/* Real FFT of size NFFT (512): NFFT real samples <-> NBIN complex bins.
 * Unnormalized forward; fft_inverse() includes the 1/N scale, so
 * fft_inverse(fft_forward(x)) == x. Thread-safe after fft_init(). */
void fft_init(void);
void fft_forward(const float *in, cfloat *out);
void fft_inverse(const cfloat *in, float *out);

/* Short-time analysis / synthesis with a sqrt-Hann window, HOP = NFFT / 2:
 * perfect reconstruction. One state per signal. */
typedef struct {
    float buf[NFFT];
} stft_t;

typedef struct {
    float ola[NFFT];
} istft_t;

extern float g_win[NFFT];
void stft_push(stft_t *s, const float *hop, cfloat *spec);
void istft_push(istft_t *s, const cfloat *spec, float *hop);

#endif
