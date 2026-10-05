#include "sbaec.h"

#include <string.h>

void sbaec_init(sbaec_t *a, int tail_ms)
{
    memset(a, 0, sizeof *a);
    a->L = (int)clampf((float)(tail_ms + 15) / 16, 2, SB_MAXL);
}

void sbaec_process(sbaec_t *a, const cfloat *R, cfloat X[NMIC][NBIN], int near_speech, float mu)
{
    const int L = a->L;
    a->pos = (a->pos + 1) % L;
    memcpy(a->Rh[a->pos], R, sizeof(cfloat) * NBIN);

    float norm[NBIN];
    double er = 0, enorm = 0;
    for (int k = 0; k < NBIN; k++) {
        float s = 0;
        for (int l = 0; l < L; l++) {
            cfloat r = a->Rh[l][k];
            s += crealf(r) * crealf(r) + cimagf(r) * cimagf(r);
        }
        norm[k] = s;
        enorm += s;
        er += crealf(R[k]) * crealf(R[k]) + cimagf(R[k]) * cimagf(R[k]);
    }
    const float delta = (float)(enorm / NBIN) * 1e-2f + 1e-10f;
    const int active = er > 1e-7 * NFFT * NFFT;      /* playing something */

    /* filter: echo estimate and error for every mic and bin */
    static cfloat Y[NMIC][NBIN];
    double ex = 0, ee = 0, ey = 0;
    for (int m = 0; m < NMIC; m++)
        for (int k = 0; k < NBIN; k++) {
            cfloat y = 0;
            const cfloat *w = a->W[m][k];
            for (int l = 0; l < L; l++)
                y += w[l] * a->Rh[(a->pos - l + L) % L][k];
            Y[m][k] = y;
            cfloat e = X[m][k] - y;
            ex += crealf(X[m][k]) * crealf(X[m][k]) + cimagf(X[m][k]) * cimagf(X[m][k]);
            ee += crealf(e) * crealf(e) + cimagf(e) * cimagf(e);
            ey += crealf(y) * crealf(y) + cimagf(y) * cimagf(y);
        }

    /* double talk: far more energy left than the echo model explains, once
     * it has converged (and confirmed by the voice detector, or massive) */
    a->doubletalk = a->frames > 150 && ((near_speech && ee > 2.0 * ey) || ee > 8.0 * ey);
    float step = mu;
    if (!active)
        step = 0;
    else if (a->doubletalk)
        step *= 0.03f;
    else if (a->frames < 60)
        step = fminf(1.0f, mu * 1.5f);              /* converge fast at first */

    for (int m = 0; m < NMIC; m++)
        for (int k = 0; k < NBIN; k++) {
            cfloat e = X[m][k] - Y[m][k];
            if (step > 0) {
                cfloat g = e * (step / (norm[k] + delta));
                cfloat *w = a->W[m][k];
                for (int l = 0; l < L; l++)
                    w[l] += g * conjf(a->Rh[(a->pos - l + L) % L][k]);
            }
            X[m][k] = e;
        }
    if (active) {
        a->frames++;
        if (!a->doubletalk && !near_speech) {
            a->pin = 0.97 * a->pin + 0.03 * ex;
            a->pout = 0.97 * a->pout + 0.03 * ee;
            a->erle_db = (float)(10 * log10((a->pin + 1e-12) / (a->pout + 1e-12)));
        }
    }
}
