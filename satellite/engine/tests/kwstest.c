/* Runs a 16 kHz mono WAV through the hotword front end (STFT, noise
 * suppression, MFCC, DTW) and prints the best cost over time.
 *   make kwstest && ./kwstest <wakeword dir> file.wav [threshold] */
#include <stdlib.h>
#include <string.h>

#include "../src/fft.h"
#include "../src/kws.h"
#include "../src/ns.h"

int g_verbose = 1;
void logmsg(int level, const char *fmt, ...) { (void)level; (void)fmt; }

int main(int argc, char **argv)
{
    if (argc < 3) {
        fprintf(stderr, "usage: kwstest DIR FILE.wav [threshold]\n");
        return 2;
    }
    float thr = argc > 3 ? (float)atof(argv[3]) : 0.22f;
    extern float g_kws_floor_db;
    if (getenv("FLOOR"))
        g_kws_floor_db = (float)atof(getenv("FLOOR"));
    fft_init();
    kws_init();
    static kws_model_t m;
    char info[4096];
    int n = kws_load(&m, argv[1], info, sizeof info);
    printf("templates: %d %s\n", n, info);
    float loo;
    float sug = kws_suggest_threshold(&m, &loo);
    printf("loo max %.3f suggested threshold %.3f\n", loo, sug);
    for (int i = 0; i < m.ntpl; i++)
        printf("  tpl %d: %s, %d frames\n", i, m.tpl[i].keyword, m.tpl[i].len);
    int len, rate;
    int16_t *pcm = wav_read(argv[2], &len, &rate);
    if (!pcm || rate != SR) {
        fprintf(stderr, "need a 16 kHz mono 16-bit wav\n");
        return 1;
    }
    static kws_det_t det;
    kws_det_reset(&det, &m);
    stft_t st;
    ns_t ns;
    memset(&st, 0, sizeof st);
    ns_init(&ns);
    float best = 9;
    int bestf = -1;
    for (int pos = 0, f = 0; pos + HOP <= len; pos += HOP, f++) {
        float hop[HOP], mf[KWS_NFEAT];
        cfloat X[NBIN];
        for (int i = 0; i < HOP; i++)
            hop[i] = pcm[pos + i] / 32768.0f;
        stft_push(&st, hop, X);
        ns_process(&ns, X, X, -18, 6);
        kws_features(X, mf);
        float c = kws_det_push(&det, &m, mf, ns.prob > 0.5f, thr);
        if (c < best) {
            best = c;
            bestf = f;
        }
        if (c < thr + 0.1f)
            printf("  %6.2fs cost %.3f tpl %d%s\n", f * HOP / (float)SR, c, det.last_tpl, c < thr ? "  <== WAKE" : "");
    }
    printf("best cost %.3f at %.2fs\n", best, bestf * HOP / (float)SR);
    return 0;
}
