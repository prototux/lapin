#include "resample.h"

#include <string.h>

static float dec_h[DEC_TAPS];
static float int_h[4][INT_TAPS * 3];  /* [factor][taps] prototype, factor 2 and 3 */

static double bessel_i0(double x)
{
    double sum = 1, term = 1;
    for (int k = 1; k < 30; k++) {
        term *= (x / (2 * k)) * (x / (2 * k));
        sum += term;
    }
    return sum;
}

/* Kaiser-windowed sinc lowpass; cutoff in Hz at rate `fs`, scaled by `gain`. */
static void design(float *h, int n, double cutoff, double fs, double beta, double gain)
{
    double fc = cutoff / fs, sum = 0, mid = (n - 1) / 2.0;
    for (int i = 0; i < n; i++) {
        double x = i - mid;
        double s = x == 0 ? 2 * fc : sin(2 * M_PI * fc * x) / (M_PI * x);
        double r = 2.0 * i / (n - 1) - 1;
        double w = bessel_i0(beta * sqrt(1 - r * r)) / bessel_i0(beta);
        h[i] = (float)(s * w);
        sum += h[i];
    }
    for (int i = 0; i < n; i++)
        h[i] = (float)(h[i] * gain / sum);
}

void resample_init(void)
{
    design(dec_h, DEC_TAPS, 7200, CAP_RATE, 8.0, 1.0);
    design(int_h[2], INT_TAPS * 2, 11000, OUT_RATE, 8.0, 2.0);
    design(int_h[3], INT_TAPS * 3, 7600, OUT_RATE, 8.0, 3.0);
}

void decim_process(decim_t *d, const float *in, int stride, float *out, int n)
{
    /* hist is a doubled ring so a contiguous window is always available */
    for (int i = 0; i < n; i++) {
        for (int k = 0; k < DECIM; k++) {
            float x = in[(i * DECIM + k) * stride];
            d->hist[d->pos] = x;
            d->hist[d->pos + DEC_TAPS] = x;
            d->pos = (d->pos + 1) % DEC_TAPS;
        }
        const float *w = d->hist + d->pos;   /* oldest .. newest */
        float acc = 0;
        for (int t = 0; t < DEC_TAPS; t++)
            acc += w[t] * dec_h[t];
        out[i] = acc;
    }
}

void interp_init(interp_t *s, int factor)
{
    memset(s, 0, sizeof *s);
    s->factor = factor;
}

void interp_push(interp_t *s, int channels, const float *in)
{
    for (int c = 0; c < channels; c++) {
        memmove(s->hist[c] + 1, s->hist[c], (INT_TAPS - 1) * sizeof(float));
        s->hist[c][0] = in[c];
    }
}

void interp_output(const interp_t *s, int channels, float *out)
{
    const int L = s->factor;
    if (L == 1) {
        for (int c = 0; c < channels; c++)
            out[c] = s->hist[c][0];
        return;
    }
    const float *h = int_h[L];
    for (int c = 0; c < channels; c++) {
        float acc = 0;
        for (int t = 0; t < INT_TAPS; t++)
            acc += h[s->phase + t * L] * s->hist[c][t];
        out[c] = acc;
    }
}
