#ifndef SATD_NS_H
#define SATD_NS_H

#include "common.h"

/* Single-channel noise suppressor in the STFT domain: minima-controlled
 * recursive noise averaging (MCRA) and a decision-directed Wiener gain with
 * a floor. Its noise estimate also yields a frame SNR, used as the VAD. */
typedef struct {
    float S[NBIN], Smin[NBIN], Stmp[NBIN], N[NBIN], p[NBIN], G[NBIN], post_prev[NBIN];
    int frames;
    float snr_db;       /* speech band, this frame */
    float prob;         /* speech probability, 0..1 */
} ns_t;

void ns_init(ns_t *ns);
/* Y: input spectrum; out (may alias Y): enhanced spectrum, or NULL to only
 * update the estimates. floor_db: maximum attenuation (e.g. -15). */
void ns_process(ns_t *ns, const cfloat *Y, cfloat *out, float floor_db, float vad_thr_db);

/* Speech AGC: the gain moves only during speech (no pumping in pauses),
 * quickly down and slowly up, followed by a peak limiter. */
typedef struct {
    float gain_db, cur, lim_gain;
} agc_t;

void agc_init(agc_t *a);
void agc_process(agc_t *a, float *x, int n, int speech, float target_db, float max_gain_db);
/* Applies the current gain only (for pre-roll audio). */
float agc_gain(const agc_t *a);

#endif
