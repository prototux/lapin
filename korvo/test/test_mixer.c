/*
 * Mixer tests: resampling (24 kHz speech, 48 kHz stereo music), ducking
 * (music -18 dB under speech, -40 dB while listening), prebuffering, drain,
 * pause, stop, earcons, events.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "mixer.h"

int sat_send_text(const char *json) { return 0; }
int sat_send_audio(const int16_t *pcm, int n) { return 0; }
void sat_templates_begin(void) {}
void sat_template_msg(const char *kw, const char *name, const char *b64, size_t len, int index, int count) {}
void sat_template_done(int count) {}

static int fails;
#define CHECK(cond, ...)                                                                                     \
    do {                                                                                                     \
        int ok_ = (cond);                                                                                    \
        printf(ok_ ? "  ok    " : "  FAIL  ");                                                               \
        fails += !ok_;                                                                                       \
        printf(__VA_ARGS__);                                                                                 \
        printf("\n");                                                                                        \
    } while (0)

/* amplitude of a tone in a block (Goertzel) */
static float tone_amp(const float *x, int n, float f, int rate)
{
    float w = TAU_F * f / rate, c = 2 * cosf(w), s1 = 0, s2 = 0;
    for (int i = 0; i < n; i++) {
        float s = x[i] + c * s1 - s2;
        s2 = s1;
        s1 = s;
    }
    float re = s1 - s2 * cosf(w), im = s2 * sinf(w);
    return 2 * sqrtf(re * re + im * im) / n;
}

static void feed_tone(uint32_t id, int rate, int ch, float f, float amp, float secs, int *phase)
{
    int n = (int)(rate * secs);
    int16_t *buf = malloc(sizeof(int16_t) * (size_t)n * (size_t)ch);
    for (int i = 0; i < n; i++) {
        float v = amp * sinf(TAU_F * f * (float)(*phase + i) / rate);
        for (int c = 0; c < ch; c++)
            buf[i * ch + c] = (int16_t)(v * 32767);
    }
    *phase += n;
    mixer_write(id, buf, n * ch);
    free(buf);
}

/* renders `secs` of output and returns it as float */
static float *render(float secs, int *n_out)
{
    int periods = (int)(secs * OUT_RATE / OUT_PERIOD);
    float *x = malloc(sizeof(float) * (size_t)periods * OUT_PERIOD);
    int16_t buf[OUT_PERIOD];
    for (int p = 0; p < periods; p++) {
        mixer_render(buf, 0);
        for (int i = 0; i < OUT_PERIOD; i++)
            x[p * OUT_PERIOD + i] = buf[i] / 32768.0f;
    }
    *n_out = periods * OUT_PERIOD;
    return x;
}

static int count_events(int kind_mask, int event)
{
    mixer_event_t ev;
    int n = 0;
    while (mixer_poll_event(&ev))
        if ((kind_mask >> ev.kind & 1) && (event == 0 || ev.event == event))
            n++;
    return n;
}

int main(void)
{
    if (mixer_init(1) != 0)
        return 1;
    mixer_cfg_t c;
    mixer_get_cfg(&c);
    c.volume = 100;
    c.drc_ratio = 1;          /* measure ducking without the compressor */
    c.hpf_hz = 0;
    mixer_set_cfg(&c);
    int n, ph1 = 0, ph2 = 0;
    float *x;

    printf("1. 24 kHz mono speech, resampled to 48 kHz:\n");
    mixer_open(1, SK_TTS, 24000, 1, 0, 0, 100);
    feed_tone(1, 24000, 1, 1000, 0.25f, 1.0f, &ph1);
    x = render(0.05f, &n);              /* prebuffer reached at once: plays */
    free(x);
    x = render(0.5f, &n);
    float a1k = tone_amp(x, n, 1000, OUT_RATE), a23k = tone_amp(x, n, 23000, OUT_RATE);
    CHECK(fabsf(a1k - 0.25f) < 0.02f, "1 kHz tone amplitude %.3f (sent 0.250)", a1k);
    CHECK(a23k < 0.25f * 0.01f, "image at 23 kHz %.1f dB below", 20 * log10f(a1k / (a23k + 1e-9f)));
    free(x);
    mixer_close(1, 0);
    x = render(0.2f, &n);
    free(x);
    count_events(0xF, 0);

    printf("2. 48 kHz stereo music, ducked under speech and while listening:\n");
    mixer_open(2, SK_MEDIA, 48000, 2, 0, 0, 1000);
    feed_tone(2, 48000, 2, 300, 0.2f, 0.5f, &ph2);
    x = render(0.2f, &n);
    float early = tone_amp(x, n, 300, OUT_RATE);
    free(x);
    CHECK(early < 0.001f, "music waits for its 1 s prebuffer (level %.4f after 0.5 s queued)", early);
    feed_tone(2, 48000, 2, 300, 0.2f, 9.5f, &ph2);
    x = render(1.0f, &n);
    float m0 = tone_amp(x + n / 2, n / 2, 300, OUT_RATE);
    free(x);
    CHECK(fabsf(m0 - 0.2f) < 0.02f, "music alone: 300 Hz at %.3f (sent 0.200)", m0);
    mixer_open(3, SK_TTS, 24000, 1, 0, 0, 100);
    ph1 = 0;
    feed_tone(3, 24000, 1, 2000, 0.2f, 3.0f, &ph1);
    x = render(1.0f, &n);
    float m1 = tone_amp(x + n / 2, n / 2, 300, OUT_RATE), s1 = tone_amp(x + n / 2, n / 2, 2000, OUT_RATE);
    free(x);
    CHECK(fabsf(20 * log10f(m1 / m0) + 18) < 1.0f, "music under speech: %.1f dB (want -18), speech %.3f",
          20 * log10f(m1 / m0), s1);
    mixer_set_duck(2);
    x = render(1.0f, &n);
    float m2 = tone_amp(x + n / 2, n / 2, 300, OUT_RATE);
    free(x);
    CHECK(fabsf(20 * log10f(m2 / m0) + 40) < 1.5f, "music while listening: %.1f dB (want -40)", 20 * log10f(m2 / m0));
    mixer_set_duck(0);
    mixer_stop_kinds(1 << SK_TTS);
    x = render(1.5f, &n);
    float m3 = tone_amp(x + n / 2, n / 2, 300, OUT_RATE);
    free(x);
    CHECK(fabsf(20 * log10f(m3 / m0)) < 1.0f, "speech stopped: music back to %.1f dB", 20 * log10f(m3 / m0));
    CHECK(count_events(1 << SK_TTS, MIXEV_STOPPED) == 1, "speech stream reported stopped");

    printf("3. pause, drain, events:\n");
    mixer_pause(2, 1);
    CHECK(!(mixer_active_kinds() & (1 << SK_MEDIA)), "paused music not active");
    x = render(0.2f, &n);
    CHECK(tone_amp(x, n, 300, OUT_RATE) < 0.001f, "paused music silent");
    free(x);
    mixer_pause(2, 0);
    mixer_close(2, 1);          /* drain what is queued (~5 s) */
    x = render(8.0f, &n);
    free(x);
    CHECK(count_events(1 << SK_MEDIA, MIXEV_FINISHED) == 1, "drained music reported finished");
    CHECK(mixer_active_kinds() == 0, "no stream left");
    CHECK(mixer_queued_bytes() == 0, "jitter buffers empty (%zu bytes)", mixer_queued_bytes());

    printf("4. underrun and earcons:\n");
    mixer_open(4, SK_TTS, 24000, 1, 0, 0, 50);
    ph1 = 0;
    feed_tone(4, 24000, 1, 500, 0.2f, 0.1f, &ph1);
    x = render(0.3f, &n);
    free(x);
    CHECK(count_events(1 << SK_TTS, MIXEV_UNDERRUN) == 1, "underrun reported when the server is late");
    mixer_close(4, 1);
    x = render(0.1f, &n);
    free(x);
    count_events(0xF, 0);
    mixer_earcon(EC_WAKE, 0);
    x = render(0.5f, &n);
    float e = tone_amp(x, n / 4, 880, OUT_RATE);
    free(x);
    CHECK(e > 0.01f, "wake earcon plays (880 Hz at %.3f)", e);
    CHECK(mixer_active_kinds() == 0, "earcon released after playing");
    mixer_earcon(EC_ALARM, 1);
    x = render(3.0f, &n);
    free(x);
    CHECK(mixer_active_kinds() & (1 << SK_ALARM), "looping alarm still playing after 3 s");
    mixer_stop_kinds(1 << SK_ALARM);
    x = render(0.3f, &n);
    free(x);
    CHECK(mixer_active_kinds() == 0, "alarm stopped");

    printf("5. volume taper and limiter:\n");
    mixer_get_cfg(&c);
    c.volume = 50;
    mixer_set_cfg(&c);
    mixer_open(6, SK_TTS, 24000, 1, 0, 0, 50);
    ph1 = 0;
    feed_tone(6, 24000, 1, 1000, 0.2f, 3.0f, &ph1);
    x = render(2.5f, &n);
    float v50 = tone_amp(x + n * 4 / 5, n / 5, 1000, OUT_RATE);
    free(x);
    CHECK(fabsf(20 * log10f(v50 / 0.2f) + 12) < 0.5f, "volume 50: %.1f dB (perceptual taper v^2: -12 dB)",
          20 * log10f(v50 / 0.2f));
    mixer_stop_all();
    x = render(0.3f, &n);
    free(x);
    c.volume = 100;
    c.drc_ratio = 3;
    mixer_set_cfg(&c);
    mixer_open(5, SK_TTS, 24000, 1, 0, 0, 50);
    ph1 = 0;
    feed_tone(5, 24000, 1, 1000, 0.99f, 2.0f, &ph1);
    x = render(1.5f, &n);
    float pk = 0;
    for (int i = n / 2; i < n; i++)
        pk = fmaxf(pk, fabsf(x[i]));
    free(x);
    CHECK(pk <= db_to_lin(-1.0f) + 0.01f, "full-scale input stays under the -1 dB ceiling (peak %.3f)", pk);
    mixer_stop_all();

    printf("%s (%d failure%s)\n", fails ? "FAILED" : "PASSED", fails, fails == 1 ? "" : "s");
    return fails ? 1 : 0;
}
