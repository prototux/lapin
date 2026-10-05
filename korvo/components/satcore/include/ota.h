#ifndef SAT_OTA_H
#define SAT_OTA_H

#include <stddef.h>
#include <stdint.h>

/*
 * Firmware update over the device WebSocket (no HTTP needed):
 *   server -> {"type":"ota_begin","size":N,"sha256":"<hex>","version":"x.y.z"}
 *   device -> {"type":"ota_status","state":"ready","offset":0}
 *   server -> binary 0x03 + u32 LE offset + data (16 KB chunks), in order;
 *             it waits for an ack every 64 KB (flow control)
 *   device -> {"type":"ota_status","state":"receiving","offset":N} per 64 KB
 *   server -> {"type":"ota_end"}
 *   device -> verifying, then done (reboots into the new slot) or error.
 * The protocol logic lives here (portable, host-tested); the flash writes
 * are the backend's (esp_ota_* on the device).
 */
#define OTA_ACK_BYTES (64 * 1024)
#define OTA_TIMEOUT_US (30 * 1000000LL)

typedef struct {
    /* return 0 on success, or a negative code; err gets a message */
    int (*begin)(size_t size, char *err, size_t errn);
    int (*write)(const uint8_t *data, size_t len, char *err, size_t errn);
    int (*finish)(char *err, size_t errn);     /* validates the image, selects it for the next boot */
    void (*abort)(void);
    void (*reboot)(void);                      /* after "done" was sent */
    void (*progress)(int active, float fraction);  /* UI, may be NULL */
} ota_backend_t;

void ota_init(const ota_backend_t *b, size_t max_size);
void ota_on_begin(size_t size, const char *sha256_hex, const char *version);
void ota_on_data(const uint8_t *frame, size_t len);   /* the whole 0x03 frame */
void ota_on_end(void);
void ota_tick(int64_t now_us);                        /* aborts a stalled transfer */
void ota_on_disconnect(void);
int ota_active(void);

#endif
