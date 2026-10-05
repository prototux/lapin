#ifndef SATD_RES_H
#define SATD_RES_H

#include "common.h"

/*
 * Residual echo suppressor, after the linear AEC. Small speakers distort,
 * and part of their echo (harmonics, rattle) is not a linear function of the
 * reference, so the adaptive filters cannot remove it. Per bin, the residual
 * echo power is modelled as
 *     a_k * Pref_k  +  b_k * Pref_total
 * (the second term captures distortion spread across frequencies), learned by
 * recursive least squares on far-end-only frames. The suppression gain is the
 * same for every microphone, so inter-mic phases (DOA, beamforming) are kept.
 */
typedef struct {
    float S11[NBIN], S12[NBIN], S22[NBIN], C1[NBIN], C2[NBIN];
    float a[NBIN], b[NBIN];
    float pref_s[NBIN], ptot_s;
    float gain[NBIN];
    int frames;
    float atten_db;     /* echo attenuation measured on far-end-only frames */
    double pin, pout;
    int doubletalk;
} res_t;

void res_init(res_t *r);
/* R: reference spectrum; X: echo-cancelled mic spectra, modified in place.
 * near_speech: VAD of the previous frame. strength: over-subtraction (1..3),
 * floor_db: maximum suppression. */
void res_process(res_t *r, const cfloat *R, cfloat X[NMIC][NBIN], int near_speech, float strength,
                 float floor_db);

#endif
