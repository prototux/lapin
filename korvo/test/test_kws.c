/*
 * Offline tests of the wake word path with the server's real recordings
 * (fetched through the protocol by hostsat / ws_bridge.py --dump DIR):
 *
 *   test_kws DIR [other.wav ...]
 *
 *  1. every recording replayed through the full engine (STFT, NS, MFCC,
 *     DTW, candidate picking, coverage gate) fires the wake word;
 *  2. leave-one-out: the same with that recording removed from the model;
 *  3. silence, white noise and pink-ish noise (60 s each) never fire;
 *  4. other speech files given on the command line: wakes are reported;
 *  5. --trace FILE writes the per-frame DTW cost of a recording (to compare
 *     with the satellite's reference implementation).
 * The VAD stands in for the AFE's (ns.c speech probability on the input).
 */
#define _GNU_SOURCE
#include <dirent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include "engine.h"
#include "fft.h"
#include "kws.h"
#include "mixer.h"
#include "ns.h"
#include "templates.h"

static int wakes;
static int fails;

int sat_send_text(const char *json)
{
    if (strstr(json, "\"type\":\"wake\""))
        wakes++;
    return 0;
}
int sat_send_audio(const int16_t *pcm, int n) { return 0; }
void sat_templates_begin(void) {}
void sat_template_msg(const char *kw, const char *name, const char *b64, size_t len, int index, int count) {}
void sat_template_done(int count) {}

#define CHECK(cond, ...)                                                                                     \
    do {                                                                                                     \
        if (cond) {                                                                                          \
            printf("  ok    ");                                                                              \
        } else {                                                                                             \
            printf("  FAIL  ");                                                                              \
            fails++;                                                                                         \
        }                                                                                                    \
        printf(__VA_ARGS__);                                                                                 \
        printf("\n");                                                                                        \
    } while (0)

static int16_t *load_wav(const char *path, int *n)
{
    FILE *f = fopen(path, "rb");
    if (!f)
        return NULL;
    fseek(f, 0, SEEK_END);
    long sz = ftell(f);
    fseek(f, 0, SEEK_SET);
    uint8_t *raw = malloc((size_t)sz);
    size_t got = fread(raw, 1, (size_t)sz, f);
    fclose(f);
    int rate, ch;
    const int16_t *p = wav_parse(raw, got, n, &rate, &ch);
    int16_t *pcm = NULL;
    if (p && rate == 16000 && ch == 1) {
        pcm = malloc((size_t)*n * 2);
        memcpy(pcm, p, (size_t)*n * 2);
    }
    free(raw);
    return pcm;
}

typedef struct {
    char name[64];
    int16_t *pcm;
    int n;
} rec_t;
static rec_t recs[64];
static int nrec;

static kws_model_t *build_model(int skip, float *thr)
{
    kws_model_t *m = calloc(1, sizeof *m);
    for (int i = 0; i < nrec; i++) {
        if (i == skip)
            continue;
        kws_template_t t;
        memset(&t, 0, sizeof t);
        if (kws_make_template(&t, recs[i].pcm, recs[i].n) == 0) {
            snprintf(t.keyword, sizeof t.keyword, "dis_lapin");
            snprintf(t.name, sizeof t.name, "%.39s", recs[i].name);
            kws_model_add(m, &t);
        }
    }
    kws_model_finish(m);
    *thr = kws_suggest_threshold(m, NULL);
    return m;
}

/* noise generators */
static unsigned seed = 12345;
static float white(void)
{
    seed = seed * 1103515245u + 12345u;
    return ((seed >> 8) & 0xFFFF) / 32768.0f - 1.0f;
}

static double t_kws_us, t_frames;

/* Streams a signal through the engine: lead silence, the clip, tail. Returns
 * the number of wakes. trace: per-frame costs. */
static FILE *trace_in;

static int run_stream(const int16_t *pcm, int n, double lead_s, double tail_s, int noise_kind, float noise_db,
                      FILE *trace)
{
    static ns_t vad;
    static stft_t st;
    ns_init(&vad);
    memset(&st, 0, sizeof st);
    int lead = (int)(lead_s * SR), total = lead + n + (int)(tail_s * SR);
    float g = powf(10, noise_db / 20), pink = 0;
    int before = wakes;
    for (int pos = 0; pos + HOP <= total; pos += HOP) {
        int16_t hop[HOP];
        float x[HOP];
        for (int i = 0; i < HOP; i++) {
            int k = pos + i - lead;
            float s = (pcm && k >= 0 && k < n) ? pcm[k] / 32768.0f : 0;
            float w = white();
            pink = 0.98f * pink + 0.2f * w;
            float nz = noise_kind == 1 ? w : noise_kind == 2 ? pink : w * 0.03f;   /* 0: faint floor */
            s += nz * g;
            x[i] = s;
            hop[i] = (int16_t)clampf(s * 32767.0f, -32768, 32767);
        }
        if (trace_in)
            fwrite(hop, 2, HOP, trace_in);
        for (int i = 0; i < HOP; i++)
            x[i] = hop[i] / 32768.0f;
        cfloat X[NBIN];
        stft_push(&st, x, X);
        ns_process(&vad, X, NULL, -20, 6);
        struct timespec a, b;
        clock_gettime(CLOCK_MONOTONIC, &a);
        engine_process(hop, vad.prob > 0.5f, 0);
        clock_gettime(CLOCK_MONOTONIC, &b);
        t_kws_us += (b.tv_sec - a.tv_sec) * 1e6 + (b.tv_nsec - a.tv_nsec) / 1e3;
        t_frames++;
        if (trace) {
            engine_status_t s = engine_status();
            fprintf(trace, "%.4f\n", s.kws_cost);
        }
    }
    return wakes - before;
}

/* level of the loudest 10 % of 20 ms frames, dBFS */
static float loud_db(const int16_t *pcm, int n)
{
    int nf = n / 320;
    float *db = malloc(sizeof(float) * (size_t)(nf > 0 ? nf : 1));
    for (int f = 0; f < nf; f++) {
        double e = 0;
        for (int i = 0; i < 320; i++)
            e += (double)pcm[f * 320 + i] * pcm[f * 320 + i];
        db[f] = 10 * log10f((float)(e / 320 / (32768.0 * 32768.0)) + 1e-12f);
    }
    for (int i = 1; i < nf; i++)
        for (int j = i; j > 0 && db[j] < db[j - 1]; j--) {
            float t = db[j];
            db[j] = db[j - 1];
            db[j - 1] = t;
        }
    float r = nf ? db[nf * 9 / 10] : -90;
    free(db);
    return r;
}

static void idle_engine(void)
{
    /* let the refractory period pass and the state return to idle */
    engine_cancel("test", 0);
    run_stream(NULL, 0, 2.0, 0, 0, -60, NULL);
}

static int cmp_name(const void *a, const void *b) { return strcmp(((const rec_t *)a)->name, ((const rec_t *)b)->name); }

int main(int argc, char **argv)
{
    if (argc < 2) {
        fprintf(stderr, "usage: test_kws DIR [--trace FILE.wav OUT] [speech.wav ...]\n");
        return 2;
    }
    fft_init();
    kws_init();
    mixer_init(1);
    engine_init();
    engine_link(LINK_ONLINE);
    DIR *d = opendir(argv[1]);
    struct dirent *e;
    while (d && (e = readdir(d)) && nrec < 64) {
        if (!strstr(e->d_name, ".wav"))
            continue;
        char p[600];
        snprintf(p, sizeof p, "%.300s/%.250s", argv[1], e->d_name);
        recs[nrec].pcm = load_wav(p, &recs[nrec].n);
        if (recs[nrec].pcm) {
            snprintf(recs[nrec].name, sizeof recs[nrec].name, "%.63s", e->d_name);
            nrec++;
        }
    }
    if (d)
        closedir(d);
    qsort(recs, (size_t)nrec, sizeof recs[0], cmp_name);
    printf("%d recordings in %s\n", nrec, argv[1]);
    if (!nrec)
        return 1;

    float thr;
    kws_model_t *m = build_model(-1, &thr);
    int npos = m->npos;
    printf("model: %d templates, %.1f KB of features, suggested threshold %.3f\n", m->ntpl,
           kws_model_bytes(m) / 1024.0, thr);
    {
        /* the flash store: save, load back, compare */
        char path[] = "/tmp/test_kws_store_XXXXXX";
        int fd = mkstemp(path);
        close(fd);
        kws_model_t back;
        float thr2 = 0;
        int ok = tpl_save_file(path, m, thr) == 0 && tpl_load_file(path, &back, &thr2) == m->ntpl && thr2 == thr &&
                 back.npos == m->npos;
        for (int i = 0; ok && i < m->ntpl; i++)
            ok = back.tpl[i].len == m->tpl[i].len && !strcmp(back.tpl[i].name, m->tpl[i].name) &&
                 !memcmp(back.tpl[i].f, m->tpl[i].f, KWS_TPL_BYTES(m->tpl[i].len)) &&
                 !memcmp(back.tpl[i].mean, m->tpl[i].mean, sizeof back.tpl[i].mean);
        FILE *f = fopen(path, "rb");
        fseek(f, 0, SEEK_END);
        long sz = ftell(f);
        fclose(f);
        CHECK(ok, "template store round trip (%ld bytes on flash)", sz);
        if (ok)
            kws_model_free(&back);
        remove(path);
    }
    engine_set_model(m, thr);

    /* trace mode */
    for (int a = 2; a + 2 < argc; a++)
        if (!strcmp(argv[a], "--trace")) {
            int n;
            int16_t *pcm = load_wav(argv[a + 1], &n);
            FILE *f = fopen(argv[a + 2], "w");
            char rawp[600];
            snprintf(rawp, sizeof rawp, "%s.raw", argv[a + 2]);
            trace_in = fopen(rawp, "wb");
            if (pcm && f) {
                engine_cfg_t c;
                engine_get_cfg(&c);
                c.kws_enabled = 1;
                engine_set_cfg(&c);
                int w = run_stream(pcm, n, 1.5, 1.0, 0, -60, f);
                printf("trace written to %s (%d wakes)\n", argv[a + 2], w);
            }
            if (f)
                fclose(f);
            if (trace_in)
                fclose(trace_in);
            return 0;
        }

    printf("\n1. each recording replayed (in the model):\n");
    /* a recording can only fire if 2 templates match it (auto min matches
     * with 3+ samples): itself and at least one other within the threshold */
    int fired = 0, expect = 0;
    for (int i = 0; i < nrec; i++) {
        kws_template_t t;
        memset(&t, 0, sizeof t);
        int usable = kws_make_template(&t, recs[i].pcm, recs[i].n) == 0;
        float nearest = 9;
        for (int j = 0; usable && j < m->ntpl; j++)
            if (strcmp(m->tpl[j].name, recs[i].name)) {
                float c = kws_dtw_pair(&t, &m->tpl[j]);
                nearest = c < nearest ? c : nearest;
            }
        kws_template_free(&t);
        int can = usable && nearest < thr;
        expect += usable;
        idle_engine();
        int w = run_stream(recs[i].pcm, recs[i].n, 1.5, 1.5, 0, -60, NULL);
        fired += w > 0 && usable;
        if (w != 1)
            printf("        %s: %d wake(s)%s (nearest other sample %.3f, level %.1f dBFS)\n", recs[i].name, w,
                   !usable ? ", no usable speech" : !can ? ", an outlier: 2 matching templates are required" : "",
                   nearest, loud_db(recs[i].pcm, recs[i].n));
    }
    CHECK(fired >= expect - 1, "%d / %d usable recordings fire (%d recordings, %d templates)", fired, expect, nrec,
          npos);

    printf("\n2. leave-one-out (recording not in the model):\n");
    int loo = 0, loo_n = 0;
    for (int i = 0; i < nrec; i++) {
        float t2;
        kws_model_t *m2 = build_model(i, &t2);
        engine_set_model(m2, t2);
        idle_engine();
        int w = run_stream(recs[i].pcm, recs[i].n, 1.5, 1.5, 0, -60, NULL);
        engine_status_t s = engine_status();
        printf("        %-24s thr %.3f -> %s\n", recs[i].name, s.kws_threshold, w ? "wake" : "missed");
        loo += w > 0;
        loo_n++;
    }
    CHECK(loo * 10 >= loo_n * 7, "%d / %d fire with their recording left out", loo, loo_n);
    m = build_model(-1, &thr);
    engine_set_model(m, thr);

    printf("\n3. no wake on silence and noise (60 s each):\n");
    idle_engine();
    int w0 = run_stream(NULL, 0, 60, 0, 0, -60, NULL);
    CHECK(w0 == 0, "silence (-90 dBFS floor): %d wakes", w0);
    idle_engine();
    int w1 = run_stream(NULL, 0, 60, 0, 1, -30, NULL);
    CHECK(w1 == 0, "white noise -30 dBFS: %d wakes", w1);
    idle_engine();
    int w2 = run_stream(NULL, 0, 60, 0, 2, -20, NULL);
    CHECK(w2 == 0, "pink-ish noise: %d wakes", w2);

    printf("\n4. recordings in white noise, 20 dB below the speech (the AFE's NS is not simulated):\n");
    int fired_noise = 0;
    for (int i = 0; i < nrec; i++) {
        idle_engine();
        fired_noise += run_stream(recs[i].pcm, recs[i].n, 2.0, 1.5, 1, loud_db(recs[i].pcm, recs[i].n) - 20 + 4.8f,
                                  NULL) > 0;
    }
    CHECK(fired_noise >= expect - 3, "%d / %d fire in noise", fired_noise, expect);

    if (argc > 2) {
        printf("\n5. other recordings (wakes reported, not checked):\n");
        for (int a = 2; a < argc; a++) {
            int n;
            int16_t *pcm = load_wav(argv[a], &n);
            if (!pcm)
                continue;
            idle_engine();
            int w = run_stream(pcm, n, 1.0, 1.0, 0, -60, NULL);
            printf("        %-40s %5.1f s: %d wake(s)\n", strrchr(argv[a], '/') ? strrchr(argv[a], '/') + 1 : argv[a],
                   n / 16000.0, w);
            free(pcm);
        }
    }
    printf("\nengine_process: %.1f us per 16 ms hop on this PC (%d templates), %.0f hops\n",
           t_kws_us / t_frames, npos, t_frames);
    printf("%s (%d failure%s)\n", fails ? "FAILED" : "PASSED", fails, fails == 1 ? "" : "s");
    return fails ? 1 : 0;
}
