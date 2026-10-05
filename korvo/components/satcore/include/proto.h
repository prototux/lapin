#ifndef SAT_PROTO_H
#define SAT_PROTO_H

#include "sat_common.h"

/*
 * The device protocol (docs/PROTOCOL.md) for a kind "satellite" device:
 * hello, server message dispatch (the satellite agent's on_server), clock
 * offset from welcome / pong, playback audio frames. Transport-agnostic: the
 * WebSocket lives in main/link.c (device) or in the test harness (host).
 */

typedef struct {
    char device_id[48];
    char token[64];
    char name[64];
    char room[64];
    char owner[48];
} proto_identity_t;

enum { PROTO_OK, PROTO_WELCOME, PROTO_PENDING, PROTO_ERROR };

/* Name / room pushed by the server (welcome config or config message). */
typedef void (*proto_config_cb_t)(const char *name, const char *room);

void proto_init(const proto_identity_t *id, proto_config_cb_t cfg_cb);
/* Builds the hello message. Returns its length, or -1 if it does not fit. */
int proto_hello(char *out, size_t n);
/* Handles one text frame. Returns PROTO_WELCOME / PROTO_PENDING /
 * PROTO_ERROR for the link layer (error message in err), else PROTO_OK. */
int proto_on_text(const char *msg, size_t len, char *err, size_t errn);
/* Handles one binary frame (0x02 + u32 stream id + PCM). */
void proto_on_binary(const uint8_t *data, size_t len);
/* Called by the link layer when the socket goes down. */
void proto_on_disconnect(void);
/* Application-level ping (clock sync); call every few seconds. */
void proto_send_ping(void);
/* Server clock - local clock, ns (0 until known). */
int64_t proto_clock_offset_ns(void);
int proto_rtt_ms(void);

#endif
