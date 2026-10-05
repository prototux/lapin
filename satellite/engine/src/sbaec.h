#ifndef SATD_SBAEC_H
#define SATD_SBAEC_H

#include "common.h"

/*
 * Subband acoustic echo canceller, in the engine's STFT domain: per
 * microphone and frequency bin, a short complex filter over the last L
 * reference spectra (L x 16 ms of echo tail), adapted by normalized LMS.
 * Robust where a full-band canceller struggles with this hardware (the
 * small speaker's distortion makes the echo partly non-linear): it cannot
 * diverge, and the adaptation slows down when someone talks over the
 * playback (double talk) so the filter is not destroyed by near-end speech.
 */
#define SB_MAXL 16

typedef struct {
    int L, pos, frames;
    cfloat W[NMIC][NBIN][SB_MAXL];
    cfloat Rh[SB_MAXL][NBIN];
    double pin, pout;
    float erle_db;
    int doubletalk;
} sbaec_t;

void sbaec_init(sbaec_t *a, int tail_ms);
/* R: reference spectrum; X: microphone spectra, echo removed in place. */
void sbaec_process(sbaec_t *a, const cfloat *R, cfloat X[NMIC][NBIN], int near_speech, float mu);

#endif
