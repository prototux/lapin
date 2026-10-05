/*
 * Real FFT of size 512, computed as a 256-point complex radix-2 FFT on the
 * even/odd samples packed as re/im, plus a split step.
 */
#include "fft.h"

#include <string.h>

#define M (NFFT / 2)

static cfloat tw[M / 2];        /* twiddles of the M-point FFT */
static cfloat split_w[M + 1];   /* e^{-j 2 pi k / NFFT} */
static int rev[M];
float g_win[NFFT];

void fft_init(void)
{
    int bits = 0;
    while ((1 << bits) < M)
        bits++;
    for (int i = 0; i < M; i++) {
        int r = 0;
        for (int b = 0; b < bits; b++)
            if (i & (1 << b))
                r |= 1 << (bits - 1 - b);
        rev[i] = r;
    }
    for (int i = 0; i < M / 2; i++)
        tw[i] = cexpf(-I * TAU_F * i / M);
    for (int k = 0; k <= M; k++)
        split_w[k] = cexpf(-I * TAU_F * k / NFFT);
    /* periodic sqrt-Hann: w^2(n) + w^2(n + NFFT/2) = 1 */
    for (int n = 0; n < NFFT; n++)
        g_win[n] = sqrtf(0.5f - 0.5f * cosf(TAU_F * n / NFFT));
}

/* In-place M-point complex FFT; inverse = 1 conjugates the twiddles. */
static void cfft(cfloat *x, int inverse)
{
    for (int i = 0; i < M; i++) {
        int j = rev[i];
        if (j > i) {
            cfloat t = x[i];
            x[i] = x[j];
            x[j] = t;
        }
    }
    for (int len = 2; len <= M; len <<= 1) {
        int half = len >> 1, step = M / len;
        for (int i = 0; i < M; i += len) {
            for (int k = 0; k < half; k++) {
                cfloat w = tw[k * step];
                if (inverse)
                    w = conjf(w);
                cfloat a = x[i + k], b = x[i + k + half] * w;
                x[i + k] = a + b;
                x[i + k + half] = a - b;
            }
        }
    }
}

void fft_forward(const float *in, cfloat *out)
{
    cfloat z[M];
    for (int n = 0; n < M; n++)
        z[n] = in[2 * n] + I * in[2 * n + 1];
    cfft(z, 0);
    for (int k = 0; k <= M; k++) {
        cfloat zk = z[k % M], zc = conjf(z[(M - k) % M]);
        cfloat e = 0.5f * (zk + zc);
        cfloat o = -0.5f * I * (zk - zc);
        out[k] = e + split_w[k] * o;
    }
}

void fft_inverse(const cfloat *in, float *out)
{
    cfloat z[M];
    for (int k = 0; k < M; k++) {
        cfloat xk = in[k], xc = conjf(in[M - k]);
        cfloat e = 0.5f * (xk + xc);
        cfloat o = 0.5f * (xk - xc) * conjf(split_w[k]);
        z[k] = e + I * o;
    }
    cfft(z, 1);
    const float s = 1.0f / M;
    for (int n = 0; n < M; n++) {
        out[2 * n] = crealf(z[n]) * s;
        out[2 * n + 1] = cimagf(z[n]) * s;
    }
}

void stft_push(stft_t *s, const float *hop, cfloat *spec)
{
    float frame[NFFT];
    memmove(s->buf, s->buf + HOP, (NFFT - HOP) * sizeof(float));
    memcpy(s->buf + NFFT - HOP, hop, HOP * sizeof(float));
    for (int n = 0; n < NFFT; n++)
        frame[n] = s->buf[n] * g_win[n];
    fft_forward(frame, spec);
}

void istft_push(istft_t *s, const cfloat *spec, float *hop)
{
    float frame[NFFT];
    fft_inverse(spec, frame);
    for (int n = 0; n < NFFT; n++)
        s->ola[n] += frame[n] * g_win[n];
    memcpy(hop, s->ola, HOP * sizeof(float));
    memmove(s->ola, s->ola + HOP, (NFFT - HOP) * sizeof(float));
    memset(s->ola + NFFT - HOP, 0, HOP * sizeof(float));
}
