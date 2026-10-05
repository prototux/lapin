#ifndef SAT_KWS_H
#define SAT_KWS_H

#include "sat_common.h"

/*
 * Personal wake word detector, ported from the ReSpeaker satellite
 * (satellite/engine/src/kws.c). Same front end and matcher:
 *
 * Each 16 ms frame gives 12 MFCCs plus the log energy (speech-gated cepstral
 * mean normalization) and their deltas, scaled to unit length; every
 * template is matched with an online subsequence DTW (symmetric step
 * pattern, free start, cosine distance), so the cost per frame is one DTW
 * column per template.
 *
 * ESP32 adaptations (same math):
 *  - one detector instance (no beams);
 *  - template features stored as Q14 int16 (unit vectors: |x| <= 1), sized
 *    to their actual length instead of a fixed KWS_MAX_LEN stride;
 *  - smaller limits (KWS_MAX_TPL 24, KWS_MAX_LEN 125 frames = 2 s);
 *  - DTW state packed per template, start frames modulo 2^16;
 *  - hot data (features, DTW state) in internal RAM when possible;
 *  - templates are built from WAV data in memory (the server sends them),
 *    not read from a directory.
 */
#define KWS_NCEP     12
#define KWS_NFEAT    (KWS_NCEP + 1)  /* MFCC + relative log energy */
#define KWS_NDIM     (2 * KWS_NFEAT) /* + deltas */
#define KWS_NMEL     32
#define KWS_MAX_LEN  125        /* frames (2 s) */
#define KWS_MAX_TPL  24         /* positive + negative */
#define KWS_MAX_NEG  6          /* negatives kept per keyword */
#define KWS_MAX_KW   2
/* Template features: unit vectors stored as int8 (Q7) by default: 16 KB for
 * 13 templates, small enough to sit in internal RAM, where the DTW reads them
 * every 16 ms (from PSRAM they cost ~3 ms per hop in cache refills on the
 * ESP32). The cost differs from float features by < 0.003 (test_kws). */
#ifndef KWS_FEAT_BITS
#define KWS_FEAT_BITS 8
#endif
#if KWS_FEAT_BITS == 8
/* a frame: 26 int8 scaled to its own largest component, then that component's
 * magnitude as a Q15 uint16 (2 bytes, little endian): 28 bytes */
typedef int8_t kws_feat_t;
#define KWS_Q        127.0f
#define KWS_FSTRIDE  (KWS_NDIM + 2)
#else
typedef int16_t kws_feat_t;
#define KWS_Q        16384.0f   /* feature scale (Q14) */
#define KWS_FSTRIDE  KWS_NDIM
#endif
/* bytes of a template's features */
#define KWS_TPL_BYTES(len) (sizeof(kws_feat_t) * (size_t)(len) * KWS_FSTRIDE)

typedef struct {
    char keyword[48];
    char name[40];          /* source recording (server file name), for dedupe */
    int negative;           /* a false trigger rejected by the server */
    int len;
    float mean[KWS_NFEAT];
    kws_feat_t *f;          /* len frames of KWS_FSTRIDE, owned by the template */
} kws_template_t;

typedef struct {
    int ntpl, npos;
    kws_template_t tpl[KWS_MAX_TPL];
    float mean_init[KWS_NFEAT];
} kws_model_t;

/* DTW state: one column per template, packed (sum of the template lengths,
 * ~640 cells for 13 templates: 3.8 KB instead of 24 KB at the maximum
 * sizes). Start frames are stored modulo 2^16 (spans are < 213 frames). */
typedef struct {
    float *D;
    uint16_t *S;
    int off[KWS_MAX_TPL];       /* first cell of each template */
    int cells, cap;             /* cells in use / allocated */
    float mean[KWS_NFEAT];
    float hist[2][KWS_NFEAT];    /* previous normalized MFCCs, for the deltas */
    int32_t t;
    float last_cost;    /* best normalized cost ending this frame */
    int last_tpl;
    int last_matches;   /* templates of the best keyword under the threshold */
    int last_span;      /* input frames covered by the best match */
    float last_neg;     /* best cost of a negative of the same keyword */
} kws_det_t;

void kws_init(void);
/* Features of an (enhanced) spectrum: 12 MFCCs and the log energy. */
void kws_features(const cfloat *Y, float *mfcc);

/* Builds a template from 16 kHz mono PCM (same front end as the live audio:
 * STFT, noise suppression, MFCC, trimmed to the spoken word). Allocates t->f.
 * Returns 0, or -1 when there is no usable speech. */
int kws_make_template(kws_template_t *t, const int16_t *pcm, int n);
void kws_template_free(kws_template_t *t);

/* Model management. kws_model_add takes ownership of t->f. */
void kws_model_init(kws_model_t *m);
int kws_model_add(kws_model_t *m, const kws_template_t *t);
void kws_model_finish(kws_model_t *m);       /* recomputes npos / mean_init */
void kws_model_free(kws_model_t *m);
/* Moves the features to fast memory (internal RAM on the ESP32) if possible. */
void kws_model_compact(kws_model_t *m);
size_t kws_model_bytes(const kws_model_t *m);

/* Threshold suggested from the spread of the enrolled samples (leave-one-out). */
float kws_suggest_threshold(const kws_model_t *m, float *loo_max);
/* Whole-sequence DTW distance between two templates (for tests). */
float kws_dtw_pair(const kws_template_t *a, const kws_template_t *b);

/* Sizes the DTW state for the model (call after any model change; the
 * memory comes from sat_calloc_hot). Returns 0, or -1 when out of memory. */
int kws_det_setup(kws_det_t *d, const kws_model_t *m);
void kws_det_free(kws_det_t *d);
void kws_det_reset(kws_det_t *d, const kws_model_t *m);
/* Feeds one frame; returns the best normalized cost of a template ending here
 * and counts (last_matches) the templates of that keyword below `thr`. */
float kws_det_push(kws_det_t *d, const kws_model_t *m, const float *mfcc, int speech, float thr);

/* 16-bit PCM WAV in memory: returns a pointer to the samples (inside `wav`),
 * or NULL. */
const int16_t *wav_parse(const uint8_t *wav, size_t len, int *n, int *rate, int *channels);

#endif
