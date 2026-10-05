#ifndef SATD_KWS_H
#define SATD_KWS_H

#include "common.h"

/*
 * Hotword detector (stage 1): personal wake words enrolled from a few spoken
 * samples. Each frame of a beam gives 12 MFCCs (speech-gated cepstral mean
 * normalization) plus their deltas, scaled to unit length; every template is
 * matched with an online
 * subsequence DTW (symmetric step pattern, free start, cosine distance), so
 * the cost per frame is one DTW column per template. One detector instance
 * runs per fixed beam; the beam that matches best gives the talker direction.
 */
#define KWS_NCEP     12
#define KWS_NFEAT    (KWS_NCEP + 1)  /* MFCC + relative log energy */
#define KWS_NDIM     (2 * KWS_NFEAT) /* + deltas */
#define KWS_NMEL     32
#define KWS_MAX_LEN  125        /* frames (2 s) */
#define KWS_MAX_TPL  48         /* positive + negative */
#define KWS_MAX_NEG  20         /* negatives kept per keyword */
#define KWS_MAX_KW   4

typedef struct {
    char keyword[48];
    int negative;           /* a false trigger rejected by the server */
    int len;
    float f[KWS_MAX_LEN][KWS_NDIM];
    float mean[KWS_NFEAT];
} kws_template_t;

typedef struct {
    int ntpl, npos;
    kws_template_t tpl[KWS_MAX_TPL];
    float mean_init[KWS_NFEAT];
} kws_model_t;

typedef struct {
    float D[KWS_MAX_TPL][KWS_MAX_LEN];
    int64_t S[KWS_MAX_TPL][KWS_MAX_LEN];
    float mean[KWS_NFEAT];
    float hist[2][KWS_NFEAT];    /* previous normalized MFCCs, for the deltas */
    int64_t t;
    float last_cost;    /* best normalized cost ending this frame */
    int last_tpl;
    int last_matches;   /* templates of the best keyword under the threshold */
    int last_span;      /* input frames covered by the best match */
    float last_neg;     /* best cost of a negative of the same keyword */
} kws_det_t;

void kws_init(void);
/* Features of an (enhanced) spectrum: 12 MFCCs and the log energy. */
void kws_features(const cfloat *Y, float *mfcc);

/* Loads every <dir>/<keyword>/NAME.wav (16 kHz mono 16-bit) as templates,
 * and <dir>/<keyword>/negatives/NAME.wav as negative examples.
 * Returns the number of templates; info gets a JSON array describing them. */
int kws_load(kws_model_t *m, const char *dir, char *info, size_t infon);

/* Checks a recorded sample: 1 usable, 0 no usable speech, -1 unreadable;
 * cost = distance to the closest positive template, speech extent in ms. */
int kws_check_wav(const kws_model_t *m, const char *path, float *cost, int *start_ms, int *end_ms);
/* Threshold suggested from the spread of the enrolled samples (leave-one-out). */
float kws_suggest_threshold(const kws_model_t *m, float *loo_max);

void kws_det_reset(kws_det_t *d, const kws_model_t *m);
/* Feeds one frame; returns the best normalized cost of a template ending here
 * and counts (last_matches) the templates of that keyword below `thr`. */
float kws_det_push(kws_det_t *d, const kws_model_t *m, const float *mfcc, int speech, float thr);

/* 16 kHz mono 16-bit WAV helpers */
int wav_write(const char *path, const int16_t *pcm, int n);
int16_t *wav_read(const char *path, int *n, int *rate);

#endif
