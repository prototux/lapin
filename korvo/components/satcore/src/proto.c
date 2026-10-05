/*
 * Device protocol, satellite side: what satellite/agent/satagent/link.py
 * (hello, clock) and agent.py (on_server) do on the ReSpeaker satellite.
 */
#include "proto.h"

#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "engine.h"
#include "mixer.h"
#include "ota.h"

static proto_identity_t ident;
static proto_config_cb_t config_cb;
static int64_t offset_ns;
static int rtt_ms = -1;
static struct {
    int64_t rtt, off;
} clk[8];
static int nclk;

void proto_init(const proto_identity_t *id, proto_config_cb_t cb)
{
    ident = *id;
    config_cb = cb;
}

int64_t proto_clock_offset_ns(void) { return offset_ns; }
int proto_rtt_ms(void) { return rtt_ms; }

static void esc(char *out, size_t n, const char *s)
{
    cJSON *j = cJSON_CreateString(s ? s : "");
    char *p = j ? cJSON_PrintUnformatted(j) : NULL;
    snprintf(out, n, "%s", p ? p : "\"\"");
    cJSON_free(p);
    cJSON_Delete(j);
}

int proto_hello(char *out, size_t n)
{
    char id[64], tok[96], name[140], room[140], owner[100], ww[256];
    esc(id, sizeof id, ident.device_id);
    esc(tok, sizeof tok, ident.token);
    esc(name, sizeof name, ident.name);
    esc(room, sizeof room, ident.room);
    esc(owner, sizeof owner, ident.owner);
    engine_wake_words_json(ww, sizeof ww);
    engine_status_t st = engine_status();
    int r = snprintf(out, n,
                     "{\"type\":\"hello\",\"protocol\":1,\"device_id\":%s,\"token\":%s,\"kind\":\"satellite\","
                     "\"name\":%s,\"room\":%s,\"owner\":%s,\"version\":\"%s\",\"model\":\"esp32-korvo-v1.1\","
                     "\"capabilities\":{\"audio_in\":{\"rate\":16000,\"channels\":1,\"format\":\"s16le\"},"
                     "\"audio_out\":{\"rates\":[16000,24000,48000],\"channels\":[1,2],\"format\":\"s16le\"},"
                     "\"wakeword\":true,\"doa\":false,\"leds\":true,\"button\":true,\"sync_playback\":true,\"ota\":true,\"log\":true,"
                     "\"confirm_ui\":false,\"display\":false},"
                     "\"wake_words\":%s,\"settings\":{\"volume\":%d,\"mic_muted\":%s}}",
                     id, tok, name, room, owner, SAT_VERSION, ww, (int)lrintf(st.volume),
                     st.muted ? "true" : "false");
    return r > 0 && r < (int)n ? r : -1;
}

void proto_send_ping(void)
{
    char buf[80];
    snprintf(buf, sizeof buf, "{\"type\":\"ping\",\"t0\":%lld}", (long long)sat_real_ns());
    sat_send_text(buf);
}

static double num(const cJSON *o, const char *k, double def)
{
    const cJSON *v = cJSON_GetObjectItemCaseSensitive(o, k);
    return cJSON_IsNumber(v) ? v->valuedouble : def;
}

static int flag(const cJSON *o, const char *k, int def)
{
    const cJSON *v = cJSON_GetObjectItemCaseSensitive(o, k);
    if (cJSON_IsBool(v))
        return cJSON_IsTrue(v);
    if (cJSON_IsNumber(v))
        return v->valuedouble != 0;
    return def;
}

static const char *str(const cJSON *o, const char *k, const char *def)
{
    const cJSON *v = cJSON_GetObjectItemCaseSensitive(o, k);
    return cJSON_IsString(v) && v->valuestring ? v->valuestring : def;
}

static int has(const cJSON *o, const char *k) { return cJSON_GetObjectItemCaseSensitive(o, k) != NULL; }

/* int64 from a JSON number (server ns timestamps exceed double precision a
 * little: parse the raw text when there is one) */
static int64_t i64(const cJSON *o, const char *k)
{
    const cJSON *v = cJSON_GetObjectItemCaseSensitive(o, k);
    if (cJSON_IsNumber(v))
        return (int64_t)v->valuedouble;
    if (cJSON_IsString(v) && v->valuestring)
        return strtoll(v->valuestring, NULL, 10);
    return 0;
}

static void apply_config(const cJSON *m)
{
    const char *name = str(m, "name", NULL), *room = str(m, "room", NULL);
    if ((name && strcmp(name, ident.name)) || (room && strcmp(room, ident.room))) {
        if (name)
            snprintf(ident.name, sizeof ident.name, "%s", name);
        if (room)
            snprintf(ident.room, sizeof ident.room, "%s", room);
        if (config_cb)
            config_cb(ident.name, ident.room);
    }
    const cJSON *e = cJSON_GetObjectItemCaseSensitive(m, "engine");
    if (cJSON_IsObject(e)) {
        engine_cfg_t c;
        engine_get_cfg(&c);
        if (has(e, "kws_threshold")) {
            c.kws_threshold = clampf((float)num(e, "kws_threshold", c.kws_threshold), 0.02f, 0.8f);
            c.kws_auto_threshold = 0;
        }
        c.kws_tts_threshold = clampf((float)num(e, "kws_tts_threshold", c.kws_tts_threshold), 0.2f, 0.8f);
        c.kws_playback_margin = clampf((float)num(e, "kws_playback_margin", c.kws_playback_margin), -0.1f, 0.3f);
        c.kws_enabled = flag(e, "kws_enabled", c.kws_enabled);
        c.kws_barge_in = flag(e, "kws_barge_in", c.kws_barge_in);
        c.preroll_ms = (int)clampf((float)num(e, "preroll_ms", c.preroll_ms), 0, 2400);
        c.earcon_wake = flag(e, "earcon_wake", c.earcon_wake);
        c.earcon_end = flag(e, "earcon_end", c.earcon_end);
        c.listen_timeout_ms = (int)clampf((float)num(e, "listen_timeout_ms", c.listen_timeout_ms), 1000, 30000);
        engine_set_cfg(&c);
        if (has(e, "volume"))
            engine_set_volume((float)num(e, "volume", 60), 0);
        if (has(e, "mic_muted"))
            engine_set_muted(flag(e, "mic_muted", 0), 0);
        if (has(e, "speaker_muted"))
            engine_set_speaker_muted(flag(e, "speaker_muted", 0));
    }
}

static void on_pong(const cJSON *m)
{
    int64_t t2 = sat_real_ns(), t0 = i64(m, "t0"), t1 = i64(m, "t1");
    if (!t0 || !t1 || !t2)
        return;
    int64_t rtt = t2 - t0;
    if (rtt < 0)
        return;
    if (nclk == 8) {
        memmove(clk, clk + 1, sizeof clk[0] * 7);
        nclk = 7;
    }
    clk[nclk].rtt = rtt;
    clk[nclk].off = t1 - (t0 + t2) / 2;
    nclk++;
    int best = 0;           /* lowest round trip = best estimate */
    for (int i = 1; i < nclk; i++)
        if (clk[i].rtt < clk[best].rtt)
            best = i;
    rtt_ms = (int)(clk[best].rtt / 1000000);
    offset_ns = clk[best].off;
}

int proto_on_text(const char *msg, size_t len, char *err, size_t errn)
{
    cJSON *m = cJSON_ParseWithLength(msg, len);
    if (!m)
        return PROTO_OK;
    int ret = PROTO_OK;
    const char *t = str(m, "type", "");

    if (!strcmp(t, "welcome")) {
        int64_t srv = i64(m, "server_time_ns");
        if (srv) {
            sat_set_time_ns(srv);
            int64_t now = sat_real_ns();
            offset_ns = now ? srv - now : 0;
        }
        nclk = 0;
        const cJSON *c = cJSON_GetObjectItemCaseSensitive(m, "config");
        if (cJSON_IsObject(c))
            apply_config(c);
        LOGI("server: welcome (%s %s)", str(m, "server", "?"), str(m, "version", "?"));
        engine_link(LINK_ONLINE);
        /* the wake word comes from the other satellites' recordings */
        sat_templates_begin();
        sat_send_text("{\"type\":\"wake_templates_get\"}");
        sat_on_online();
        ret = PROTO_WELCOME;
    } else if (!strcmp(t, "pending")) {
        LOGI("server: %s", str(m, "message", "waiting for approval"));
        engine_link(LINK_PENDING);
        ret = PROTO_PENDING;
    } else if (!strcmp(t, "error")) {
        snprintf(err, errn, "%s", str(m, "message", "error"));
        LOGE("server error: %s", err);
        ret = PROTO_ERROR;
    } else if (!strcmp(t, "pong")) {
        on_pong(m);
    } else if (!strcmp(t, "cancel")) {
        const char *reason = str(m, "reason", "server");
        LOGI("server: cancel (%s)", reason);
        /* learn from a rejection: that audio was not the wake word */
        int learn = !strcmp(reason, "rejected") && (!has(m, "verify_score") || num(m, "verify_score", 0) < 0.6);
        engine_cancel(reason, learn);
    } else if (!strcmp(t, "eot")) {
        engine_eot();
    } else if (!strcmp(t, "stream_open")) {
        int64_t at = i64(m, "start_at_ns");
        engine_stream_open((uint32_t)num(m, "id", 0), str(m, "kind", "tts"), (int)num(m, "rate", 24000),
                           (int)num(m, "channels", 1), at ? at - offset_ns : 0, (float)num(m, "gain_db", 0),
                           (int)num(m, "prebuffer_ms", -1));
    } else if (!strcmp(t, "stream_close")) {
        engine_stream_close((uint32_t)num(m, "id", 0), flag(m, "drain", 1));
    } else if (!strcmp(t, "stream_pause")) {
        mixer_pause((uint32_t)num(m, "id", 0), flag(m, "paused", 1));
    } else if (!strcmp(t, "session_end")) {
        engine_session_end(flag(m, "follow_up", 0), (int)num(m, "follow_up_ms", 6000));
    } else if (!strcmp(t, "listen")) {
        engine_listen((int)num(m, "timeout_ms", 6000), flag(m, "earcon", 0));
    } else if (!strcmp(t, "stop")) {
        engine_stop(flag(m, "media", 1));
    } else if (!strcmp(t, "earcon")) {
        engine_earcon(str(m, "name", "notify"), flag(m, "loop", 0));
    } else if (!strcmp(t, "alarm")) {
        engine_alarm(flag(m, "on", 0));
    } else if (!strcmp(t, "set")) {
        if (has(m, "volume"))
            engine_set_volume((float)num(m, "volume", 60), 0);
        if (has(m, "mic_muted"))
            engine_set_muted(flag(m, "mic_muted", 0), 0);
        if (has(m, "speaker_muted"))
            engine_set_speaker_muted(flag(m, "speaker_muted", 0));
    } else if (!strcmp(t, "config")) {
        apply_config(m);
    } else if (!strcmp(t, "led")) {
        engine_led_hint(str(m, "pattern", "success"), flag(m, "on", 1));
    } else if (!strcmp(t, "transcript")) {
        LOGI("heard: %s%s", str(m, "text", ""), flag(m, "final", 1) ? "" : " ...");
    } else if (!strcmp(t, "reply")) {
        LOGI("reply: %s", str(m, "text", ""));
    } else if (!strcmp(t, "wake_template")) {
        const cJSON *b = cJSON_GetObjectItemCaseSensitive(m, "wav_b64");
        if (cJSON_IsString(b) && b->valuestring)
            sat_template_msg(str(m, "keyword", ""), str(m, "name", ""), b->valuestring, strlen(b->valuestring),
                             (int)num(m, "index", -1), (int)num(m, "count", 0));
    } else if (!strcmp(t, "wake_templates_done")) {
        int n = (int)num(m, "count", 0);
        if (n > 0)
            sat_template_done(n);
        else
            LOGI("server sent no wake word recordings: keeping the stored ones");
    } else if (!strcmp(t, "ota_begin")) {
        ota_on_begin((size_t)num(m, "size", 0), str(m, "sha256", ""), str(m, "version", ""));
    } else if (!strcmp(t, "ota_end")) {
        ota_on_end();
    } else if (!strcmp(t, "tool_call")) {
        /* no device tools are declared; answer so the server does not wait */
        char buf[200], id[80];
        esc(id, sizeof id, str(m, "call_id", ""));
        snprintf(buf, sizeof buf, "{\"type\":\"tool_result\",\"call_id\":%s,\"result\":{\"error\":\"not supported\"}}",
                 id);
        sat_send_text(buf);
    }
    cJSON_Delete(m);
    return ret;
}

void proto_on_binary(const uint8_t *d, size_t len)
{
    if (len >= 5 && d[0] == 0x03) {
        ota_on_data(d, len);
        return;
    }
    if (len < 5 || d[0] != 0x02)
        return;
    uint32_t id = (uint32_t)d[1] | (uint32_t)d[2] << 8 | (uint32_t)d[3] << 16 | (uint32_t)d[4] << 24;
    mixer_write(id, d + 5, (int)((len - 5) / 2));
}

void proto_on_disconnect(void)
{
    ota_on_disconnect();
    engine_link(LINK_OFFLINE);
    nclk = 0;
}
