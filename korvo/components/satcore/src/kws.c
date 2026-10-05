/*
 * Personal wake word detector: MFCC front end + online subsequence DTW.
 * Port of satellite/engine/src/kws.c (see kws.h for what changed: storage
 * and limits only, the math is the same).
 */
#include "kws.h"
#include "sat_port.h"

#include <stdlib.h>
#include <string.h>

#include "fft.h"
#include "ns.h"

/* mel filterbank, packed: band b uses weights mel_pw[mel_off[b] ..] for bins
 * mel_k0[b] .. mel_k1[b] (the satellite keeps a dense [NMEL][NBIN] matrix;
 * 33 KB of internal RAM is too much here) */
static float mel_pw[2 * NBIN + KWS_NMEL];
static int mel_off[KWS_NMEL], mel_k0[KWS_NMEL], mel_k1[KWS_NMEL];
static float dct_m[KWS_NCEP][KWS_NMEL];
static float lifter[KWS_NCEP];

static float hz_to_mel(float f) { return 2595.0f * log10f(1 + f / 700.0f); }
static float mel_to_hz(float m) { return 700.0f * (powf(10, m / 2595.0f) - 1); }

void kws_init(void)
{
    float lo = hz_to_mel(80), hi = hz_to_mel(7600);
    float c[KWS_NMEL + 2];
    for (int i = 0; i < KWS_NMEL + 2; i++)
        c[i] = mel_to_hz(lo + (hi - lo) * i / (KWS_NMEL + 1)) * NFFT / SR;
    int off = 0;
    for (int b = 0; b < KWS_NMEL; b++) {
        float w[NBIN];
        mel_k0[b] = NBIN;
        mel_k1[b] = 0;
        for (int k = 0; k < NBIN; k++) {
            float v = 0;
            if (k > c[b] && k <= c[b + 1])
                v = (k - c[b]) / (c[b + 1] - c[b]);
            else if (k > c[b + 1] && k < c[b + 2])
                v = (c[b + 2] - k) / (c[b + 2] - c[b + 1]);
            w[k] = v;
            if (v > 0) {
                if (k < mel_k0[b])
                    mel_k0[b] = k;
                mel_k1[b] = k;
            }
        }
        if (mel_k0[b] > mel_k1[b]) {    /* narrow low bands: nearest bin */
            int k = (int)lrintf(c[b + 1]);
            mel_k0[b] = mel_k1[b] = k;
            w[k] = 1;
        }
        mel_off[b] = off;
        for (int k = mel_k0[b]; k <= mel_k1[b]; k++)
            mel_pw[off++] = w[k];
    }
    for (int i = 0; i < KWS_NCEP; i++) {
        for (int b = 0; b < KWS_NMEL; b++)
            dct_m[i][b] = cosf((float)M_PI * (i + 1) * (b + 0.5f) / KWS_NMEL);
        lifter[i] = 1 + 11.0f * sinf((float)M_PI * (i + 1) / 22.0f);
    }
}

float g_kws_floor_db = 20;

void kws_features(const cfloat *Y, float *mfcc)
{
    float lm[KWS_NMEL], e[KWS_NMEL], emax = 1e-9f;
    for (int b = 0; b < KWS_NMEL; b++) {
        const float *w = mel_pw + mel_off[b] - mel_k0[b];
        e[b] = 0;
        for (int k = mel_k0[b]; k <= mel_k1[b]; k++)
            e[b] += w[k] * (crealf(Y[k]) * crealf(Y[k]) + cimagf(Y[k]) * cimagf(Y[k]));
        if (e[b] > emax)
            emax = e[b];
    }
    /* floor relative to the strongest band: weak bands, where noise and
     * its suppression dominate, look the same in clean templates and noisy
     * input */
    /* powf is slow in software on the ESP32: the factor once, not per frame */
    static float floor_db = -1, floor_k;
    if (g_kws_floor_db != floor_db) {
        floor_db = g_kws_floor_db;
        floor_k = powf(10, -floor_db / 10);
    }
    const float floor = emax * floor_k + 1e-6f;
    float etot = 0;
    for (int b = 0; b < KWS_NMEL; b++) {
        lm[b] = logf(fmaxf(e[b], floor));
        etot += e[b];
    }
    /* loudness: after mean normalization it tells silence and soft noise
     * from speech, which the spectral shape alone cannot */
    mfcc[KWS_NCEP] = logf(etot + 1e-6f);
    for (int i = 0; i < KWS_NCEP; i++) {
        float s = 0;
        for (int b = 0; b < KWS_NMEL; b++)
            s += dct_m[i][b] * lm[b];
        mfcc[i] = s * lifter[i];
    }
}

static void normalize(float *v)
{
    float n = 0;
    for (int i = 0; i < KWS_NDIM; i++)
        n += v[i] * v[i];
    n = 1.0f / (sqrtf(n) + 1e-9f);
    for (int i = 0; i < KWS_NDIM; i++)
        v[i] *= n;
}

#define E_WEIGHT 3.0f       /* energy vs cepstra in the cosine distance */

/* Feature vector: mean-normalized MFCCs and energy, and their (causal)
 * deltas. */
static void make_vector(float *v, const float *x, const float *x2)
{
    for (int c = 0; c < KWS_NCEP; c++) {
        v[c] = x[c];
        v[KWS_NFEAT + c] = x[c] - x2[c];
    }
    v[KWS_NCEP] = E_WEIGHT * x[KWS_NCEP];
    v[KWS_NFEAT + KWS_NCEP] = E_WEIGHT * (x[KWS_NCEP] - x2[KWS_NCEP]);
    normalize(v);
}

/* stores one unit vector as a template frame */
static void quant_frame(kws_feat_t *f, const float *v)
{
#if KWS_FEAT_BITS == 8
    float mx = 1e-6f;
    for (int c = 0; c < KWS_NDIM; c++)
        mx = maxf(mx, fabsf(v[c]));
    uint16_t q15 = (uint16_t)lrintf(clampf(mx, 0, 1) * 32767.0f);
    if (q15 == 0)
        q15 = 1;
    const float k = KWS_Q * 32767.0f / q15;        /* exact inverse of the stored scale */
    for (int c = 0; c < KWS_NDIM; c++)
        f[c] = (kws_feat_t)lrintf(clampf(v[c] * k, -127, 127));
    memcpy(&f[KWS_NDIM], &q15, 2);
#else
    for (int c = 0; c < KWS_NDIM; c++)
        f[c] = (kws_feat_t)lrintf(clampf(v[c], -1.0f, 1.0f) * KWS_Q);
#endif
}

static inline float frame_scale(const kws_feat_t *f)
{
#if KWS_FEAT_BITS == 8
    const uint8_t *b = (const uint8_t *)&f[KWS_NDIM];
    uint32_t q15 = (uint32_t)b[0] | (uint32_t)b[1] << 8;
    return (float)q15 * (1.0f / (32767.0f * KWS_Q));
#else
    (void)f;
    return 1.0f / KWS_Q;
#endif
}

/* dot product of the live vector (quantized once per hop to Q15) with a
 * stored template frame: integer multiply-accumulates, ~3x fewer
 * instructions than converting every stored value to float */
static inline float dot_tpl(const int16_t *xq, const kws_feat_t *f)
{
    /* unrolled (26 terms): the loop counter and branch were half the work */
    int32_t dot = 0;            /* unit vectors: |sum| <= 32767 * 127 (int8) or 32767 * 16384 (int16) */
#define T(c) dot += (int32_t)xq[c] * f[c];
#if KWS_NDIM == 26
    T(0) T(1) T(2) T(3) T(4) T(5) T(6) T(7) T(8) T(9) T(10) T(11) T(12)
    T(13) T(14) T(15) T(16) T(17) T(18) T(19) T(20) T(21) T(22) T(23) T(24) T(25)
#else
    for (int c = 0; c < KWS_NDIM; c++)
        T(c)
#endif
#undef T
    return (float)dot * frame_scale(f) * (1.0f / 32767.0f);
}

static inline float dot_tt(const kws_feat_t *a, const kws_feat_t *b)
{
#if KWS_FEAT_BITS == 8
    int32_t d8 = 0;
    for (int c = 0; c < KWS_NDIM; c++)
        d8 += (int32_t)a[c] * b[c];
    return (float)d8 * frame_scale(a) * frame_scale(b);
#else
    int32_t dot = 0;     /* unit vectors: |sum| <= 16384^2 = 2^28 */
    for (int c = 0; c < KWS_NDIM; c++)
        dot += (int32_t)a[c] * b[c];
    return (float)dot * (1.0f / (KWS_Q * KWS_Q));
#endif
}


/* ---------------------------------------------------------------- WAV --- */

const int16_t *wav_parse(const uint8_t *wav, size_t len, int *n, int *rate, int *channels)
{
    uint16_t fmt = 0, ch = 0, bits = 0;
    uint32_t r = 0;
    if (len < 12 || memcmp(wav, "RIFF", 4) || memcmp(wav + 8, "WAVE", 4))
        return NULL;
    size_t pos = 12;
    while (pos + 8 <= len) {
        uint32_t sz;
        memcpy(&sz, wav + pos + 4, 4);
        const uint8_t *body = wav + pos + 8;
        if (!memcmp(wav + pos, "fmt ", 4)) {
            if (sz < 16 || pos + 8 + 16 > len)
                return NULL;
            memcpy(&fmt, body, 2);
            memcpy(&ch, body + 2, 2);
            memcpy(&r, body + 4, 4);
            memcpy(&bits, body + 14, 2);
        } else if (!memcmp(wav + pos, "data", 4)) {
            if (fmt != 1 || bits != 16 || ch < 1)
                return NULL;
            size_t avail = len - (pos + 8);
            if (sz > avail)
                sz = (uint32_t)avail;      /* truncated file: use what is there */
            *n = (int)(sz / 2 / ch);
            *rate = (int)r;
            *channels = ch;
            return (const int16_t *)body;  /* RIFF data is 2-byte aligned here */
        }
        pos += 8 + (size_t)sz + (sz & 1);
    }
    return NULL;
}

/* ---------------------------------------------------------- Templates --- */

#define TPL_MAX_FRAMES 400

/* Features of a recorded sample: same front end as the live audio (STFT,
 * noise suppression, MFCC), trimmed to the spoken word. */
int kws_make_template(kws_template_t *t, const int16_t *pcm, int n)
{
    float (*feats)[KWS_NFEAT] = sat_calloc_big(sizeof(float) * TPL_MAX_FRAMES * KWS_NFEAT);
    float *snr = sat_calloc_big(sizeof(float) * TPL_MAX_FRAMES);
    stft_t *st = sat_calloc_big(sizeof *st);
    ns_t *ns = sat_calloc_big(sizeof *ns);
    cfloat *X = sat_calloc_big(sizeof(cfloat) * NBIN);
    int ret = -1;
    t->f = NULL;
    if (!feats || !snr || !st || !ns || !X)
        goto out;
    ns_init(ns);
    int nf = 0;
    float hop[HOP];
    for (int pos = 0; pos + HOP <= n && nf < TPL_MAX_FRAMES; pos += HOP) {
        for (int i = 0; i < HOP; i++)
            hop[i] = pcm[pos + i] / 32768.0f;
        stft_push(st, hop, X);
        ns_process(ns, X, X, -20.0f, 6.0f);
        kws_features(X, feats[nf]);
        snr[nf] = ns->snr_db;
        nf++;
        sat_yield();
    }
    /* word boundaries: first / last frame clearly above the noise */
    int a = -1, b = -1;
    for (int i = 8; i < nf; i++)
        if (snr[i] > 8.0f) {
            if (a < 0)
                a = i;
            b = i;
        }
    if (a < 0)
        goto out;
    a = a > 10 ? a - 2 : 8;
    b = b + 3 < nf ? b + 3 : nf - 1;
    int len = b - a + 1;
    if (len < 12)
        goto out;
    if (len > KWS_MAX_LEN)
        len = KWS_MAX_LEN;
    t->len = len;
    t->f = sat_calloc_big(KWS_TPL_BYTES(len));
    if (!t->f)
        goto out;
    memset(t->mean, 0, sizeof t->mean);
    for (int i = 0; i < len; i++)
        for (int c = 0; c < KWS_NFEAT; c++)
            t->mean[c] += feats[a + i][c] / len;
    for (int i = 0; i < len; i++) {
        float x[KWS_NFEAT], x2[KWS_NFEAT], v[KWS_NDIM];
        for (int c = 0; c < KWS_NFEAT; c++) {
            x[c] = feats[a + i][c] - t->mean[c];
            x2[c] = feats[a + i - 2][c] - t->mean[c];   /* a >= 8 */
        }
        make_vector(v, x, x2);
        quant_frame(t->f + i * KWS_FSTRIDE, v);
    }
    ret = 0;
out:
    sat_free(feats);
    sat_free(snr);
    sat_free(st);
    sat_free(ns);
    sat_free(X);
    return ret;
}

void kws_template_free(kws_template_t *t)
{
    sat_free(t->f);
    t->f = NULL;
}

void kws_model_init(kws_model_t *m) { memset(m, 0, sizeof *m); }

int kws_model_add(kws_model_t *m, const kws_template_t *t)
{
    if (m->ntpl >= KWS_MAX_TPL || !t->f)
        return -1;
    m->tpl[m->ntpl++] = *t;
    return 0;
}

void kws_model_finish(kws_model_t *m)
{
    m->npos = 0;
    memset(m->mean_init, 0, sizeof m->mean_init);
    for (int i = 0; i < m->ntpl; i++)
        if (!m->tpl[i].negative)
            m->npos++;
    for (int i = 0; i < m->ntpl; i++)
        if (!m->tpl[i].negative)
            for (int c = 0; c < KWS_NFEAT; c++)
                m->mean_init[c] += m->tpl[i].mean[c] / (m->npos ? m->npos : 1);
}

void kws_model_free(kws_model_t *m)
{
    for (int i = 0; i < m->ntpl; i++)
        kws_template_free(&m->tpl[i]);
    memset(m, 0, sizeof *m);
}

void kws_model_compact(kws_model_t *m)
{
    /* the DTW reads every feature each 16 ms: internal RAM when there is
     * room (PSRAM goes through the cache) */
    for (int i = 0; i < m->ntpl; i++) {
        size_t b = KWS_TPL_BYTES(m->tpl[i].len);
        kws_feat_t *f = sat_calloc_hot(b);
        if (!f)
            continue;
        memcpy(f, m->tpl[i].f, b);
        sat_free(m->tpl[i].f);
        m->tpl[i].f = f;
    }
}

size_t kws_model_bytes(const kws_model_t *m)
{
    size_t b = 0;
    for (int i = 0; i < m->ntpl; i++)
        b += KWS_TPL_BYTES(m->tpl[i].len);
    return b;
}

/* Whole-sequence DTW between two templates (symmetric2, cost / (La + Lb)). */
static float dtw_pair_buf(const kws_template_t *a, const kws_template_t *b, float *D)
{
    const int W = KWS_MAX_LEN;
    for (int i = 0; i < a->len; i++) {
        sat_yield();
        for (int j = 0; j < b->len; j++) {
            float d = maxf(0.0f, 1.0f - dot_tt(a->f + i * KWS_FSTRIDE, b->f + j * KWS_FSTRIDE));
            if (i == 0 && j == 0)
                D[0] = 2 * d;
            else {
                float best = 1e9f;
                if (i > 0 && j > 0)
                    best = D[(i - 1) * W + j - 1] + 2 * d;
                if (i > 0 && D[(i - 1) * W + j] + d < best)
                    best = D[(i - 1) * W + j] + d;
                if (j > 0 && D[i * W + j - 1] + d < best)
                    best = D[i * W + j - 1] + d;
                D[i * W + j] = best;
            }
        }
    }
    return D[(a->len - 1) * W + b->len - 1] / (a->len + b->len);
}

float kws_dtw_pair(const kws_template_t *a, const kws_template_t *b)
{
    float *D = sat_calloc_big(sizeof(float) * KWS_MAX_LEN * KWS_MAX_LEN);
    if (!D)
        return 9;
    float c = dtw_pair_buf(a, b, D);
    sat_free(D);
    return c;
}

float kws_suggest_threshold(const kws_model_t *m, float *loo_max)
{
    /* each sample against the others of its keyword: how far apart the
     * user's own repetitions are */
    float worst = 0, costs[KWS_MAX_TPL];
    int n = 0;
    float *D = sat_calloc_big(sizeof(float) * KWS_MAX_LEN * KWS_MAX_LEN);
    if (!D)
        return 0.38f;
    for (int i = 0; i < m->ntpl; i++) {
        float best = 9;
        if (m->tpl[i].negative)
            continue;
        for (int j = 0; j < m->ntpl; j++)
            if (i != j && !m->tpl[j].negative && !strcmp(m->tpl[i].keyword, m->tpl[j].keyword)) {
                float c = dtw_pair_buf(&m->tpl[i], &m->tpl[j], D);
                if (c < best)
                    best = c;
            }
        if (best < 9) {
            worst = fmaxf(worst, best);
            costs[n++] = best;
        }
    }
    sat_free(D);
    if (loo_max)
        *loo_max = worst;
    if (!n)
        return 0.38f;
    /* typical distance between the user's own repetitions (median), with a
     * little slack; one odd sample must not loosen it for all */
    for (int i = 1; i < n; i++)
        for (int j = i; j > 0 && costs[j] < costs[j - 1]; j--) {
            float t = costs[j];
            costs[j] = costs[j - 1];
            costs[j - 1] = t;
        }
    float med = n % 2 ? costs[n / 2] : 0.5f * (costs[n / 2 - 1] + costs[n / 2]);
    return clampf(med + 0.06f, 0.36f, 0.46f);
}

/* ---------------------------------------------------------------- DTW --- */

int kws_det_setup(kws_det_t *d, const kws_model_t *m)
{
    int cells = 0;
    for (int j = 0; m && j < m->ntpl; j++) {
        d->off[j] = cells;
        cells += m->tpl[j].len;
    }
    if (cells > d->cap) {
        /* some room for learned negatives without reallocating */
        int cap = cells + 4 * KWS_MAX_LEN / 2;
        float *D = sat_calloc_hot(sizeof(float) * (size_t)cap);
        uint16_t *S = sat_calloc_hot(sizeof(uint16_t) * (size_t)cap);
        if (!D || !S) {
            sat_free(D);
            sat_free(S);
            d->cells = 0;
            return -1;
        }
        sat_free(d->D);
        sat_free(d->S);
        d->D = D;
        d->S = S;
        d->cap = cap;
    }
    d->cells = cells;
    kws_det_reset(d, m);
    return 0;
}

void kws_det_free(kws_det_t *d)
{
    sat_free(d->D);
    sat_free(d->S);
    d->D = NULL;
    d->S = NULL;
    d->cap = d->cells = 0;
}

void kws_det_reset(kws_det_t *d, const kws_model_t *m)
{
    for (int i = 0; i < d->cells; i++) {
        d->D[i] = 1e9f;
        d->S[i] = 0;
    }
    if (m && m->ntpl && d->t == 0)
        memcpy(d->mean, m->mean_init, sizeof d->mean);
    d->last_cost = 9;
    d->last_tpl = -1;
}

SAT_HOT float kws_det_push(kws_det_t *d, const kws_model_t *m, const float *mfcc, int speech, float thr)
{
    float xc[KWS_NFEAT], x[KWS_NDIM];
    if (d->t == 0)
        memcpy(d->mean, m->mean_init, sizeof d->mean);
    if (speech)
        for (int c = 0; c < KWS_NFEAT; c++)
            d->mean[c] += 0.004f * (mfcc[c] - d->mean[c]);     /* ~4 s of speech */
    for (int c = 0; c < KWS_NFEAT; c++)
        xc[c] = mfcc[c] - d->mean[c];
    make_vector(x, xc, d->t >= 2 ? d->hist[1] : xc);
    memcpy(d->hist[1], d->hist[0], sizeof d->hist[0]);
    memcpy(d->hist[0], xc, sizeof xc);
    const int32_t t = d->t++;
    float tcost[KWS_MAX_TPL];

    float best = 9;
    int besttpl = -1, bestspan = 0;
    /* the state must match the model (kws_det_setup after every change) */
    int ncheck = 0;
    for (int j = 0; j < m->ntpl; j++)
        ncheck += m->tpl[j].len;
    if (!d->D || ncheck != d->cells) {
        d->last_cost = 9;
        d->last_tpl = -1;
        d->last_matches = 0;
        d->last_span = 0;
        d->last_neg = 9;
        return 9;
    }
    const uint16_t t16 = (uint16_t)t;
    int16_t xq[KWS_NDIM];
    for (int c = 0; c < KWS_NDIM; c++)
        xq[c] = (int16_t)lrintf(clampf(x[c], -1.0f, 1.0f) * 32767.0f);
    for (int j = 0; j < m->ntpl; j++) {
        const kws_template_t *tp = &m->tpl[j];
        float *D = d->D + d->off[j];
        uint16_t *S = d->S + d->off[j];
        const float maxspan = 1.7f * tp->len;
        /* new column, in place: prev_diag holds D[i-1] of the previous column.
         * A path longer than the longest acceptable match can never succeed:
         * dropping it lets a fresh start take the cell. */
        float prev_diag = 1e9f;
        uint16_t prev_diag_s = 0;
        const kws_feat_t *f = tp->f;
        /* ages (t - start) in 16-bit modular arithmetic: exact while < 65536 */
#define AGE(s_) ((float)(uint16_t)(t16 - (s_)))
        for (int i = 0; i < tp->len; i++, f += KWS_FSTRIDE) {
            float dist = maxf(0.0f, 1.0f - dot_tpl(xq, f));
            float old = D[i];
            uint16_t old_s = S[i];
            if (old < 1e8f && AGE(old_s) + 1 > maxspan)
                old = 1e9f;
            float cand = 1e9f, n = 1;
            uint16_t cs = t16;
            if (i == 0) {
                cand = 2 * dist;                    /* fresh start */
                n = tp->len + 1;
            } else if (prev_diag < 1e8f) {
                cand = prev_diag + 2 * dist;        /* diagonal */
                cs = prev_diag_s;
                n = tp->len + AGE(cs) + 1;
            }
            /* normalized costs compared as v / nv < cand / n, written
             * without divisions (all terms are positive) */
            if (i > 0 && D[i - 1] < 1e8f) {         /* vertical: this column */
                float v = D[i - 1] + dist, nv = tp->len + AGE(S[i - 1]) + 1;
                if (cand >= 1e8f || v * n < cand * nv) {
                    cand = v;
                    cs = S[i - 1];
                    n = nv;
                }
            }
            if (old < 1e8f) {                       /* horizontal */
                float h = old + dist, nh = tp->len + AGE(old_s) + 1;
                if (cand >= 1e8f || h * n < cand * nh) {
                    cand = h;
                    cs = old_s;
                }
            }
            prev_diag = old;
            prev_diag_s = old_s;
            D[i] = cand;
            S[i] = cs;
        }
        int L = tp->len;
        float span = AGE(S[L - 1]) + 1;
#undef AGE
        tcost[j] = 9;
        if (D[L - 1] < 1e8f && span >= 0.6f * L && span <= 1.7f * L) {
            float cost = D[L - 1] / (L + span);
            tcost[j] = cost;
            if (tp->negative)
                continue;
            if (cost < best) {
                best = cost;
                besttpl = j;
                bestspan = (int)span;
            }
        }
    }
    int matches = 0;
    float bestneg = 9;
    if (besttpl >= 0)
        for (int j = 0; j < m->ntpl; j++) {
            if (strcmp(m->tpl[j].keyword, m->tpl[besttpl].keyword))
                continue;
            if (m->tpl[j].negative) {
                if (tcost[j] < bestneg)
                    bestneg = tcost[j];
            } else if (tcost[j] < thr) {
                matches++;
            }
        }
    d->last_neg = bestneg;
    d->last_cost = best;
    d->last_tpl = besttpl;
    d->last_matches = matches;
    d->last_span = bestspan;
    return best;
}
