#ifndef SATD_IPC_H
#define SATD_IPC_H

#include <stddef.h>
#include <stdint.h>

/*
 * Local control socket (Unix stream). Every message is framed as
 *     u32 length (LE, of type + payload) | u8 type | payload
 * Types:
 *     1  JSON (UTF-8)                both directions
 *     2  uplink audio                engine -> client: s16le mono 16 kHz
 *     3  downlink audio              client -> engine: u32 stream id | s16le PCM
 * Clients get JSON events; audio and meters only after subscribing.
 */
enum { MSG_JSON = 1, MSG_UPLINK = 2, MSG_DOWNLINK = 3 };
enum { SUB_AUDIO = 1, SUB_METERS = 2 };

typedef void (*ipc_handler_t)(int client, int type, const uint8_t *data, size_t len);

int ipc_listen(const char *path, ipc_handler_t handler);
void ipc_poll(int timeout_ms);
void ipc_close(void);

void ipc_subscribe(int client, int mask);
/* Thread-safe; client < 0 broadcasts. `sub` restricts to subscribed clients. */
void ipc_send(int client, int type, int sub, const void *data, size_t len);
void ipc_json(int client, int sub, const char *fmt, ...) __attribute__((format(printf, 3, 4)));
int ipc_clients(void);

#endif
