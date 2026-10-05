/*
 * Reference cost trace with the ReSpeaker satellite's own detector
 * (satellite/engine/src/kws.c, float templates), on the exact input stream
 * test_kws --trace wrote (OUT.raw), with the same front end: STFT, NS
 * (-8 dB floor) -> MFCC, speech flag from an ns.c VAD (-20 dB) with a 300 ms
 * hangover. Prints one cost per frame, to compare with the port.
 *
 *   ref_trace KWDIR input.raw > ref.txt      (KWDIR/<keyword>/*.wav)
 */
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>

#include "fft.h"
#include "kws.h"
#include "ns.h"

int g_verbose = 0;
void logmsg(int level, const char *fmt, ...)
{
    (void)level;
    (void)fmt;
}

int main(int argc, char **argv)
{
    if (argc < 3)
        return 2;
    fft_init();
    kws_init();
    static kws_model_t m;
    static kws_det_t d;
    char info[4096];
    kws_load(&m, argv[1], info, sizeof info);
    float thr = kws_suggest_threshold(&m, NULL);
    fprintf(stderr, "reference: %d templates, threshold %.4f\n", m.ntpl, thr);
    d.t = 0;
    kws_det_reset(&d, &m);
    FILE *f = fopen(argv[2], "rb");
    if (!f)
        return 1;
    static ns_t vad, ns;
    static stft_t st, st2;
    ns_init(&vad);
    ns_init(&ns);
    int16_t hop[HOP];
    int speech = 0, hang = 0;
    while (fread(hop, 2, HOP, f) == HOP) {
        float x[HOP];
        cfloat X[NBIN], Y[NBIN];
        float feat[KWS_NFEAT];
        for (int i = 0; i < HOP; i++)
            x[i] = hop[i] / 32768.0f;
        stft_push(&st, x, X);
        ns_process(&vad, X, NULL, -20, 6);
        int v = vad.prob > 0.5f;
        if (v) {
            speech = 1;
            hang = 19;
        } else if (hang > 0) {
            hang--;
        } else {
            speech = 0;
        }
        stft_push(&st2, x, Y);
        ns_process(&ns, Y, Y, -8, 6);
        kws_features(Y, feat);
        float c = kws_det_push(&d, &m, feat, speech, thr);
        printf("%.4f\n", c);
    }
    fclose(f);
    return 0;
}
