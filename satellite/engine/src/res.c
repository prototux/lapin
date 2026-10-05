#include "res.h"

#include <string.h>

#define K0 3          /* ignore DC bins */

void res_init(res_t *r)
{
    memset(r, 0, sizeof *r);
    for (int k = 0; k < NBIN; k++)
        r->gain[k] = 1;
}

void res_process(res_t *r, const cfloat *R, cfloat X[NMIC][NBIN], int near_speech, float strength,
                 float floor_db)
{
    float pref[NBIN], pe[NBIN], ptot = 0, petot = 0, echo_tot = 0;
    for (int k = 0; k < NBIN; k++) {
        pref[k] = crealf(R[k]) * crealf(R[k]) + cimagf(R[k]) * cimagf(R[k]);
        float s = 0;
        for (int m = 0; m < NMIC; m++)
            s += crealf(X[m][k]) * crealf(X[m][k]) + cimagf(X[m][k]) * cimagf(X[m][k]);
        pe[k] = s / NMIC;
        if (k >= K0) {
            ptot += pref[k];
            petot += pe[k];
        }
    }
    /* the echo lasts after the reference (room, speaker): smooth in time */
    for (int k = 0; k < NBIN; k++)
        r->pref_s[k] = fmaxf(pref[k], 0.75f * r->pref_s[k] + 0.25f * pref[k]);
    r->ptot_s = fmaxf(ptot, 0.75f * r->ptot_s + 0.25f * ptot);

    const float active = 1e-4f * NFFT;          /* reference above ~ -70 dBFS */
    if (r->ptot_s < active) {
        for (int k = 0; k < NBIN; k++)
            r->gain[k] = fminf(1.0f, r->gain[k] + 0.1f);
        r->doubletalk = 0;
        goto apply;
    }
    for (int k = K0; k < NBIN; k++)
        echo_tot += r->a[k] * r->pref_s[k] + r->b[k] * r->ptot_s;
    /* double talk: much more energy than the model expects (once trained).
     * The voice detector is not trusted here: our own playback fools it, and
     * the model would never learn. Early on it gates training only when the
     * playback is quiet compared to what the mics hear. */
    r->doubletalk = r->frames > 120 && petot > 4.0f * echo_tot + 1e-9f;
    int train = r->frames > 120 ? !r->doubletalk : (!near_speech || ptot > 0.3f * petot);
    if (train) {
        const float al = 0.995f;
        r->frames++;
        for (int k = K0; k < NBIN; k++) {
            float x1 = r->pref_s[k], x2 = r->ptot_s, y = pe[k];
            r->S11[k] = al * r->S11[k] + (1 - al) * x1 * x1;
            r->S12[k] = al * r->S12[k] + (1 - al) * x1 * x2;
            r->S22[k] = al * r->S22[k] + (1 - al) * x2 * x2;
            r->C1[k] = al * r->C1[k] + (1 - al) * x1 * y;
            r->C2[k] = al * r->C2[k] + (1 - al) * x2 * y;
            /* 2x2 regularized least squares, non-negative */
            float s11 = r->S11[k] * 1.01f + 1e-20f, s22 = r->S22[k] * 1.01f + 1e-20f, s12 = r->S12[k];
            float det = s11 * s22 - s12 * s12;
            float a = 0, b = 0;
            if (det > 1e-30f) {
                a = (r->C1[k] * s22 - r->C2[k] * s12) / det;
                b = (r->C2[k] * s11 - r->C1[k] * s12) / det;
            }
            if (a < 0 || b < 0) {           /* fall back to a single regressor */
                float a1 = r->C1[k] / s11, b1 = r->C2[k] / s22;
                float e1 = a1 * a1 * s11, e2 = b1 * b1 * s22;
                a = e1 >= e2 ? fmaxf(a1, 0) : 0;
                b = e1 >= e2 ? 0 : fmaxf(b1, 0);
            }
            r->a[k] = a;
            r->b[k] = b;
        }
    }
    {
        const float gmin = db_to_lin(floor_db);
        for (int k = 0; k < NBIN; k++) {
            float echo = r->a[k] * r->pref_s[k] + r->b[k] * r->ptot_s;
            float g = 1.0f - strength * echo / (pe[k] + 1e-12f);
            g = clampf(g, gmin, 1.0f);
            /* fast attack, slower release: no pumping on the echo tail */
            r->gain[k] = g < r->gain[k] ? g : r->gain[k] + 0.3f * (g - r->gain[k]);
        }
    }
apply:;
    double pin = 0, pout = 0;
    for (int k = 0; k < NBIN; k++) {
        float g = r->gain[k];
        pin += pe[k];
        pout += pe[k] * g * g;
        for (int m = 0; m < NMIC; m++)
            X[m][k] *= g;
    }
    if (r->ptot_s >= active && !near_speech && !r->doubletalk) {
        r->pin = 0.95 * r->pin + 0.05 * pin;
        r->pout = 0.95 * r->pout + 0.05 * pout;
        r->atten_db = (float)(10 * log10((r->pin + 1e-12) / (r->pout + 1e-12)));
    }
}
