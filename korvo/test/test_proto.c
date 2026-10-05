/*
 * Protocol / state machine tests with scripted server messages (no network):
 * hello, pending -> welcome, templates request, a button turn (wake, uplink,
 * eot, TTS stream, session_end with follow-up after the drain), barge-in,
 * cancel, stop, set, listen, earcon, alarm, config, tool_call, binary
 * frames, mute.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "engine.h"
#include "fft.h"
#include "kws.h"
#include "mixer.h"
#include "proto.h"

static int fails;
#define CHECK(cond, ...)                                                                                     \
    do {                                                                                                     \
        int ok_ = (cond);                                                                                    \
        printf(ok_ ? "  ok    " : "  FAIL  ");                                                               \
        fails += !ok_;                                                                                       \
        printf(__VA_ARGS__);                                                                                 \
        printf("\n");                                                                                        \
    } while (0)

/* sent messages log */
static char sent[200][700];
static int nsent, audio_frames, audio_samples, templates_begun;

int sat_send_text(const char *json)
{
    if (nsent < 200)
        snprintf(sent[nsent++], sizeof sent[0], "%s", json);
    return 0;
}
int sat_send_audio(const int16_t *pcm, int n)
{
    audio_frames++;
    audio_samples += n;
    return 0;
}
void sat_templates_begin(void) { templates_begun++; }
void sat_template_msg(const char *kw, const char *name, const char *b64, size_t len, int index, int count) {}
void sat_template_done(int count) {}

static void clear(void)
{
    nsent = 0;
    audio_frames = audio_samples = 0;
}

/* last sent message of a type, parsed (caller deletes), or NULL */
static cJSON *find_sent(const char *type)
{
    for (int i = nsent - 1; i >= 0; i--) {
        cJSON *j = cJSON_Parse(sent[i]);
        const cJSON *t = cJSON_GetObjectItem(j, "type");
        if (cJSON_IsString(t) && !strcmp(t->valuestring, type))
            return j;
        cJSON_Delete(j);
    }
    return NULL;
}

static int count_sent(const char *type)
{
    int n = 0;
    char pat[64];
    snprintf(pat, sizeof pat, "\"type\":\"%s\"", type);
    for (int i = 0; i < nsent; i++)
        n += strstr(sent[i], pat) != NULL;
    return n;
}

static int server(const char *json)
{
    char err[100];
    return proto_on_text(json, strlen(json), err, sizeof err);
}

/* advance time: mixer periods + engine hops of silence */
static void run_ms(int ms)
{
    int16_t out[OUT_PERIOD], hop[HOP] = {0};
    for (int t = 0; t < ms; t += 16) {
        mixer_render(out, 0);
        mixer_render(out, 0);
        if ((t / 16) % 5 == 4)
            mixer_render(out, 0);   /* 16 ms ~ 1.6 periods of 10 ms */
        engine_process(hop, 0, 0);
    }
}

static void send_tts(uint32_t id, float secs)
{
    char m[200];
    snprintf(m, sizeof m, "{\"type\":\"stream_open\",\"id\":%u,\"kind\":\"tts\",\"rate\":24000,\"channels\":1,"
                          "\"prebuffer_ms\":100}", id);
    server(m);
    int n = (int)(24000 * secs);
    uint8_t *frame = malloc(5 + (size_t)n * 2);
    frame[0] = 0x02;
    memcpy(frame + 1, &id, 4);
    for (int i = 0; i < n; i++) {
        int16_t v = (int16_t)(8000 * sinf(TAU_F * 440 * i / 24000.0f));
        memcpy(frame + 5 + 2 * i, &v, 2);
    }
    proto_on_binary(frame, 5 + (size_t)n * 2);      /* odd offset: alignment-safe */
    free(frame);
    snprintf(m, sizeof m, "{\"type\":\"stream_close\",\"id\":%u,\"drain\":true}", id);
    server(m);
}

static const char *st_name(int s)
{
    static const char *n[] = {"idle", "listening", "thinking", "speaking"};
    return s >= 0 && s < 4 ? n[s] : "?";
}

int main(void)
{
    fft_init();
    kws_init();
    mixer_init(1);
    engine_init();
    proto_identity_t id = {"test-korvo-unit", "tok", "Cuisine", "kitchen", "jason"};
    proto_init(&id, NULL);

    printf("1. hello and pairing:\n");
    char hello[2048];
    CHECK(proto_hello(hello, sizeof hello) > 0, "hello built (%zu bytes)", strlen(hello));
    cJSON *h = cJSON_Parse(hello);
    CHECK(h && !strcmp(cJSON_GetObjectItem(h, "kind")->valuestring, "satellite"), "kind satellite");
    CHECK(h && cJSON_GetObjectItem(h, "protocol")->valueint == 1, "protocol 1");
    cJSON *cap = cJSON_GetObjectItem(h, "capabilities");
    CHECK(cap && cJSON_GetObjectItem(cJSON_GetObjectItem(cap, "audio_in"), "rate")->valueint == 16000,
          "audio_in 16 kHz");
    CHECK(cap && cJSON_IsTrue(cJSON_GetObjectItem(cap, "wakeword")), "capabilities.wakeword");
    cJSON_Delete(h);
    CHECK(server("{\"type\":\"pending\",\"message\":\"waiting\"}") == PROTO_PENDING, "pending recognised");
    CHECK(engine_status().link == LINK_PENDING, "link state pending");
    clear();
    CHECK(server("{\"type\":\"welcome\",\"server\":\"Assistant\",\"server_time_ns\":1791138464886213195,"
                 "\"config\":{\"name\":\"Cuisine\",\"room\":\"kitchen\"}}") == PROTO_WELCOME, "welcome recognised");
    CHECK(engine_status().link == LINK_ONLINE, "link online");
    CHECK(count_sent("wake_templates_get") == 1 && templates_begun == 1, "wake_templates_get sent on connection");
    CHECK(server("{\"type\":\"error\",\"message\":\"bad token\"}") == PROTO_ERROR, "error recognised");

    printf("2. a button turn:\n");
    run_ms(500);
    clear();
    engine_talk_button();
    cJSON *w = find_sent("wake");
    CHECK(w != NULL, "wake sent");
    CHECK(w && !strcmp(cJSON_GetObjectItem(w, "source")->valuestring, "button"), "source button");
    CHECK(w && cJSON_GetObjectItem(w, "wake_id")->valueint == 1, "wake_id 1");
    int pre = w ? cJSON_GetObjectItem(w, "preroll_ms")->valueint : -1;
    cJSON_Delete(w);
    CHECK(audio_samples == 16 * pre, "pre-roll sent right after the wake (%d ms = %d samples)", pre, audio_samples);
    CHECK(engine_status().state == ST_LISTENING, "state listening");
    clear();
    run_ms(320);
    CHECK(audio_frames == 10 && audio_samples == 5120, "mic streamed in 32 ms frames (%d frames, %d samples)",
          audio_frames, audio_samples);
    server("{\"type\":\"wake_ack\",\"wake_id\":1}");
    server("{\"type\":\"transcript\",\"text\":\"quelle heure\",\"final\":false}");
    clear();
    server("{\"type\":\"eot\",\"wake_id\":1}");
    CHECK(engine_status().state == ST_THINKING, "eot -> thinking");
    CHECK(count_sent("audio_end") == 1, "uplink ended (audio_end, as the satellite agent)");
    clear();
    run_ms(200);
    CHECK(audio_frames == 0, "no audio after eot");
    send_tts(7, 1.0f);
    server("{\"type\":\"reply\",\"text\":\"Il est midi.\",\"ack\":\"\"}");
    server("{\"type\":\"session_end\",\"follow_up\":true,\"follow_up_ms\":6000}");
    run_ms(300);
    CHECK(engine_status().state == ST_SPEAKING, "TTS playing -> speaking (%s)", st_name(engine_status().state));
    cJSON *pb = find_sent("playback");
    CHECK(pb && !strcmp(cJSON_GetObjectItem(pb, "event")->valuestring, "started"), "playback started reported");
    cJSON_Delete(pb);
    CHECK(count_sent("wake") == 0, "follow-up waits for the playback to drain");
    run_ms(1200);
    w = find_sent("wake");
    CHECK(w && !strcmp(cJSON_GetObjectItem(w, "source")->valuestring, "followup") &&
              cJSON_GetObjectItem(w, "wake_id")->valueint == 2,
          "after the drain: wake source followup, wake_id 2");
    cJSON_Delete(w);
    CHECK(engine_status().state == ST_LISTENING, "listening again");
    clear();
    server("{\"type\":\"cancel\",\"wake_id\":2,\"reason\":\"no_speech\"}");
    CHECK(engine_status().state == ST_IDLE, "cancel -> idle");

    printf("3. barge-in, stop, push-to-talk end:\n");
    send_tts(8, 3.0f);
    run_ms(300);
    CHECK(engine_status().state == ST_SPEAKING, "speaking");
    clear();
    engine_wake("button");
    run_ms(100);
    CHECK(!(mixer_active_kinds() & (1 << SK_TTS)), "a new wake while speaking stops the answer");
    w = find_sent("wake");
    CHECK(w && cJSON_IsTrue(cJSON_GetObjectItem(w, "barge_in")) && cJSON_IsTrue(cJSON_GetObjectItem(w, "tts")),
          "wake says barge_in and tts");
    cJSON_Delete(w);
    clear();
    engine_talk_button();
    cJSON *ae = find_sent("audio_end");
    CHECK(ae && !strcmp(cJSON_GetObjectItem(ae, "reason")->valuestring, "button"),
          "talk button while listening -> audio_end");
    cJSON_Delete(ae);
    CHECK(engine_status().state == ST_THINKING, "-> thinking");
    server("{\"type\":\"cancel\",\"reason\":\"empty\"}");
    server("{\"type\":\"stream_open\",\"id\":9,\"kind\":\"media\",\"rate\":48000,\"channels\":2}");
    CHECK(mixer_active_kinds() & (1 << SK_MEDIA), "media stream opened");
    server("{\"type\":\"stream_pause\",\"id\":9,\"paused\":true}");
    CHECK(!(mixer_active_kinds() & (1 << SK_MEDIA)), "stream_pause");
    server("{\"type\":\"stream_pause\",\"id\":9,\"paused\":false}");
    server("{\"type\":\"stop\",\"media\":true}");
    run_ms(200);
    CHECK(!(mixer_active_kinds() & (1 << SK_MEDIA)), "stop media");

    printf("4. settings, earcons, alarm, config, tools:\n");
    clear();
    server("{\"type\":\"set\",\"volume\":35}");
    CHECK(fabsf(engine_status().volume - 35) < 0.01f, "set volume 35");
    cJSON *s = find_sent("settings");
    CHECK(s && cJSON_GetObjectItem(s, "volume")->valueint == 35, "settings reported back");
    cJSON_Delete(s);
    server("{\"type\":\"set\",\"mic_muted\":true}");
    CHECK(engine_status().muted, "set mic_muted");
    clear();
    engine_talk_button();
    CHECK(count_sent("wake") == 0, "no wake while muted");
    engine_toggle_mute();
    CHECK(!engine_status().muted, "mute button toggles back");
    server("{\"type\":\"earcon\",\"name\":\"done\"}");
    CHECK(mixer_active_kinds() & (1 << SK_EARCON), "earcon done plays");
    server("{\"type\":\"alarm\",\"on\":true}");
    run_ms(100);
    CHECK(engine_status().alarm && (mixer_active_kinds() & (1 << SK_ALARM)), "alarm rings");
    clear();
    engine_talk_button();
    s = find_sent("button");
    CHECK(s && !strcmp(cJSON_GetObjectItem(s, "action")->valuestring, "stop") && !engine_status().alarm,
          "talk button stops the alarm (button stop sent)");
    cJSON_Delete(s);
    server("{\"type\":\"config\",\"name\":\"Bureau\",\"engine\":{\"kws_threshold\":0.4}}");
    engine_cfg_t c;
    engine_get_cfg(&c);
    CHECK(fabsf(c.kws_threshold - 0.4f) < 1e-4f && !c.kws_auto_threshold, "config engine.kws_threshold");
    clear();
    server("{\"type\":\"tool_call\",\"call_id\":\"c1\",\"name\":\"x\",\"args\":{}}");
    s = find_sent("tool_result");
    CHECK(s && !strcmp(cJSON_GetObjectItem(s, "call_id")->valuestring, "c1"), "tool_call answered");
    cJSON_Delete(s);
    clear();
    server("{\"type\":\"listen\",\"timeout_ms\":4000,\"earcon\":false}");
    w = find_sent("wake");
    CHECK(w && !strcmp(cJSON_GetObjectItem(w, "source")->valuestring, "followup"), "listen -> wake followup");
    cJSON_Delete(w);
    clear();
    run_ms(4000 + 1600);
    s = find_sent("eos");
    CHECK(s && !strcmp(cJSON_GetObjectItem(s, "reason")->valuestring, "no_speech"),
          "nobody speaks: eos no_speech after the timeout");
    cJSON_Delete(s);
    run_ms(1600);
    CHECK(engine_status().state == ST_IDLE, "server silent: back to idle locally (%s)", st_name(engine_status().state));
    server("{\"type\":\"pong\",\"t0\":1,\"t1\":2}");
    proto_on_disconnect();
    CHECK(engine_status().link == LINK_OFFLINE, "disconnect -> offline");
    clear();
    engine_talk_button();
    CHECK(count_sent("wake") == 0, "no wake offline (offline earcon instead)");

    printf("%s (%d failure%s)\n", fails ? "FAILED" : "PASSED", fails, fails == 1 ? "" : "s");
    return fails ? 1 : 0;
}
