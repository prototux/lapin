#ifndef SATD_BEAM_H
#define SATD_BEAM_H

#include "common.h"

/* Microphone geometry (metres): x to the right, y toward the Ethernet/USB
 * edge. Bearings are degrees clockwise from +y. ALSA channels 0..5 are
 * MIC1..MIC6 as printed on the board. */
extern const float g_mic_pos[NMIC][2];

enum bf_mode { BF_MVDR, BF_SUPERDIRECTIVE, BF_DAS, BF_MIC1 };

typedef struct {
    float az;           /* refined bearing, degrees */
    float power;        /* normalized SRP at the peak, 0..1 */
} doa_peak_t;

typedef struct {
    float srp[NANG];    /* normalized steered response power, 0..1 */
    doa_peak_t peak[2]; /* the two strongest separated peaks */
    int npeaks;
    float confidence;   /* (peak - mean) / (1 - mean) */
} doa_t;

typedef struct beam beam_t;

beam_t *beam_create(int nfixed, float loading);
void beam_destroy(beam_t *b);
int beam_nfixed(const beam_t *b);
float beam_fixed_az(const beam_t *b, int i);

/* SRP-PHAT on the (echo-cancelled) microphone spectra. weight: optional
 * per-bin weight (e.g. speech presence) so stationary noise does not pull
 * the estimate; NULL = plain PHAT. */
void beam_doa(beam_t *b, cfloat X[NMIC][NBIN], const float *weight, doa_t *out);

/* Output of fixed beam i. */
void beam_fixed(const beam_t *b, int i, cfloat X[NMIC][NBIN], cfloat *Y);

/* Adaptive beam toward `az`. noise_update: X holds noise / interference only,
 * fold it into the noise covariance. */
void beam_tracked(beam_t *b, enum bf_mode mode, float az, cfloat X[NMIC][NBIN],
                  int noise_update, cfloat *Y);
void beam_reset_noise(beam_t *b);

static inline int ang_index(float az)
{
    return (int)lrintf(wrap360(az) / ANG_STEP) % NANG;
}

#endif
