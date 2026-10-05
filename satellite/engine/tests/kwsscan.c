/* Replays a raw 8-channel 48 kHz s16le recording through the hotword path of
 * satd (decimation, fixed superdirective beams, noise suppression, VAD, MFCC,
 * DTW per beam) and reports detections and the cost distribution.
 *   kwsscan <wakeword dir> <file.raw> [threshold] [min_matches] [cover%] [neg_margin]
 * Mono 16 kHz WAV files are accepted too (same signal on every mic). */
#include <stdlib.h>
#include <string.h>

#include "../src/beam.h"
#include "../src/fft.h"
#include "../src/kws.h"
#include "../src/ns.h"
#include "../src/resample.h"

int g_verbose = 1;
void logmsg(int level, const char *fmt, ...) { (void)level; (void)fmt; }

int main(int argc, char **argv)
{
    if (argc < 3) {
        fprintf(stderr, "usage: kwsscan DIR FILE [thr] [min_matches] [cover%%] [neg_margin]\n");
        return 2;
    }
    float thr = argc > 3 ? (float)atof(argv[3]) : 0.42f;
    int minm = argc > 4 ? atoi(argv[4]) : 1;
    int cover = argc > 5 ? atoi(argv[5]) : 40;
    float negm = argc > 6 ? (float)atof(argv[6]) : -1;
    fft_init();
    resample_init();
    kws_init();
    static kws_model_t m;
    char info[4096];
    kws_load(&m, argv[1], info, sizeof info);
    float loo, sug = kws_suggest_threshold(&m, &loo);
    fprintf(stderr, "templates %d %s loo %.3f suggested %.3f\n", m.ntpl, info, loo, sug);

    int is_wav = strstr(argv[2], ".wav") != NULL;
    int16_t *mono = NULL;
    int nmono = 0, rate = 0;
    FILE *f = NULL;
    if (is_wav) {
        mono = wav_read(argv[2], &nmono, &rate);
        if (!mono || rate != SR)
            return 1;
    } else {
        f = fopen(argv[2], "rb");
        if (!f)
            return 1;
    }
    beam_t *b = beam_create(6, 0.05f);
    static decim_t dec[CAP_CH];
    static stft_t st[NMIC];
    static ns_t ns[MAX_BEAMS];
    static kws_det_t det[MAX_BEAMS];
    for (int i = 0; i < 6; i++) {
        ns_init(&ns[i]);
        kws_det_reset(&det[i], &m);
    }
    int16_t raw[HOP * DECIM * CAP_CH];
    float fl[HOP * DECIM * CAP_CH];
    static float cap[CAP_CH][HOP];
    static cfloat X[NMIC][NBIN];
    uint8_t hist[256] = {0};
    int64_t frame = 0, refr = 0, cand_frame = 0;
    int cand = -1;
    float cand_cost = 9, best_all = 9;
    int dets = 0, hist_cost[20] = {0}, speech_frames = 0;
    for (;;) {
        if (is_wav) {
            if ((frame + 1) * HOP > nmono)
                break;
            for (int n = 0; n < HOP; n++)
                for (int c = 0; c < NMIC; c++)
                    cap[c][n] = mono[frame * HOP + n] / 32768.0f;
        } else {
            if (fread(raw, sizeof raw, 1, f) != 1)
                break;
            for (size_t i = 0; i < sizeof raw / 2; i++)
                fl[i] = raw[i] / 32768.0f;
            for (int c = 0; c < CAP_CH; c++)
                decim_process(&dec[c], fl + c, CAP_CH, cap[c], HOP);
        }
        frame++;
        for (int c = 0; c < NMIC; c++)
            stft_push(&st[c], cap[c], X[c]);
        float pmax = 0, feats[6][KWS_NFEAT];
        for (int i = 0; i < 6; i++) {
            cfloat Y[NBIN];
            beam_fixed(b, i, X, Y);
            ns_process(&ns[i], Y, Y, -18, 6);
            if (ns[i].prob > pmax)
                pmax = ns[i].prob;
            kws_features(Y, feats[i]);
        }
        int speech = pmax > 0.5f;
        speech_frames += speech;
        hist[frame & 255] = speech;
        int best = -1;
        float cost[6];
        for (int i = 0; i < 6; i++) {
            cost[i] = kws_det_push(&det[i], &m, feats[i], speech, thr);
            if (best < 0 || cost[i] < cost[best])
                best = i;
        }
        float c = cost[best];
        if (c < best_all)
            best_all = c;
        if (c < 1.0f)
            hist_cost[(int)(c * 20)]++;
        int span = det[best].last_span, voiced = 0;
        for (int k = 0; k < span && k < 256; k++)
            voiced += hist[(frame - k) & 255];
        int ok = c < thr && det[best].last_matches >= minm && voiced * 100 >= span * cover && frame > 62 &&
                 frame >= refr;
        if (ok && negm >= 0 && det[best].last_neg < c + negm)
            ok = 0;
        if (ok && (cand < 0 || c < cand_cost)) {
            if (cand < 0)
                cand_frame = frame;
            cand = best;
            cand_cost = c;
        }
        if (cand >= 0 && frame - cand_frame >= 4) {
            dets++;
            printf("DETECT at %7.2fs beam %d cost %.3f matches %d\n", cand_frame * HOP / (float)SR, cand, cand_cost,
                   det[cand].last_matches);
            cand = -1;
            cand_cost = 9;
            refr = frame + 94;
            for (int i = 0; i < 6; i++)
                kws_det_reset(&det[i], &m);
        }
    }
    printf("frames %lld (%.0fs, speech %.0f%%), detections %d, best cost %.3f\n", (long long)frame,
           frame * HOP / (float)SR, 100.0 * speech_frames / (frame ? frame : 1), dets, best_all);
    printf("cost histogram (frames):");
    for (int i = 0; i < 20; i++)
        if (hist_cost[i])
            printf(" %.2f:%d", i / 20.0, hist_cost[i]);
    printf("\n");
    return 0;
}
