/*
 * Firmware update over the WebSocket, host side: the server's messages go
 * through proto.c (JSON + 0x03 binary frames) into ota.c, written to a memory
 * "flash" backend. Checks the acks (ready, receiving every 64 KB, verifying,
 * done), the image bytes, and the failure paths (bad sha256, out-of-order or
 * oversized chunks, too big an image, timeout, lost connection).
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "engine.h"
#include "fft.h"
#include "kws.h"
#include "mixer.h"
#include "ota.h"
#include "proto.h"
#include "sha256.h"

static int fails;
#define CHECK(cond, ...)                                                                                     \
    do {                                                                                                     \
        int ok_ = (cond);                                                                                    \
        printf(ok_ ? "  ok    " : "  FAIL  ");                                                               \
        fails += !ok_;                                                                                       \
        printf(__VA_ARGS__);                                                                                 \
        printf("\n");                                                                                        \
    } while (0)

/* --- sent messages: ota_status only */
static char states[400][16];
static unsigned offsets[400];
static char last_error[200];
static int nstat;

int sat_send_text(const char *json)
{
    cJSON *j = cJSON_Parse(json);
    const cJSON *t = cJSON_GetObjectItem(j, "type");
    if (cJSON_IsString(t) && !strcmp(t->valuestring, "ota_status") && nstat < 400) {
        snprintf(states[nstat], 16, "%s", cJSON_GetObjectItem(j, "state")->valuestring);
        offsets[nstat] = (unsigned)cJSON_GetObjectItem(j, "offset")->valuedouble;
        const cJSON *e = cJSON_GetObjectItem(j, "error");
        if (cJSON_IsString(e))
            snprintf(last_error, sizeof last_error, "%s", e->valuestring);
        nstat++;
    }
    cJSON_Delete(j);
    return 0;
}
int sat_send_audio(const int16_t *pcm, int n) { return 0; }
void sat_templates_begin(void) {}
void sat_template_msg(const char *kw, const char *name, const char *b64, size_t len, int index, int count) {}
void sat_template_done(int count) {}

/* --- memory flash backend */
#define SLOT (1600 * 1024)
static uint8_t flash_mem[SLOT];
static size_t flash_len, began_size;
static int began, finished, aborted, rebooted, reject_image;

static int b_begin(size_t size, char *err, size_t n)
{
    began++;
    began_size = size;
    flash_len = 0;
    return 0;
}
static int b_write(const uint8_t *d, size_t len, char *err, size_t n)
{
    if (flash_len + len > SLOT) {
        snprintf(err, n, "slot full");
        return -1;
    }
    memcpy(flash_mem + flash_len, d, len);
    flash_len += len;
    return 0;
}
static int b_finish(char *err, size_t n)
{
    if (reject_image) {
        snprintf(err, n, "image rejected: ESP_ERR_OTA_VALIDATE_FAILED");
        return -1;
    }
    finished++;
    return 0;
}
static void b_abort(void) { aborted++; }
static void b_reboot(void) { rebooted++; }
static const ota_backend_t backend = {b_begin, b_write, b_finish, b_abort, b_reboot, NULL};

extern int64_t g_fake_mono_us;

static void server(const char *json)
{
    char err[100];
    proto_on_text(json, strlen(json), err, sizeof err);
}

static void reset_log(void)
{
    nstat = 0;
    last_error[0] = 0;
    began = finished = aborted = rebooted = 0;
}

static void begin_msg(size_t size, const uint8_t *img)
{
    uint8_t h[32];
    sha256_t s;
    sha256_init(&s);
    sha256_update(&s, img, size);
    sha256_final(&s, h);
    char hex[65], msg[300];
    for (int i = 0; i < 32; i++)
        sprintf(hex + 2 * i, "%02x", h[i]);
    snprintf(msg, sizeof msg, "{\"type\":\"ota_begin\",\"size\":%zu,\"sha256\":\"%s\",\"version\":\"1.2.0\"}", size, hex);
    server(msg);
}

/* the server's sender: 16 KB chunks, waits for an ack per 64 KB */
static int send_image(const uint8_t *img, size_t size, size_t chunk)
{
    uint8_t *frame = malloc(5 + chunk);
    size_t off = 0, acked = 0;
    int stalls = 0;
    while (off < size) {
        if (off - acked >= OTA_ACK_BYTES) {
            /* flow control: the latest "receiving" ack */
            size_t a = 0;
            for (int i = 0; i < nstat; i++)
                if (!strcmp(states[i], "receiving"))
                    a = offsets[i];
            if (a <= acked) {
                stalls++;
                break;
            }
            acked = a;
            continue;
        }
        size_t n = size - off < chunk ? size - off : chunk;
        frame[0] = 0x03;
        frame[1] = (uint8_t)off, frame[2] = (uint8_t)(off >> 8), frame[3] = (uint8_t)(off >> 16),
        frame[4] = (uint8_t)(off >> 24);
        memcpy(frame + 5, img + off, n);
        proto_on_binary(frame, 5 + n);
        off += n;
    }
    free(frame);
    return stalls;
}

static int count_state(const char *s)
{
    int n = 0;
    for (int i = 0; i < nstat; i++)
        n += !strcmp(states[i], s);
    return n;
}

int main(void)
{
    fft_init();
    kws_init();
    mixer_init(0);
    engine_init();
    proto_identity_t id = {"test-korvo-ota", "tok", "Korvo", "", ""};
    proto_init(&id, NULL);
    ota_init(&backend, SLOT);
    const size_t size = 1321680;        /* the real app size */
    uint8_t *img = malloc(size);
    unsigned seed = 7;
    for (size_t i = 0; i < size; i++) {
        seed = seed * 1103515245u + 12345u;
        img[i] = (uint8_t)(seed >> 16);
    }

    printf("1. a full update, 16 KB chunks, flow control by 64 KB acks:\n");
    reset_log();
    begin_msg(size, img);
    CHECK(began == 1 && began_size == size && nstat == 1 && !strcmp(states[0], "ready"), "ota_begin -> ready");
    int stalls = send_image(img, size, 16 * 1024);
    CHECK(stalls == 0, "the sender never waited in vain for an ack");
    int acks = count_state("receiving");
    CHECK(acks == (int)((size - 1) / OTA_ACK_BYTES), "%d \"receiving\" acks, one per 64 KB", acks);
    int mono = 1;
    for (int i = 1; i < nstat; i++)
        mono &= !strcmp(states[i], "receiving") ? offsets[i] % OTA_ACK_BYTES == 0 && offsets[i] > offsets[i - 1] : 1;
    CHECK(mono, "ack offsets on 64 KB boundaries, increasing");
    server("{\"type\":\"ota_end\"}");
    CHECK(count_state("verifying") == 1 && count_state("done") == 1 && finished == 1 && rebooted == 1,
          "ota_end -> verifying -> done, image selected, reboot");
    CHECK(flash_len == size && !memcmp(flash_mem, img, size), "flash holds the exact image (%zu bytes)", flash_len);

    printf("2. failures:\n");
    reset_log();
    begin_msg(size, img);
    img[1000] ^= 1;                     /* corrupted in transit */
    send_image(img, size, 16 * 1024);
    img[1000] ^= 1;
    server("{\"type\":\"ota_end\"}");
    CHECK(count_state("error") == 1 && strstr(last_error, "sha256") && aborted == 1 && rebooted == 0,
          "sha256 mismatch -> error (\"%s\"), aborted, no reboot", last_error);

    reset_log();
    begin_msg(size, img);
    uint8_t fr[5 + 100] = {0x03, 0x00, 0x40, 0, 0};     /* offset 16384, expected 0 */
    proto_on_binary(fr, sizeof fr);
    CHECK(count_state("error") == 1 && strstr(last_error, "expected 0") && aborted == 1, "out-of-order chunk -> error (\"%s\")",
          last_error);
    send_image(img, 32768, 16 * 1024);
    CHECK(count_state("error") == 1 && flash_len == 0, "frames after an error are ignored");

    reset_log();
    begin_msg(1000, img);
    send_image(img, 1000, 16 * 1024);
    uint8_t extra[5 + 10] = {0x03, 0xe8, 0x03, 0, 0};    /* offset 1000: beyond the size */
    proto_on_binary(extra, sizeof extra);
    CHECK(count_state("error") == 1 && strstr(last_error, "more data"), "data beyond the announced size -> error");

    reset_log();
    server("{\"type\":\"ota_begin\",\"size\":2000000,\"sha256\":\"00\",\"version\":\"9\"}");
    CHECK(count_state("error") == 1 && began == 0, "image larger than the slot refused (\"%s\")", last_error);
    reset_log();
    server("{\"type\":\"ota_begin\",\"size\":1000,\"sha256\":\"zz\",\"version\":\"9\"}");
    CHECK(count_state("error") == 1 && began == 0, "malformed sha256 refused");

    reset_log();
    begin_msg(size, img);
    send_image(img, 100000, 16 * 1024);
    server("{\"type\":\"ota_end\"}");
    CHECK(count_state("error") == 1 && strstr(last_error, "received"), "ota_end before all data -> error (\"%s\")",
          last_error);

    reset_log();
    begin_msg(4096, img);
    send_image(img, 4096, 4096);
    reject_image = 1;
    server("{\"type\":\"ota_end\"}");
    reject_image = 0;
    CHECK(count_state("error") == 1 && rebooted == 0 && strstr(last_error, "rejected"),
          "image rejected by the bootloader check -> error, no reboot");

    reset_log();
    g_fake_mono_us = 1000;
    begin_msg(size, img);
    send_image(img, 32768, 16 * 1024);
    ota_tick(1000 + 29000000LL);
    CHECK(count_state("error") == 0, "29 s without data: still waiting");
    ota_tick(1000 + 31000000LL);
    CHECK(count_state("error") == 1 && strstr(last_error, "timeout") && aborted == 1, "31 s without data -> timeout");
    g_fake_mono_us = -1;

    reset_log();
    begin_msg(size, img);
    send_image(img, 65536, 16 * 1024);
    proto_on_disconnect();
    CHECK(aborted == 1 && !ota_active(), "connection lost -> update aborted");
    reset_log();
    begin_msg(size, img);
    CHECK(count_state("ready") == 1 && began == 1, "a new ota_begin after that starts cleanly");
    send_image(img, size, 16 * 1024);
    server("{\"type\":\"ota_end\"}");
    CHECK(count_state("done") == 1 && !memcmp(flash_mem, img, size), "and completes");

    printf("3. sha256 known answers:\n");
    {
        uint8_t h[32];
        sha256_t s;
        sha256_init(&s);
        sha256_update(&s, "abc", 3);
        sha256_final(&s, h);
        static const uint8_t abc[4] = {0xba, 0x78, 0x16, 0xbf};
        CHECK(!memcmp(h, abc, 4) && h[31] == 0xad, "sha256(\"abc\") = ba7816bf...ad");
        sha256_init(&s);
        for (int i = 0; i < 1000000; i++)
            sha256_update(&s, "a", 1);
        sha256_final(&s, h);
        CHECK(h[0] == 0xcd && h[1] == 0xc7 && h[31] == 0xd0, "sha256(1M x 'a') = cdc76e5c...d0");
    }
    printf("%s (%d failure%s)\n", fails ? "FAILED" : "PASSED", fails, fails == 1 ? "" : "s");
    return fails ? 1 : 0;
}
