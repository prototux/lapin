/* Firmware update over the WebSocket: protocol state machine (see ota.h). */
#include "ota.h"

#include <stdio.h>
#include <string.h>

#include "sat_port.h"
#include "sha256.h"

enum { O_IDLE, O_RECEIVING, O_DONE };

static const ota_backend_t *be;
static size_t max_size;
static struct {
    int state;
    size_t size, got, next_ack;
    uint8_t want[32];
    sha256_t sha;
    int64_t last_us;
    char version[24];
} O;

static void status(const char *state, size_t offset, const char *err)
{
    char buf[300];
    if (err)
        snprintf(buf, sizeof buf, "{\"type\":\"ota_status\",\"state\":\"%s\",\"offset\":%u,\"error\":\"%s\"}", state,
                 (unsigned)offset, err);
    else
        snprintf(buf, sizeof buf, "{\"type\":\"ota_status\",\"state\":\"%s\",\"offset\":%u}", state, (unsigned)offset);
    sat_send_text(buf);
}

static void fail(const char *err)
{
    sat_log(0, "ota: %s", err);
    if (O.state == O_RECEIVING && be && be->abort)
        be->abort();
    status("error", O.got, err);
    O.state = O_IDLE;
    if (be && be->progress)
        be->progress(0, 0);
}

static int hexval(char c)
{
    return c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 : c >= 'A' && c <= 'F' ? c - 'A' + 10 : -1;
}

void ota_init(const ota_backend_t *b, size_t max) { be = b, max_size = max; }
int ota_active(void) { return O.state == O_RECEIVING; }

void ota_on_begin(size_t size, const char *hex, const char *version)
{
    char err[120] = "";
    if (O.state == O_RECEIVING) {
        sat_log(1, "ota: restarting a transfer in progress");
        if (be && be->abort)
            be->abort();
        O.state = O_IDLE;
    }
    O.got = 0;
    if (!be) {
        status("error", 0, "updates not supported");
        return;
    }
    if (size == 0 || size > max_size) {
        snprintf(err, sizeof err, "image size %u does not fit (max %u)", (unsigned)size, (unsigned)max_size);
        status("error", 0, err);
        return;
    }
    if (!hex || strlen(hex) != 64) {
        status("error", 0, "bad sha256");
        return;
    }
    for (int i = 0; i < 32; i++) {
        int a = hexval(hex[2 * i]), b = hexval(hex[2 * i + 1]);
        if (a < 0 || b < 0) {
            status("error", 0, "bad sha256");
            return;
        }
        O.want[i] = (uint8_t)(a << 4 | b);
    }
    if (be->begin(size, err, sizeof err) != 0) {
        status("error", 0, err[0] ? err : "cannot start the update");
        return;
    }
    snprintf(O.version, sizeof O.version, "%s", version ? version : "");
    O.size = size;
    O.next_ack = OTA_ACK_BYTES;
    sha256_init(&O.sha);
    O.state = O_RECEIVING;
    O.last_us = sat_mono_us();
    sat_log(1, "ota: receiving %s (%u bytes)", O.version, (unsigned)size);
    if (be->progress)
        be->progress(1, 0);
    status("ready", 0, NULL);
}

void ota_on_data(const uint8_t *d, size_t len)
{
    if (O.state != O_RECEIVING)
        return;                         /* late frames after an error: ignored */
    if (len < 5 || d[0] != 0x03) {
        fail("bad frame");
        return;
    }
    uint32_t off = (uint32_t)d[1] | (uint32_t)d[2] << 8 | (uint32_t)d[3] << 16 | (uint32_t)d[4] << 24;
    const uint8_t *p = d + 5;
    size_t n = len - 5;
    if (off != O.got) {
        char err[80];
        snprintf(err, sizeof err, "chunk at offset %u, expected %u", (unsigned)off, (unsigned)O.got);
        fail(err);
        return;
    }
    if (O.got + n > O.size) {
        fail("more data than announced");
        return;
    }
    char err[120] = "";
    if (be->write(p, n, err, sizeof err) != 0) {
        fail(err[0] ? err : "flash write failed");
        return;
    }
    sha256_update(&O.sha, p, n);
    O.got += n;
    O.last_us = sat_mono_us();
    if (be->progress)
        be->progress(1, (float)O.got / (float)O.size);
    if (O.got >= O.next_ack && O.got < O.size) {
        status("receiving", O.got, NULL);
        while (O.next_ack <= O.got)
            O.next_ack += OTA_ACK_BYTES;
    }
}

void ota_on_end(void)
{
    if (O.state != O_RECEIVING) {
        status("error", O.got, "no update in progress");
        return;
    }
    if (O.got != O.size) {
        char err[80];
        snprintf(err, sizeof err, "received %u of %u bytes", (unsigned)O.got, (unsigned)O.size);
        fail(err);
        return;
    }
    status("verifying", O.got, NULL);
    uint8_t got[32];
    sha256_final(&O.sha, got);
    if (memcmp(got, O.want, 32)) {
        fail("sha256 mismatch");
        return;
    }
    char err[120] = "";
    if (be->finish(err, sizeof err) != 0) {
        O.state = O_IDLE;           /* finish already released the slot */
        status("error", O.got, err[0] ? err : "image rejected");
        if (be->progress)
            be->progress(0, 0);
        return;
    }
    O.state = O_DONE;
    sat_log(1, "ota: %s written, rebooting", O.version);
    status("done", O.got, NULL);
    if (be->reboot)
        be->reboot();
}

void ota_tick(int64_t now)
{
    if (O.state == O_RECEIVING && now - O.last_us > OTA_TIMEOUT_US)
        fail("timeout: no data for 30 s");
}

void ota_on_disconnect(void)
{
    if (O.state == O_RECEIVING) {
        sat_log(0, "ota: connection lost, update aborted");
        if (be && be->abort)
            be->abort();
        O.state = O_IDLE;
        if (be && be->progress)
            be->progress(0, 0);
    }
}
