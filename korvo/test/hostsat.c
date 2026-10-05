/*
 * hostsat: the firmware's portable core (proto, engine, KWS, templates,
 * mixer) running on a PC, connected to the real server through
 * ws_bridge.py (which owns the WebSocket). Used to test the protocol logic
 * end to end without a board:
 *
 *   ws_bridge.py ws://localhost:8765/v1/device -- ./hostsat --id test-korvo-1 \
 *       --input clip.wav --after 3
 *
 * stdin/stdout carry frames from/to the bridge: 1 byte type ('C' connected,
 * 'D' disconnected, 'T' text, 'B' binary) + u32 LE length + payload.
 * The "microphone" is a 16 kHz WAV file fed in real time (as if it were the
 * AFE output); the VAD stands in for the AFE's (ns.c speech probability).
 * Playback is rendered by the mixer in real time and saved to --out.
 */
#define _GNU_SOURCE
#include <getopt.h>
#include <pthread.h>
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
#include "proto.h"
#include "templates.h"

extern int g_log_level;
static pthread_mutex_t out_lock = PTHREAD_MUTEX_INITIALIZER;
static volatile int connected, online, templates_done, running = 1;
static int n_wake_sent, n_audio_frames, n_audio_bytes;

static void put_frame(char type, const void *data, uint32_t len)
{
    pthread_mutex_lock(&out_lock);
    fputc(type, stdout);
    fwrite(&len, 4, 1, stdout);
    fwrite(data, 1, len, stdout);
    fflush(stdout);
    pthread_mutex_unlock(&out_lock);
}

int sat_send_text(const char *json)
{
    if (!connected)
        return -1;
    if (strstr(json, "\"type\":\"wake\""))
        n_wake_sent++;
    fprintf(stderr, "-> %s\n", json);
    put_frame('T', json, (uint32_t)strlen(json));
    return 0;
}

int sat_send_audio(const int16_t *pcm, int n)
{
    if (!connected)
        return -1;
    uint8_t buf[1 + 4096];
    if (n * 2 > 4096)
        n = 2048;
    buf[0] = 0x01;
    memcpy(buf + 1, pcm, (size_t)n * 2);
    n_audio_frames++;
    n_audio_bytes += n * 2;
    put_frame('B', buf, (uint32_t)(1 + n * 2));
    return 0;
}

void sat_templates_begin(void) { tpl_collect_begin(); }
void sat_template_msg(const char *kw, const char *name, const char *b64, size_t len, int index, int count)
{
    int r = tpl_collect_add(kw, name, b64, len, index, count);
    fprintf(stderr, "   template %d/%d %s/%s: %s\n", index + 1, count, kw, name, r == 0 ? "ok" : "skipped");
}
void sat_template_done(int count)
{
    tpl_collect_done(count);
    templates_done = 1;
}

static void on_ui(const char *ev, int arg) { fprintf(stderr, "   [ui] %s %d\n", ev, arg); }

static int read_full(void *p, size_t n)
{
    return fread(p, 1, n, stdin) == n;
}

static void *reader(void *arg)
{
    (void)arg;
    for (;;) {
        int type = fgetc(stdin);
        uint32_t len;
        if (type == EOF || !read_full(&len, 4))
            break;
        char *buf = malloc(len + 1);
        if (!buf || (len && !read_full(buf, len)))
            break;
        buf[len] = 0;
        if (type == 'C') {
            connected = 1;
            char hello[2048];
            if (proto_hello(hello, sizeof hello) > 0)
                sat_send_text(hello);
        } else if (type == 'D') {
            connected = online = 0;
            proto_on_disconnect();
        } else if (type == 'T') {
            if (!strstr(buf, "\"wake_template\""))
                fprintf(stderr, "<- %.300s\n", buf);
            char err[200];
            int r = proto_on_text(buf, len, err, sizeof err);
            if (r == PROTO_WELCOME)
                online = 1;
            else if (r == PROTO_ERROR)
                fprintf(stderr, "server error: %s\n", err);
        } else if (type == 'B') {
            proto_on_binary((const uint8_t *)buf, len);
        }
        free(buf);
    }
    running = 0;
    return NULL;
}

static void sleep_us(long us)
{
    struct timespec ts = {us / 1000000, (us % 1000000) * 1000};
    nanosleep(&ts, NULL);
}

static FILE *outwav;
static long out_samples;

static void *player(void *arg)
{
    (void)arg;
    int16_t buf[OUT_PERIOD];
    while (running) {
        mixer_render(buf, 0);
        if (outwav) {
            fwrite(buf, 2, OUT_PERIOD, outwav);
            out_samples += OUT_PERIOD;
        }
        sleep_us(1000000L * OUT_PERIOD / OUT_RATE);
    }
    return NULL;
}

static int16_t *read_wav(const char *path, int *n)
{
    FILE *f = fopen(path, "rb");
    if (!f)
        return NULL;
    fseek(f, 0, SEEK_END);
    long sz = ftell(f);
    fseek(f, 0, SEEK_SET);
    uint8_t *raw = malloc((size_t)sz);
    if (fread(raw, 1, (size_t)sz, f) != (size_t)sz) {
        fclose(f);
        free(raw);
        return NULL;
    }
    fclose(f);
    int rate, ch;
    const int16_t *p = wav_parse(raw, (size_t)sz, n, &rate, &ch);
    if (!p || rate != 16000 || ch != 1) {
        free(raw);
        return NULL;
    }
    int16_t *pcm = malloc((size_t)*n * 2);
    memcpy(pcm, p, (size_t)*n * 2);
    free(raw);
    return pcm;
}

int main(int argc, char **argv)
{
    proto_identity_t id = {"test-korvo-host", "test-token-0123456789abcdef", "Test Korvo (host)", "", ""};
    const char *input = NULL, *store = "/tmp/hostsat-kws.bin", *out = NULL;
    double after = 3, duration = 30, lead = 1.5;
    int c;
    while ((c = getopt(argc, argv, "i:t:s:a:d:o:l:vn:")) != -1) {
        switch (c) {
        case 'i': snprintf(id.device_id, sizeof id.device_id, "%s", optarg); break;
        case 't': snprintf(id.token, sizeof id.token, "%s", optarg); break;
        case 'n': snprintf(id.name, sizeof id.name, "%s", optarg); break;
        case 's': store = optarg; break;
        case 'a': after = atof(optarg); break;
        case 'd': duration = atof(optarg); break;
        case 'o': out = optarg; break;
        case 'l': lead = atof(optarg); break;
        case 'v': g_log_level = 2; break;
        default:
            fprintf(stderr, "usage: hostsat [-i id] [-t token] [-s store] [-a after_s] [-d duration_s] "
                            "[-o out.raw] [-l lead_s] input.wav\n");
            return 2;
        }
    }
    if (optind < argc)
        input = argv[optind];
    fft_init();
    kws_init();
    if (mixer_init(1) != 0)
        return 1;
    engine_init();
    engine_set_callbacks(on_ui, NULL, NULL);
    tpl_init(store);
    proto_init(&id, NULL);
    if (out)
        outwav = fopen(out, "wb");

    pthread_t tr, tp;
    pthread_create(&tr, NULL, reader, NULL);
    pthread_create(&tp, NULL, player, NULL);

    /* wait for the templates, then a little more */
    for (int i = 0; running && !templates_done && i < 600; i++)
        sleep_us(100000);
    fprintf(stderr, "== online %d, templates %s, model %d templates\n", online, templates_done ? "done" : "missing",
            engine_model_count());
    sleep_us((long)(after * 1e6));
    /* read the input only now: the test may write it meanwhile */
    int n = 0;
    int16_t *pcm = input ? read_wav(input, &n) : NULL;
    if (input && !pcm) {
        fprintf(stderr, "cannot read %s (16 kHz mono WAV)\n", input);
        _exit(1);
    }

    /* feed: lead silence + input + trailing silence, in real time */
    ns_t vadns;
    stft_t st;
    ns_init(&vadns);
    memset(&st, 0, sizeof st);
    int total = (int)(duration * SR), lead_n = (int)(lead * SR);
    int16_t hop[HOP];
    unsigned seed = 1;
    for (int pos = 0; running && pos + HOP <= total; pos += HOP) {
        for (int i = 0; i < HOP; i++) {
            int k = pos + i - lead_n;
            seed = seed * 1103515245u + 12345u;
            int noise = (int)((seed >> 16) % 61) - 30;        /* ~ -60 dBFS floor */
            hop[i] = (int16_t)((pcm && k >= 0 && k < n ? pcm[k] : 0) + noise);
        }
        float x[HOP];
        cfloat X[NBIN];
        for (int i = 0; i < HOP; i++)
            x[i] = hop[i] / 32768.0f;
        stft_push(&st, x, X);
        ns_process(&vadns, X, NULL, -20, 6);
        engine_process(hop, vadns.prob > 0.5f, 0);
        sleep_us(1000000L * HOP / SR);
    }
    engine_status_t s = engine_status();
    fprintf(stderr, "== done: wakes sent %d, state %d, audio frames %d (%d bytes), threshold %.3f, out %.1f s\n",
            n_wake_sent, s.state, n_audio_frames, n_audio_bytes, s.kws_threshold, out_samples / (double)OUT_RATE);
    running = 0;
    if (outwav)
        fclose(outwav);
    fflush(stderr);
    _exit(n_wake_sent > 0 ? 0 : 3);
}
