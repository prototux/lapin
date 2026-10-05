/* From satellite/engine/src/ns.c (ReSpeaker satellite), same algorithm; for
 * the ESP32: no libm min/max calls and reciprocals instead of divisions in
 * the per-bin loop (it has no hardware divider). */
#include "ns.h"

#include <string.h>

#define VAD_K0 10       /* 312 Hz */
#define VAD_K1 128      /* 4000 Hz */
#define MIN_WIN 125     /* frames per minimum-search sub-window (~2 s) */

void ns_init(ns_t *ns)
{
    memset(ns, 0, sizeof *ns);
    for (int k = 0; k < NBIN; k++) {
        ns->G[k] = 1;
        ns->post_prev[k] = 1;
    }
}

void ns_process(ns_t *ns, const cfloat *Y, cfloat *out, float floor_db, float vad_thr_db)
{
    const float gmin = db_to_lin(floor_db);
    float P[NBIN];
    for (int k = 0; k < NBIN; k++)
        P[k] = crealf(Y[k]) * crealf(Y[k]) + cimagf(Y[k]) * cimagf(Y[k]) + 1e-12f;

    if (ns->frames < 8) {
        /* bootstrap the noise estimate from the first frames */
        for (int k = 0; k < NBIN; k++) {
            ns->N[k] = (ns->N[k] * ns->frames + P[k]) / (ns->frames + 1);
            ns->S[k] = ns->Smin[k] = ns->Stmp[k] = ns->N[k];
        }
    }
    ns->frames++;

    float ps = 0, pn = 0;      /* single precision: doubles are emulated in software on the ESP32 */
    for (int k = 0; k < NBIN; k++) {
        /* frequency-smoothed periodogram, then time smoothing */
        float pf = 0.25f * P[k > 0 ? k - 1 : k] + 0.5f * P[k] + 0.25f * P[k < NBIN - 1 ? k + 1 : k];
        ns->S[k] = 0.8f * ns->S[k] + 0.2f * pf;
        /* running minimum over ~2-4 s (two sub-windows) */
        ns->Smin[k] = minf(ns->Smin[k], ns->S[k]);
        ns->Stmp[k] = minf(ns->Stmp[k], ns->S[k]);
        if (ns->frames % MIN_WIN == 0) {
            ns->Smin[k] = minf(ns->Stmp[k], ns->S[k]);
            ns->Stmp[k] = ns->S[k];
        }
        /* speech presence probability */
        float ind = ns->S[k] > 5.0f * ns->Smin[k] ? 1.0f : 0.0f;
        ns->p[k] = 0.8f * ns->p[k] + 0.2f * ind;
        /* noise update, frozen where speech is likely */
        float ad = 0.95f + 0.05f * ns->p[k];
        ns->N[k] = ad * ns->N[k] + (1 - ad) * P[k];
        /* never above the noise floor seen by the minimum tracking: speech
         * onsets (before the presence probability rises) must not inflate
         * the estimate, or the following speech looks like noise */
        if (ns->frames > 8)
            ns->N[k] = minf(ns->N[k], 2.5f * ns->Smin[k]);

        /* decision-directed a priori SNR, Wiener gain */
        float post = P[k] * sat_recipf(ns->N[k]);
        float prio = 0.98f * ns->G[k] * ns->G[k] * ns->post_prev[k] + 0.02f * maxf(post - 1, 0);
        float g = prio * sat_recipf(1 + prio);
        ns->G[k] = maxf(g, gmin);
        ns->post_prev[k] = post;
        if (k >= VAD_K0 && k <= VAD_K1) {
            ps += P[k];
            pn += ns->N[k];
        }
    }
    ns->snr_db = 10 * log10f(ps / (pn + 1e-12f) + 1e-12f);
    float pr = 1.0f / (1.0f + expf(-(ns->snr_db - vad_thr_db) / 1.5f));
    ns->prob = 0.6f * ns->prob + 0.4f * pr;
    if (out)
        for (int k = 0; k < NBIN; k++)
            out[k] = Y[k] * ns->G[k];
}

void agc_init(agc_t *a)
{
    a->gain_db = 0;
    a->cur = 1;
    a->lim_gain = 1;
}

void agc_process(agc_t *a, float *x, int n, int speech, float target_db, float max_gain_db)
{
    float e = 0;
    for (int i = 0; i < n; i++)
        e += x[i] * x[i];
    float level = pow_to_db(e / n);
    if (speech && level > -70) {
        float want = clampf(target_db - level, -12.0f, max_gain_db);
        a->gain_db += (want - a->gain_db) * (want < a->gain_db ? 0.25f : 0.015f);
    }
    float target = db_to_lin(a->gain_db), step = (target - a->cur) / n;
    const float ceil = 0.89f, inv_rel = 1.0f / 0.9995f;
    for (int i = 0; i < n; i++) {
        a->cur += step;
        float y = x[i] * a->cur;
        float pk = fabsf(y) * a->lim_gain;
        if (pk > ceil)
            a->lim_gain = ceil / fabsf(y);
        else
            a->lim_gain = minf(1.0f, a->lim_gain * inv_rel);
        x[i] = y * a->lim_gain;
    }
}

float agc_gain(const agc_t *a) { return a->cur; }
