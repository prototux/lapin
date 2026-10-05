#include "kws.h"

#include <dirent.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include "fft.h"
#include "ns.h"

static float mel_w[KWS_NMEL][NBIN];
static int mel_k0[KWS_NMEL], mel_k1[KWS_NMEL];
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
    for (int b = 0; b < KWS_NMEL; b++) {
        mel_k0[b] = NBIN;
        mel_k1[b] = 0;
        for (int k = 0; k < NBIN; k++) {
            float w = 0;
            if (k > c[b] && k <= c[b + 1])
                w = (k - c[b]) / (c[b + 1] - c[b]);
            else if (k > c[b + 1] && k < c[b + 2])
                w = (c[b + 2] - k) / (c[b + 2] - c[b + 1]);
            mel_w[b][k] = w;
            if (w > 0) {
                if (k < mel_k0[b])
                    mel_k0[b] = k;
                mel_k1[b] = k;
            }
        }
        if (mel_k0[b] > mel_k1[b]) {    /* narrow low bands: nearest bin */
            int k = (int)lrintf(c[b + 1]);
            mel_k0[b] = mel_k1[b] = k;
            mel_w[b][k] = 1;
        }
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
        e[b] = 0;
        for (int k = mel_k0[b]; k <= mel_k1[b]; k++)
            e[b] += mel_w[b][k] * (crealf(Y[k]) * crealf(Y[k]) + cimagf(Y[k]) * cimagf(Y[k]));
        if (e[b] > emax)
            emax = e[b];
    }
    /* floor relative to the strongest band: weak bands, where noise and
     * its suppression dominate, look the same in clean templates and noisy
     * input */
    const float floor = emax * powf(10, -g_kws_floor_db / 10) + 1e-6f;
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

/* ---------------------------------------------------------------- WAV --- */

int wav_write(const char *path, const int16_t *pcm, int n)
{
    FILE *f = fopen(path, "wb");
    if (!f)
        return -1;
    uint32_t data = (uint32_t)n * 2, riff = 36 + data, rate = SR, brate = SR * 2, fmtlen = 16;
    uint16_t fmt = 1, ch = 1, align = 2, bits = 16;
    fwrite("RIFF", 1, 4, f);
    fwrite(&riff, 4, 1, f);
    fwrite("WAVEfmt ", 1, 8, f);
    fwrite(&fmtlen, 4, 1, f);
    fwrite(&fmt, 2, 1, f);
    fwrite(&ch, 2, 1, f);
    fwrite(&rate, 4, 1, f);
    fwrite(&brate, 4, 1, f);
    fwrite(&align, 2, 1, f);
    fwrite(&bits, 2, 1, f);
    fwrite("data", 1, 4, f);
    fwrite(&data, 4, 1, f);
    fwrite(pcm, 2, (size_t)n, f);
    return fclose(f);
}

int16_t *wav_read(const char *path, int *n, int *rate)
{
    FILE *f = fopen(path, "rb");
    if (!f)
        return NULL;
    char id[4];
    uint32_t sz;
    uint16_t fmt = 0, ch = 0, bits = 0;
    uint32_t r = 0;
    int16_t *pcm = NULL;
    if (fread(id, 1, 4, f) != 4 || memcmp(id, "RIFF", 4) || fread(&sz, 4, 1, f) != 1 ||
        fread(id, 1, 4, f) != 4 || memcmp(id, "WAVE", 4))
        goto out;
    while (fread(id, 1, 4, f) == 4 && fread(&sz, 4, 1, f) == 1) {
        if (!memcmp(id, "fmt ", 4)) {
            uint8_t b[16];
            if (sz < 16 || fread(b, 1, 16, f) != 16)
                goto out;
            memcpy(&fmt, b, 2);
            memcpy(&ch, b + 2, 2);
            memcpy(&r, b + 4, 4);
            memcpy(&bits, b + 14, 2);
            fseek(f, (long)(sz - 16 + (sz & 1)), SEEK_CUR);
        } else if (!memcmp(id, "data", 4)) {
            if (fmt != 1 || ch != 1 || bits != 16 || sz > 64u * 1024 * 1024)
                goto out;
            pcm = malloc(sz);
            if (!pcm)
                goto out;
            *n = (int)(fread(pcm, 2, sz / 2, f));
            *rate = (int)r;
            break;
        } else {
            fseek(f, (long)(sz + (sz & 1)), SEEK_CUR);
        }
    }
out:
    fclose(f);
    return pcm;
}

/* ---------------------------------------------------------- Templates --- */

/* Features of a recorded sample: same front end as the live beams (STFT,
 * noise suppression, MFCC), trimmed to the spoken word. */
static int make_template(kws_template_t *t, const int16_t *pcm, int n)
{
    static float feats[400][KWS_NFEAT];
    float snr[400];
    stft_t st;
    ns_t ns;
    memset(&st, 0, sizeof st);
    ns_init(&ns);
    int nf = 0;
    float hop[HOP];
    cfloat X[NBIN];
    for (int pos = 0; pos + HOP <= n && nf < 400; pos += HOP) {
        for (int i = 0; i < HOP; i++)
            hop[i] = pcm[pos + i] / 32768.0f;
        stft_push(&st, hop, X);
        ns_process(&ns, X, X, -20.0f, 6.0f);
        kws_features(X, feats[nf]);
        snr[nf] = ns.snr_db;
        nf++;
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
        return -1;
    a = a > 10 ? a - 2 : 8;
    b = b + 3 < nf ? b + 3 : nf - 1;
    int len = b - a + 1;
    if (len < 12)
        return -1;
    if (len > KWS_MAX_LEN) {
        len = KWS_MAX_LEN;
    }
    t->len = len;
    memset(t->mean, 0, sizeof t->mean);
    for (int i = 0; i < len; i++)
        for (int c = 0; c < KWS_NFEAT; c++)
            t->mean[c] += feats[a + i][c] / len;
    for (int i = 0; i < len; i++) {
        float x[KWS_NFEAT], x2[KWS_NFEAT];
        for (int c = 0; c < KWS_NFEAT; c++) {
            x[c] = feats[a + i][c] - t->mean[c];
            x2[c] = feats[a + i - 2][c] - t->mean[c];   /* a >= 8 */
        }
        make_vector(t->f[i], x, x2);
    }
    return 0;
}

/* Whole-sequence DTW between two templates (symmetric2, cost / (La + Lb)). */
static float dtw_pair(const kws_template_t *a, const kws_template_t *b)
{
    static float D[KWS_MAX_LEN][KWS_MAX_LEN];
    for (int i = 0; i < a->len; i++)
        for (int j = 0; j < b->len; j++) {
            float dot = 0;
            for (int c = 0; c < KWS_NDIM; c++)
                dot += a->f[i][c] * b->f[j][c];
            float d = fmaxf(0.0f, 1.0f - dot);
            if (i == 0 && j == 0)
                D[i][j] = 2 * d;
            else {
                float best = 1e9f;
                if (i > 0 && j > 0)
                    best = D[i - 1][j - 1] + 2 * d;
                if (i > 0 && D[i - 1][j] + d < best)
                    best = D[i - 1][j] + d;
                if (j > 0 && D[i][j - 1] + d < best)
                    best = D[i][j - 1] + d;
                D[i][j] = best;
            }
        }
    return D[a->len - 1][b->len - 1] / (a->len + b->len);
}

float kws_suggest_threshold(const kws_model_t *m, float *loo_max)
{
    /* each sample against the others of its keyword: how far apart the
     * user's own repetitions are */
    float worst = 0, costs[KWS_MAX_TPL];
    int n = 0;
    for (int i = 0; i < m->ntpl; i++) {
        float best = 9;
        if (m->tpl[i].negative)
            continue;
        for (int j = 0; j < m->ntpl; j++)
            if (i != j && !m->tpl[j].negative && !strcmp(m->tpl[i].keyword, m->tpl[j].keyword)) {
                float c = dtw_pair(&m->tpl[i], &m->tpl[j]);
                if (c < best)
                    best = c;
            }
        if (best < 9) {
            worst = fmaxf(worst, best);
            costs[n++] = best;
        }
    }
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

int kws_check_wav(const kws_model_t *m, const char *path, float *cost, int *start_ms, int *end_ms)
{
    int n = 0, rate = 0;
    int16_t *pcm = wav_read(path, &n, &rate);
    if (!pcm || rate != SR) {
        free(pcm);
        return -1;
    }
    static kws_template_t t;
    int ok = make_template(&t, pcm, n) == 0;
    *start_ms = *end_ms = 0;
    *cost = 9;
    /* speech extent, for the user: too early (cut) or too short */
    {
        float peak = 0;
        for (int i = 0; i < n; i++)
            peak = fmaxf(peak, fabsf(pcm[i] / 32768.0f));
        int first = -1, last = -1;
        for (int pos = 0; pos + 320 <= n; pos += 320) {
            double e = 0;
            for (int i = 0; i < 320; i++)
                e += (double)pcm[pos + i] * pcm[pos + i];
            float db = pow_to_db((float)(e / 320) / (32768.0f * 32768.0f));
            if (db > lin_to_db(peak) - 25) {
                if (first < 0)
                    first = pos;
                last = pos + 320;
            }
        }
        if (first >= 0) {
            *start_ms = first * 1000 / SR;
            *end_ms = last * 1000 / SR;
        }
    }
    /* distance to the existing positive samples */
    if (ok)
        for (int j = 0; j < m->ntpl; j++)
            if (!m->tpl[j].negative) {
                float c = dtw_pair(&t, &m->tpl[j]);
                if (c < *cost)
                    *cost = c;
            }
    free(pcm);
    return ok;
}

static int has_suffix(const char *s, const char *suf)
{
    size_t a = strlen(s), b = strlen(suf);
    return a >= b && strcmp(s + a - b, suf) == 0;
}

int kws_load(kws_model_t *m, const char *dir, char *info, size_t infon)
{
    memset(m, 0, sizeof *m);
    size_t o = (size_t)snprintf(info, infon, "[");
    DIR *d = opendir(dir);
    if (!d) {
        snprintf(info + o, infon - o, "]");
        return 0;
    }
    struct dirent *e;
    int nkw = 0;
    while ((e = readdir(d)) && nkw < KWS_MAX_KW) {
        if (e->d_name[0] == '.')
            continue;
        char sub[512];
        snprintf(sub, sizeof sub, "%s/%s", dir, e->d_name);
        struct stat stt;
        if (stat(sub, &stt) || !S_ISDIR(stt.st_mode))
            continue;
        int count = 0, frames = 0, nneg = 0;
        for (int neg = 0; neg < 2; neg++) {
        char sdir[600];
        snprintf(sdir, sizeof sdir, neg ? "%s/negatives" : "%s", sub);
        DIR *d2 = opendir(sdir);
        if (!d2)
            continue;
        struct dirent *f;
        while ((f = readdir(d2)) && m->ntpl < KWS_MAX_TPL && (!neg || nneg < KWS_MAX_NEG)) {
            if (!has_suffix(f->d_name, ".wav"))
                continue;
            char path[800];
            snprintf(path, sizeof path, "%s/%s", sdir, f->d_name);
            int n = 0, rate = 0;
            int16_t *pcm = wav_read(path, &n, &rate);
            if (!pcm)
                continue;
            kws_template_t *t = &m->tpl[m->ntpl];
            if (rate == SR && make_template(t, pcm, n) == 0) {
                snprintf(t->keyword, sizeof t->keyword, "%.47s", e->d_name);
                t->negative = neg;
                m->ntpl++;
                if (neg) {
                    nneg++;
                } else {
                    m->npos++;
                    count++;
                    frames += t->len;
                }
            } else {
                LOGI("kws: %s: no usable speech, skipped", path);
            }
            free(pcm);
        }
        closedir(d2);
        }
        if (count) {
            char name[100];
            size_t k = 0;
            for (const char *p = e->d_name; *p && k < sizeof name - 1; p++)
                if (*p != '"' && *p != '\\')
                    name[k++] = *p;
            name[k] = 0;
            o += (size_t)snprintf(info + o, infon - o,
                                  "%s{\"name\":\"%s\",\"templates\":%d,\"negatives\":%d,\"avg_ms\":%d}",
                                  nkw ? "," : "", name, count, nneg, frames * 16 / count);
            nkw++;
        }
    }
    closedir(d);
    snprintf(info + o, infon - o, "]");
    for (int i = 0; i < m->ntpl; i++)
        if (!m->tpl[i].negative)
            for (int c = 0; c < KWS_NFEAT; c++)
                m->mean_init[c] += m->tpl[i].mean[c] / (m->npos ? m->npos : 1);
    return m->npos;
}

/* ---------------------------------------------------------------- DTW --- */

void kws_det_reset(kws_det_t *d, const kws_model_t *m)
{
    for (int j = 0; j < KWS_MAX_TPL; j++)
        for (int i = 0; i < KWS_MAX_LEN; i++) {
            d->D[j][i] = 1e9f;
            d->S[j][i] = 0;
        }
    if (m && m->ntpl && d->t == 0)
        memcpy(d->mean, m->mean_init, sizeof d->mean);
    d->last_cost = 9;
    d->last_tpl = -1;
}

float kws_det_push(kws_det_t *d, const kws_model_t *m, const float *mfcc, int speech, float thr)
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
    const int64_t t = d->t++;
    float tcost[KWS_MAX_TPL];

    float best = 9;
    int besttpl = -1, bestspan = 0;
    for (int j = 0; j < m->ntpl; j++) {
        const kws_template_t *tp = &m->tpl[j];
        float *D = d->D[j];
        int64_t *S = d->S[j];
        const float maxspan = 1.7f * tp->len;
        /* new column, in place: prev_diag holds D[i-1] of the previous column.
         * A path longer than the longest acceptable match can never succeed:
         * dropping it lets a fresh start take the cell. */
        float prev_diag = 1e9f;
        int64_t prev_diag_s = 0;
        for (int i = 0; i < tp->len; i++) {
            float dot = 0;
            for (int c = 0; c < KWS_NDIM; c++)
                dot += x[c] * tp->f[i][c];
            float dist = fmaxf(0.0f, 1.0f - dot);
            float old = D[i];
            int64_t old_s = S[i];
            if (old < 1e8f && (float)(t - old_s + 1) > maxspan)
                old = 1e9f;
            float cand = 1e9f, n = 1;
            int64_t cs = t;
            if (i == 0) {
                cand = 2 * dist;                    /* fresh start */
                n = tp->len + 1;
            } else if (prev_diag < 1e8f) {
                cand = prev_diag + 2 * dist;        /* diagonal */
                cs = prev_diag_s;
                n = tp->len + (float)(t - cs) + 1;
            }
            if (i > 0 && D[i - 1] < 1e8f) {         /* vertical: this column */
                float v = D[i - 1] + dist, nv = tp->len + (float)(t - S[i - 1]) + 1;
                if (cand >= 1e8f || v / nv < cand / n) {
                    cand = v;
                    cs = S[i - 1];
                    n = nv;
                }
            }
            if (old < 1e8f) {                       /* horizontal */
                float h = old + dist, nh = tp->len + (float)(t - old_s) + 1;
                if (cand >= 1e8f || h / nh < cand / n) {
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
        float span = (float)(t - S[L - 1] + 1);
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
