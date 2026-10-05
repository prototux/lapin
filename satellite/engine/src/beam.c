/*
 * Beamforming and sound source localization for the 6-microphone circular
 * array (radius 46.3 mm).
 *
 *  - Steering vectors for a far-field source on a 4 degree grid.
 *  - SRP-PHAT: steered response power of the phase-transformed spectra,
 *    computed as |sum_m conj(d_m) X_m/|X_m||^2, equivalent to summing the
 *    GCC-PHAT of all pairs but cheaper.
 *  - Fixed bank: superdirective beams (MVDR against a diffuse noise field,
 *    with diagonal loading to bound the white noise gain), used by the
 *    hotword detector.
 *  - Tracked beam: MVDR whose noise covariance is learned online from frames
 *    without target speech, so it places nulls on persistent interferers
 *    (TV, fan). Steered continuously by the speaker tracker.
 */
#include "beam.h"

#include <stdlib.h>
#include <string.h>

const float g_mic_pos[NMIC][2] = {
    {-0.0232f, 0.0401f}, {0.0232f, 0.0401f}, {0.0463f, 0.0f},
    {0.0232f, -0.0401f}, {-0.0232f, -0.0401f}, {-0.0463f, 0.0f},
};

#define SRP_K0 7        /* 219 Hz */
#define SRP_K1 128      /* 4000 Hz */
#define MVDR_UPDATE 4   /* frames between weight updates */

struct beam {
    cfloat steer[NANG][NBIN][NMIC];
    float gamma[NBIN][NMIC][NMIC];      /* diffuse coherence + loading */
    int nfixed;
    float fixed_az[MAX_BEAMS];
    cfloat wfix[MAX_BEAMS][NBIN][NMIC];
    /* tracked beam */
    float loading;
    cfloat R[NBIN][NMIC][NMIC];
    cfloat w[NBIN][NMIC];
    int w_ang, w_mode, w_age, noise_frames;
};

/* Solves A z = b (n x n complex, A destroyed) by Gaussian elimination with
 * partial pivoting. Returns 0 if singular. */
static int csolve(cfloat A[NMIC][NMIC], cfloat *b, cfloat *z)
{
    const int n = NMIC;
    for (int c = 0; c < n; c++) {
        int p = c;
        float best = cabsf(A[c][c]);
        for (int r = c + 1; r < n; r++)
            if (cabsf(A[r][c]) > best) {
                best = cabsf(A[r][c]);
                p = r;
            }
        if (best < 1e-20f)
            return 0;
        if (p != c) {
            for (int k = 0; k < n; k++) {
                cfloat t = A[c][k];
                A[c][k] = A[p][k];
                A[p][k] = t;
            }
            cfloat t = b[c];
            b[c] = b[p];
            b[p] = t;
        }
        for (int r = c + 1; r < n; r++) {
            cfloat f = A[r][c] / A[c][c];
            if (f == 0)
                continue;
            for (int k = c; k < n; k++)
                A[r][k] -= f * A[c][k];
            b[r] -= f * b[c];
        }
    }
    for (int r = n - 1; r >= 0; r--) {
        cfloat s = b[r];
        for (int k = r + 1; k < n; k++)
            s -= A[r][k] * z[k];
        z[r] = s / A[r][r];
    }
    return 1;
}

/* w = A^-1 d / (d^H A^-1 d) */
static void mvdr_weights(cfloat A[NMIC][NMIC], const cfloat *d, cfloat *w)
{
    cfloat b[NMIC], z[NMIC];
    memcpy(b, d, sizeof b);
    if (!csolve(A, b, z)) {
        for (int m = 0; m < NMIC; m++)
            w[m] = d[m] / NMIC;
        return;
    }
    cfloat den = 0;
    for (int m = 0; m < NMIC; m++)
        den += conjf(d[m]) * z[m];
    if (cabsf(den) < 1e-12f) {
        for (int m = 0; m < NMIC; m++)
            w[m] = d[m] / NMIC;
        return;
    }
    for (int m = 0; m < NMIC; m++)
        w[m] = z[m] / den;
}

static void superdirective(beam_t *b, int ang, int k, cfloat *w)
{
    cfloat A[NMIC][NMIC];
    for (int i = 0; i < NMIC; i++)
        for (int j = 0; j < NMIC; j++)
            A[i][j] = b->gamma[k][i][j];
    mvdr_weights(A, b->steer[ang][k], w);
}

beam_t *beam_create(int nfixed, float loading)
{
    beam_t *b = calloc(1, sizeof *b);
    if (!b)
        return NULL;
    if (nfixed < 1)
        nfixed = 1;
    if (nfixed > MAX_BEAMS)
        nfixed = MAX_BEAMS;
    b->nfixed = nfixed;
    b->loading = loading;

    for (int a = 0; a < NANG; a++) {
        float th = a * ANG_STEP * (float)M_PI / 180.0f;
        float ux = sinf(th), uy = cosf(th);
        for (int m = 0; m < NMIC; m++) {
            /* arrival time relative to the array center */
            float tau = -(g_mic_pos[m][0] * ux + g_mic_pos[m][1] * uy) / SOUND_SPEED;
            for (int k = 0; k < NBIN; k++) {
                float f = (float)k * SR / NFFT;
                b->steer[a][k][m] = cexpf(-I * TAU_F * f * tau);
            }
        }
    }
    for (int k = 0; k < NBIN; k++) {
        float f = (float)k * SR / NFFT;
        for (int i = 0; i < NMIC; i++)
            for (int j = 0; j < NMIC; j++) {
                float dx = g_mic_pos[i][0] - g_mic_pos[j][0];
                float dy = g_mic_pos[i][1] - g_mic_pos[j][1];
                float x = TAU_F * f * sqrtf(dx * dx + dy * dy) / SOUND_SPEED;
                b->gamma[k][i][j] = (x < 1e-6f ? 1.0f : sinf(x) / x) + (i == j ? loading : 0);
            }
    }
    for (int i = 0; i < nfixed; i++) {
        b->fixed_az[i] = 360.0f * i / nfixed;
        int ang = ang_index(b->fixed_az[i]);
        for (int k = 0; k < NBIN; k++)
            superdirective(b, ang, k, b->wfix[i][k]);
    }
    beam_reset_noise(b);
    return b;
}

void beam_destroy(beam_t *b) { free(b); }
int beam_nfixed(const beam_t *b) { return b->nfixed; }
float beam_fixed_az(const beam_t *b, int i) { return b->fixed_az[i]; }

void beam_reset_noise(beam_t *b)
{
    /* start from a diffuse field: MVDR == superdirective until it learns */
    for (int k = 0; k < NBIN; k++)
        for (int i = 0; i < NMIC; i++)
            for (int j = 0; j < NMIC; j++)
                b->R[k][i][j] = b->gamma[k][i][j] * 1e-6f;
    b->w_ang = -1;
    b->w_age = 0;
    b->noise_frames = 0;
}

void beam_doa(beam_t *b, cfloat X[NMIC][NBIN], const float *weight, doa_t *out)
{
    cfloat Xn[SRP_K1 + 1][NMIC];
    float norm = 0;
    for (int k = SRP_K0; k <= SRP_K1; k++) {
        float w = weight ? weight[k] : 1.0f;
        norm += w * w;
        for (int m = 0; m < NMIC; m++) {
            float mag = cabsf(X[m][k]);
            Xn[k][m] = mag > 1e-12f ? X[m][k] * (w / mag) : 0;
        }
    }
    if (norm < 1e-6f)
        norm = 1e-6f;
    float mean = 0;
    for (int a = 0; a < NANG; a++) {
        float s = 0;
        for (int k = SRP_K0; k <= SRP_K1; k++) {
            const cfloat *d = b->steer[a][k];
            cfloat acc = 0;
            for (int m = 0; m < NMIC; m++)
                acc += conjf(d[m]) * Xn[k][m];
            s += crealf(acc) * crealf(acc) + cimagf(acc) * cimagf(acc);
        }
        out->srp[a] = s / (norm * NMIC * NMIC);
        mean += out->srp[a];
    }
    mean /= NANG;

    /* two strongest local maxima at least 32 degrees apart */
    out->npeaks = 0;
    int idx[2] = {-1, -1};
    for (int pass = 0; pass < 2; pass++) {
        int best = -1;
        for (int a = 0; a < NANG; a++) {
            float v = out->srp[a];
            if (v < out->srp[(a + 1) % NANG] || v < out->srp[(a + NANG - 1) % NANG])
                continue;
            if (pass == 1) {
                int d = abs(a - idx[0]);
                if (d > NANG / 2)
                    d = NANG - d;
                if (d < 8)
                    continue;
            }
            if (best < 0 || v > out->srp[best])
                best = a;
        }
        if (best < 0)
            break;
        idx[pass] = best;
        float l = out->srp[(best + NANG - 1) % NANG], c = out->srp[best],
              r = out->srp[(best + 1) % NANG];
        float den = l - 2 * c + r, off = den < 0 ? 0.5f * (l - r) / den : 0;
        out->peak[pass].az = wrap360((best + clampf(off, -0.5f, 0.5f)) * ANG_STEP);
        out->peak[pass].power = c;
        out->npeaks++;
    }
    float pk = out->npeaks ? out->peak[0].power : 0;
    out->confidence = clampf((pk - mean) / fmaxf(1e-6f, 1 - mean), 0, 1);
    /* a weak second peak is usually a sidelobe of the first: drop it */
    if (out->npeaks == 2 && (out->peak[1].power - mean) < 0.5f * (pk - mean))
        out->npeaks = 1;
}

void beam_fixed(const beam_t *b, int i, cfloat X[NMIC][NBIN], cfloat *Y)
{
    for (int k = 0; k < NBIN; k++) {
        const cfloat *w = b->wfix[i][k];
        cfloat acc = 0;
        for (int m = 0; m < NMIC; m++)
            acc += conjf(w[m]) * X[m][k];
        Y[k] = acc;
    }
}

void beam_tracked(beam_t *b, enum bf_mode mode, float az, cfloat X[NMIC][NBIN],
                  int noise_update, cfloat *Y)
{
    if (mode == BF_MIC1) {
        memcpy(Y, X[0], NBIN * sizeof(cfloat));
        return;
    }
    int ang = ang_index(az);

    if (mode == BF_MVDR && noise_update) {
        const float alpha = 0.985f;     /* ~1 s of noise frames */
        for (int k = 0; k < NBIN; k++)
            for (int i = 0; i < NMIC; i++)
                for (int j = i; j < NMIC; j++) {
                    cfloat v = alpha * b->R[k][i][j] + (1 - alpha) * X[i][k] * conjf(X[j][k]);
                    b->R[k][i][j] = v;
                    b->R[k][j][i] = conjf(v);
                }
        b->noise_frames++;
    }

    int stale = ang != b->w_ang || (int)mode != b->w_mode;
    if (mode == BF_MVDR && ++b->w_age >= MVDR_UPDATE && b->noise_frames > 0)
        stale = 1;
    if (stale) {
        for (int k = 0; k < NBIN; k++) {
            const cfloat *d = b->steer[ang][k];
            if (mode == BF_DAS) {
                for (int m = 0; m < NMIC; m++)
                    b->w[k][m] = d[m] / NMIC;
            } else if (mode == BF_SUPERDIRECTIVE || b->noise_frames < 30) {
                superdirective(b, ang, k, b->w[k]);
            } else {
                cfloat A[NMIC][NMIC];
                float tr = 0;
                for (int i = 0; i < NMIC; i++)
                    tr += crealf(b->R[k][i][i]);
                float load = b->loading * tr / NMIC + 1e-12f;
                for (int i = 0; i < NMIC; i++)
                    for (int j = 0; j < NMIC; j++)
                        A[i][j] = b->R[k][i][j] + (i == j ? load : 0);
                mvdr_weights(A, d, b->w[k]);
            }
        }
        b->w_ang = ang;
        b->w_mode = (int)mode;
        b->w_age = 0;
    }
    for (int k = 0; k < NBIN; k++) {
        cfloat acc = 0;
        for (int m = 0; m < NMIC; m++)
            acc += conjf(b->w[k][m]) * X[m][k];
        Y[k] = acc;
    }
}
