/* Wake word templates from the server: decoding, model building, storage.
 * See templates.h. */
#include "templates.h"

#include <stdlib.h>
#include <string.h>

#include "b64.h"
#include "engine.h"

#define STORE_MAGIC   0x3153574Bu    /* "KWS1" */
#define STORE_VERSION (100 + KWS_FEAT_BITS)  /* bump when the features change (v1: int16) */

typedef struct {
    uint32_t magic, version, ntpl;
    float threshold;
    uint32_t ndim, nfeat, reserved[2];
} store_hdr_t;

typedef struct {
    char keyword[48];
    char name[40];
    int32_t negative, len;
    float mean[KWS_NFEAT];
} store_tpl_t;

static char store_path[256];
static kws_model_t *coll;        /* model being collected */
static kws_model_t *prev;        /* copy of the model in use, for reuse */
static int coll_skipped;

void tpl_init(const char *path) { snprintf(store_path, sizeof store_path, "%s", path); }

/* ------------------------------------------------------------- Store --- */

int tpl_save_file(const char *path, const kws_model_t *m, float thr)
{
    char tmp[300];
    snprintf(tmp, sizeof tmp, "%s.tmp", path);
    FILE *f = fopen(tmp, "wb");
    if (!f)
        return -1;
    store_hdr_t h = {STORE_MAGIC, STORE_VERSION, (uint32_t)m->ntpl, thr, KWS_NDIM, KWS_NFEAT, {0, 0}};
    int ok = fwrite(&h, sizeof h, 1, f) == 1;
    for (int i = 0; i < m->ntpl && ok; i++) {
        const kws_template_t *t = &m->tpl[i];
        store_tpl_t st;
        memset(&st, 0, sizeof st);
        snprintf(st.keyword, sizeof st.keyword, "%s", t->keyword);
        snprintf(st.name, sizeof st.name, "%s", t->name);
        st.negative = t->negative;
        st.len = t->len;
        memcpy(st.mean, t->mean, sizeof st.mean);
        ok = fwrite(&st, sizeof st, 1, f) == 1 &&
             fwrite(t->f, KWS_TPL_BYTES(1), (size_t)t->len, f) == (size_t)t->len;
    }
    ok = fclose(f) == 0 && ok;
    if (!ok) {
        remove(tmp);
        return -1;
    }
    remove(path);
    return rename(tmp, path);
}

int tpl_load_file(const char *path, kws_model_t *m, float *thr)
{
    FILE *f = fopen(path, "rb");
    if (!f)
        return -1;
    kws_model_init(m);
    store_hdr_t h;
    int ok = fread(&h, sizeof h, 1, f) == 1 && h.magic == STORE_MAGIC && h.version == STORE_VERSION &&
             h.ndim == KWS_NDIM && h.nfeat == KWS_NFEAT && h.ntpl <= KWS_MAX_TPL;
    for (uint32_t i = 0; ok && i < h.ntpl; i++) {
        store_tpl_t st;
        kws_template_t t;
        memset(&t, 0, sizeof t);
        ok = fread(&st, sizeof st, 1, f) == 1 && st.len >= 1 && st.len <= KWS_MAX_LEN;
        if (!ok)
            break;
        t.f = sat_calloc_big(KWS_TPL_BYTES(st.len));
        ok = t.f && fread(t.f, KWS_TPL_BYTES(1), (size_t)st.len, f) == (size_t)st.len;
        if (!ok) {
            sat_free(t.f);
            break;
        }
        memcpy(t.keyword, st.keyword, sizeof t.keyword);
        t.keyword[sizeof t.keyword - 1] = 0;
        memcpy(t.name, st.name, sizeof t.name);
        t.name[sizeof t.name - 1] = 0;
        t.negative = st.negative != 0;
        t.len = st.len;
        memcpy(t.mean, st.mean, sizeof t.mean);
        kws_model_add(m, &t);
    }
    fclose(f);
    if (!ok) {
        kws_model_free(m);
        return -1;
    }
    kws_model_finish(m);
    if (thr)
        *thr = h.threshold;
    return m->ntpl;
}

int tpl_load_store(void)
{
    kws_model_t *m = sat_calloc_big(sizeof *m);
    float thr = 0;
    if (!m)
        return -1;
    int n = tpl_load_file(store_path, m, &thr);
    if (n <= 0) {
        sat_free(m);
        return -1;
    }
    LOGI("templates: %d loaded from %s (threshold %.3f)", n, store_path, thr);
    engine_set_model(m, thr);
    return n;
}

void tpl_save_current(void)
{
    float thr = 0;
    kws_model_t *m = engine_model_copy(&thr);
    if (!m)
        return;
    if (tpl_save_file(store_path, m, thr) != 0)
        LOGE("templates: cannot save %s", store_path);
    kws_model_free(m);
    sat_free(m);
}

/* -------------------------------------------------------- Collection --- */

static void drop(kws_model_t **m)
{
    if (*m) {
        kws_model_free(*m);
        sat_free(*m);
        *m = NULL;
    }
}

void tpl_collect_begin(void)
{
    drop(&coll);
    drop(&prev);
    coll = sat_calloc_big(sizeof *coll);
    prev = engine_model_copy(NULL);
    coll_skipped = 0;
}

/* Index of a template with this keyword and name in m, or -1. */
static int find_tpl(const kws_model_t *m, const char *kw, const char *name, int negative)
{
    if (!m)
        return -1;
    for (int i = 0; i < m->ntpl; i++)
        if (m->tpl[i].negative == negative && !strcmp(m->tpl[i].keyword, kw) && !strcmp(m->tpl[i].name, name))
            return i;
    return -1;
}

int tpl_collect_add(const char *keyword, const char *name, const char *b64, size_t n, int index, int count)
{
    if (!coll)
        tpl_collect_begin();
    if (!coll || !keyword[0])
        return -1;
    /* more recordings than room: keep an even spread of them */
    if (count > TPL_MAX_POS && index >= 0) {
        int keep = (int)((long)index * TPL_MAX_POS / count) != (int)((long)(index + 1) * TPL_MAX_POS / count);
        if (!keep)
            return -1;
    }
    if (coll->npos >= TPL_MAX_POS)
        return -1;
    kws_template_t t;
    memset(&t, 0, sizeof t);
    /* already known (same recording): reuse its features */
    int k = find_tpl(prev, keyword, name, 0);
    if (k >= 0) {
        t = prev->tpl[k];
        prev->tpl[k].f = NULL;      /* moved */
        prev->tpl[k].name[0] = 0;
    } else {
        size_t cap = b64_decoded_max(n);
        uint8_t *wav = sat_calloc_big(cap);
        if (!wav)
            return -1;
        int len = b64_decode(b64, n, wav, cap);
        int ns = 0, rate = 0, ch = 0;
        const int16_t *pcm = len > 0 ? wav_parse(wav, (size_t)len, &ns, &rate, &ch) : NULL;
        int ok = pcm && rate == SR && ch == 1 && kws_make_template(&t, pcm, ns) == 0;
        sat_free(wav);
        if (!ok) {
            coll_skipped++;
            LOGI("templates: %s/%s: no usable speech (rate %d, ch %d), skipped", keyword, name, rate, ch);
            return -1;
        }
        snprintf(t.keyword, sizeof t.keyword, "%s", keyword);
        snprintf(t.name, sizeof t.name, "%s", name);
    }
    t.negative = 0;
    if (kws_model_add(coll, &t) != 0) {
        kws_template_free(&t);
        return -1;
    }
    coll->npos++;
    return 0;
}

void tpl_collect_done(int count)
{
    if (!coll) {
        drop(&prev);
        return;
    }
    /* keep the learned negatives of the keywords still enrolled */
    if (prev)
        for (int i = 0; i < prev->ntpl && coll->ntpl < KWS_MAX_TPL; i++) {
            kws_template_t *t = &prev->tpl[i];
            if (!t->negative || !t->f)
                continue;
            int kw_known = 0;
            for (int j = 0; j < coll->ntpl; j++)
                kw_known |= !coll->tpl[j].negative && !strcmp(coll->tpl[j].keyword, t->keyword);
            if (!kw_known)
                continue;
            kws_model_add(coll, t);
            t->f = NULL;
        }
    kws_model_finish(coll);
    drop(&prev);
    float loo = 0, thr = kws_suggest_threshold(coll, &loo);
    LOGI("templates: %d of %d usable (%d skipped), %.1f KB of features, threshold %.3f (spread %.3f)",
         coll->npos, count, coll_skipped, kws_model_bytes(coll) / 1024.0f, thr, loo);
    kws_model_t *m = coll;
    coll = NULL;
    if (m->npos == 0 && count > 0) {
        /* nothing usable although the server has recordings: keep the old
         * model rather than going deaf */
        LOGE("templates: none usable, keeping the previous model");
        kws_model_free(m);
        sat_free(m);
    } else {
        engine_set_model(m, thr);
        sat_store_changed();          /* saved now (host) or once the audio is quiet (device) */
    }
    char ww[256], msg[300];
    engine_wake_words_json(ww, sizeof ww);
    snprintf(msg, sizeof msg, "{\"type\":\"wake_words\",\"wake_words\":%s}", ww);
    sat_send_text(msg);
}
