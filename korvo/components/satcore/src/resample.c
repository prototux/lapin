/* From satellite/engine/src/resample.c (interpolator part); the history is
 * a doubled ring instead of a memmove'd array (same output). */
#include "resample.h"

#include <string.h>

float interp_h[4][INT_TAPS * 3];  /* [factor][taps] prototype, factor 2 and 3 */
float interp_hp[4][3][INT_TAPS];

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
    design(interp_h[2], INT_TAPS * 2, 11000, OUT_RATE, 8.0, 2.0);
    design(interp_h[3], INT_TAPS * 3, 7600, OUT_RATE, 8.0, 3.0);
    for (int L = 2; L <= 3; L++)
        for (int p = 0; p < L; p++)
            for (int t = 0; t < INT_TAPS; t++)
                interp_hp[L][p][t] = interp_h[L][p + t * L];
}

void interp_init(interp_t *s, int factor)
{
    memset(s, 0, sizeof *s);
    s->factor = factor;
}

