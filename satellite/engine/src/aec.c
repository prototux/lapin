#include "aec.h"

#include <speex/speex_echo.h>
#include <stdlib.h>

struct aec {
    SpeexEchoState *st;
    int tail_ms;
    int16_t rec[HOP * NMIC], play[HOP], out[HOP * NMIC];
    double pin, pout;       /* smoothed powers for ERLE */
    float erle_db;
};

static inline int16_t to16(float x)
{
    x *= 32767.0f;
    return (int16_t)(x > 32767.0f ? 32767 : x < -32768.0f ? -32768 : lrintf(x));
}

aec_t *aec_create(int tail_ms)
{
    aec_t *a = calloc(1, sizeof *a);
    if (!a)
        return NULL;
    int tail = SR * tail_ms / 1000;
    tail = (tail + HOP - 1) / HOP * HOP;
    a->tail_ms = tail_ms;
    a->st = speex_echo_state_init_mc(HOP, tail, NMIC, 1);
    if (!a->st) {
        free(a);
        return NULL;
    }
    int rate = SR;
    speex_echo_ctl(a->st, SPEEX_ECHO_SET_SAMPLING_RATE, &rate);
    return a;
}

void aec_destroy(aec_t *a)
{
    if (!a)
        return;
    speex_echo_state_destroy(a->st);
    free(a);
}

void aec_reset(aec_t *a)
{
    speex_echo_state_reset(a->st);
    a->pin = a->pout = 0;
    a->erle_db = 0;
}

void aec_process(aec_t *a, float mic[NMIC][HOP], const float *ref, int near_speech)
{
    double pref = 0, pin = 0, pout = 0;
    for (int n = 0; n < HOP; n++) {
        a->play[n] = to16(ref[n]);
        pref += (double)ref[n] * ref[n];
        for (int m = 0; m < NMIC; m++)
            a->rec[n * NMIC + m] = to16(mic[m][n]);
    }
    speex_echo_cancellation(a->st, a->rec, a->play, a->out);
    for (int n = 0; n < HOP; n++) {
        for (int m = 0; m < NMIC; m++) {
            float o = a->out[n * NMIC + m] * (1.0f / 32768.0f);
            pin += (double)mic[m][n] * mic[m][n];
            pout += (double)o * o;
            mic[m][n] = o;
        }
    }
    /* ERLE over frames where only the far end is active */
    if (pref / HOP > 1e-6 && !near_speech) {
        a->pin = 0.95 * a->pin + 0.05 * pin;
        a->pout = 0.95 * a->pout + 0.05 * pout;
        a->erle_db = (float)(10 * log10((a->pin + 1e-12) / (a->pout + 1e-12)));
    }
}

float aec_erle_db(const aec_t *a) { return a->erle_db; }
int aec_tail_ms(const aec_t *a) { return a->tail_ms; }
