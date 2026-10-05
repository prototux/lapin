#ifndef SATD_AEC_H
#define SATD_AEC_H

#include "common.h"

/* Multichannel acoustic echo canceller: one adaptive filter per microphone
 * (speexdsp MDF, a partitioned-block frequency-domain filter with double-talk
 * control), all fed the same reference: the hardware loopback of the output,
 * captured sample-synchronously with the microphones. */
typedef struct aec aec_t;

aec_t *aec_create(int tail_ms);
void aec_destroy(aec_t *a);
void aec_reset(aec_t *a);
/* mic[m][HOP] in, out[m][HOP] out (may alias mic), ref[HOP]. near_speech
 * gates the ERLE statistics (measured on far-end-only frames). */
void aec_process(aec_t *a, float mic[NMIC][HOP], const float *ref, int near_speech);
float aec_erle_db(const aec_t *a);
int aec_tail_ms(const aec_t *a);

#endif
