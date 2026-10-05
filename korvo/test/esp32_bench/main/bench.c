/*
 * The firmware's wake word path on the ESP32 itself (xtensa, FPU, PSRAM),
 * run under Espressif's QEMU with -icount (1 instruction = 1 virtual ns, so
 * the "us" printed below are thousands of instructions):
 *
 *  1. builds the templates from the server's recordings (embedded) exactly
 *     as the device does when they arrive, and the automatic threshold;
 *  2. replays recordings through engine_process() (STFT, NS, MFCC, DTW,
 *     decision) and counts the wakes, and runs silence (no wake);
 *  3. reports the instructions per 16 ms hop.
 *
 * data/recordings.bin: u32 count, then per recording u32 length + WAV bytes.
 */
#include <stdio.h>
#include <string.h>

#include "engine.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "fft.h"
#include "kws.h"
#include "mixer.h"
#include "ns.h"
#include "resample.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

extern const uint8_t rec_start[] asm("_binary_recordings_bin_start");
int g_wakes;
static kws_det_t det;

static int nrec;
static const uint8_t *recs[32];
static uint32_t rec_len[32];

static int16_t *pcm_of(int i, int *n)
{
    int rate, ch;
    const int16_t *p = wav_parse(recs[i], rec_len[i], n, &rate, &ch);
    if (!p)
        return NULL;
    int16_t *c = heap_caps_malloc((size_t)*n * 2, MALLOC_CAP_SPIRAM);
    memcpy(c, p, (size_t)*n * 2);
    return c;
}

/* returns wakes; *us = engine time over the stream, *hops */
static int stream(const int16_t *pcm, int n, double *us, int *hops)
{
    int before = g_wakes;
    int lead = 24000, total = lead + n + 24000;
    int16_t hop[HOP];
    for (int pos = 0; pos + HOP <= total; pos += HOP) {
        int voiced = 0;
        for (int i = 0; i < HOP; i++) {
            int k = pos + i - lead;
            hop[i] = pcm && k >= 0 && k < n ? pcm[k] : 0;
            voiced |= hop[i] > 300 || hop[i] < -300;
        }
        int64_t t0 = esp_timer_get_time();
        engine_process(hop, voiced, 0);     /* crude VAD: the AFE's on the device */
        *us += (double)(esp_timer_get_time() - t0);
        (*hops)++;
    }
    return g_wakes - before;
}

void app_main(void)
{
    uint32_t cnt;
    memcpy(&cnt, rec_start, 4);
    size_t off = 4;
    for (uint32_t i = 0; i < cnt && nrec < 32; i++) {
        memcpy(&rec_len[nrec], rec_start + off, 4);
        recs[nrec] = rec_start + off + 4;
        off += 4 + rec_len[nrec];
        nrec++;
    }
    printf("\nBENCH: %d recordings, %u KB internal / %u KB PSRAM free\n", nrec,
           (unsigned)(heap_caps_get_free_size(MALLOC_CAP_INTERNAL) / 1024),
           (unsigned)(heap_caps_get_free_size(MALLOC_CAP_SPIRAM) / 1024));
    fft_init();
    kws_init();
    mixer_init(1);
    engine_init();
    engine_link(LINK_ONLINE);

    /* 1. templates, as when they arrive from the server */
    kws_model_t *m = heap_caps_calloc(1, sizeof *m, MALLOC_CAP_SPIRAM);
    int64_t t0 = esp_timer_get_time();
    for (int i = 0; i < nrec; i++) {
        int n;
        int16_t *pcm = pcm_of(i, &n);
        kws_template_t t;
        memset(&t, 0, sizeof t);
        int64_t a = esp_timer_get_time();
        int ok = pcm && kws_make_template(&t, pcm, n) == 0;
        printf("BENCH: template %d: %s, %d frames, %lld kinstr\n", i, ok ? "ok" : "no speech", ok ? t.len : 0,
               esp_timer_get_time() - a);
        if (ok) {
            snprintf(t.keyword, sizeof t.keyword, "dis_lapin");
            kws_model_add(m, &t);
        }
        heap_caps_free(pcm);
    }
    kws_model_finish(m);
    int64_t a = esp_timer_get_time();
    float loo, thr = kws_suggest_threshold(m, &loo);
    printf("BENCH: %d templates in %lld kinstr, threshold %.4f (spread %.4f) in %lld kinstr\n", m->npos,
           a - t0, thr, loo, esp_timer_get_time() - a);
    engine_set_model(m, thr);

    /* 2. replay */
    double us = 0;
    int hops = 0, fired = 0;
    for (int i = 0; i < nrec; i++) {
        int n;
        int16_t *pcm = pcm_of(i, &n);
        int w = stream(pcm, n, &us, &hops);
        fired += w > 0;
        printf("BENCH: replay %d: %d wake(s)\n", i, w);
        heap_caps_free(pcm);
        engine_cancel("bench", 0);
        stream(NULL, 0, &us, &hops);         /* let the refractory period pass */
    }
    double us_rec = us / hops;
    int silent = 0, h2 = 0;
    double us2 = 0;
    for (int k = 0; k < 5; k++)
        silent += stream(NULL, 16000 * 2, &us2, &h2);
    printf("BENCH: %d / %d recordings woke; silence: %d wakes\n", fired, nrec, silent);
    printf("BENCH: engine_process: %.0f kinstr per 16 ms hop with %d templates (%.0f during silence)\n", us_rec,
           m->npos, us2 / h2);
    /* 3. where the time goes, per 16 ms hop */
    {
        static stft_t st;
        static ns_t ns;
        static agc_t agc;
        static float x[HOP], feat[KWS_NFEAT];
        static cfloat X[NBIN];
        ns_init(&ns);
        agc_init(&agc);
        for (int i = 0; i < HOP; i++)
            x[i] = 0.01f * (float)((i * 7919) % 200 - 100) / 100.0f;
        const int N = 200;
        int64_t t = esp_timer_get_time();
        for (int k = 0; k < N; k++)
            stft_push(&st, x, X);
        int64_t t_stft = esp_timer_get_time() - t;
        t = esp_timer_get_time();
        for (int k = 0; k < N; k++)
            ns_process(&ns, X, X, -8, 6);
        int64_t t_ns = esp_timer_get_time() - t;
        t = esp_timer_get_time();
        for (int k = 0; k < N; k++)
            kws_features(X, feat);
        int64_t t_mf = esp_timer_get_time() - t;
        kws_det_setup(&det, m);
        t = esp_timer_get_time();
        for (int k = 0; k < N; k++)
            kws_det_push(&det, m, feat, 1, 0.4f);
        int64_t t_dtw = esp_timer_get_time() - t;
        t = esp_timer_get_time();
        for (int k = 0; k < N; k++)
            agc_process(&agc, x, HOP, 1, -20, 24);
        int64_t t_agc = esp_timer_get_time() - t;
        int cells = 0;
        for (int i = 0; i < m->ntpl; i++)
            cells += m->tpl[i].len;
        printf("BENCH: per hop (kinstr): stft %.1f, ns %.1f, mfcc %.1f, dtw %.1f (%d cells, %.0f instr/cell), agc %.1f\n",
               t_stft / (double)N, t_ns / (double)N, t_mf / (double)N, t_dtw / (double)N, cells,
               t_dtw * 1000.0 / N / cells, t_agc / (double)N);
    }
    {
        float worst = 0;
        for (int i = 0; i < 20000; i++) {
            float x = 1e-12f * powf(1.0035f, (float)i);     /* 1e-12 .. 1e18 */
            float e = fabsf(sat_recipf(x) * x - 1.0f);
            worst = e > worst ? e : worst;
        }
        printf("BENCH: recip0.s + 2 Newton steps: max relative error %.2e\n", worst);
    }
    /* 4. the speaker path: one 10 ms period of mixer_render, TTS (24 kHz
     * mono, x2 interpolation), then TTS over music (48 kHz stereo) */
    {
        mixer_cfg_t mc;
        mixer_get_cfg(&mc);
        mc.volume = 60;
        mixer_set_cfg(&mc);
        static int16_t pcm[48000];
        for (int i = 0; i < 48000; i++)
            pcm[i] = (int16_t)(8000 * sinf(6.2831853f * 440 * i / 24000.0f));
        static int16_t out[OUT_PERIOD];
        mixer_open(1, SK_TTS, 24000, 1, 0, 0, 50);
        mixer_write(1, pcm, 24000);
        for (int k = 0; k < 5; k++)
            mixer_render(out, 0);
        int64_t t0 = esp_timer_get_time();
        for (int k = 0; k < 50; k++)
            mixer_render(out, 0);
        int64_t tts = esp_timer_get_time() - t0;
        mixer_open(2, SK_MEDIA, 48000, 2, 0, 0, 50);
        mixer_write(2, pcm, 48000);
        mixer_write(2, pcm, 48000);
        for (int k = 0; k < 5; k++)
            mixer_render(out, 0);
        t0 = esp_timer_get_time();
        for (int k = 0; k < 20; k++)
            mixer_render(out, 0);
        int64_t both = esp_timer_get_time() - t0;
        mixer_stop_all();
        for (int k = 0; k < 50; k++)
            mixer_render(out, 0);
        t0 = esp_timer_get_time();
        for (int k = 0; k < 50; k++)
            mixer_render(out, 0);
        int64_t idle = esp_timer_get_time() - t0;
        mixer_open(3, SK_TTS, 48000, 1, 0, 0, 50);
        mixer_write(3, pcm, 48000);
        for (int k = 0; k < 5; k++)
            mixer_render(out, 0);
        t0 = esp_timer_get_time();
        for (int k = 0; k < 50; k++)
            mixer_render(out, 0);
        int64_t mono48 = esp_timer_get_time() - t0;
        mixer_stop_all();
        for (int k = 0; k < 50; k++)
            mixer_render(out, 0);
        /* the interpolator alone */
        interp_t ip;
        interp_init(&ip, 2);
        float acc = 0, in = 0.1f;
        t0 = esp_timer_get_time();
        for (int k = 0; k < 480 * 10; k++) {
            if (ip.phase == 0)
                interp_push(&ip, 1, &in);
            float o;
            interp_output(&ip, 1, &o);
            acc += o;
            if (++ip.phase == 2)
                ip.phase = 0;
        }
        int64_t interp = esp_timer_get_time() - t0;
        printf("BENCH: mixer_render per 10 ms (kinstr): TTS 24k %.1f, TTS + music %.1f, 48k mono %.1f, silence %.1f;"
               " interpolator alone %.1f (%g)\n", tts / 50.0, both / 20.0, mono48 / 50.0, idle / 50.0, interp / 10.0, acc);
        printf("BENCH: wake word data: %u B features + %u B DTW state\n", (unsigned)kws_model_bytes(m),
               (unsigned)(det.cap * (sizeof(float) + sizeof(uint16_t))));
        printf("BENCH: main task stack free: %u B\n", (unsigned)uxTaskGetStackHighWaterMark(NULL));
    }
    printf("BENCH: done\n");
}
