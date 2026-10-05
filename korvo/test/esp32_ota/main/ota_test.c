/*
 * Run 1 (from ota_0): sends the embedded firmware through ota.c exactly as
 * proto.c would (ota_begin, 16 KB 0x03 frames with offsets, ota_end), with
 * the device backend (main/ota_esp.c); checks the acks; the backend reboots.
 * The new image (the real Korvo firmware) then boots from ota_1, pending
 * verification; it never gets a welcome under QEMU, so after the next reset
 * the bootloader rolls back here.
 * Run 2 (back in ota_0): reports the rollback.
 */
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "esp_ota_ops.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "ota.h"
#include "sha256.h"

extern const uint8_t img_start[] asm("_binary_korvo_satellite_bin_start");
extern const uint8_t img_end[] asm("_binary_korvo_satellite_bin_end");
void port_ota_init(void);

static int acks, ready, done, errors;
int sat_send_text(const char *json)
{
    printf("OTATEST: <- %s\n", json);
    acks += strstr(json, "\"receiving\"") != NULL;
    ready += strstr(json, "\"ready\"") != NULL;
    done += strstr(json, "\"done\"") != NULL;
    errors += strstr(json, "\"error\"") != NULL;
    return 0;
}
int sat_send_audio(const int16_t *p, int n) { return 0; }
void sat_log(int level, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    printf("OTATEST: ");
    vprintf(fmt, ap);
    printf("\n");
    va_end(ap);
}
int64_t sat_mono_us(void) { return esp_timer_get_time(); }

void app_main(void)
{
    const esp_partition_t *run = esp_ota_get_running_partition();
    const esp_partition_t *bad = esp_ota_get_last_invalid_partition();
    printf("OTATEST: running from %s, last invalid: %s\n", run->label, bad ? bad->label : "none");
    if (bad) {
        printf("OTATEST: ROLLBACK OK: %s was never confirmed and the bootloader returned to %s\n", bad->label,
               run->label);
        return;
    }
    size_t size = img_end - img_start;
    uint8_t h[32];
    char hex[65];
    sha256_t s;
    sha256_init(&s);
    sha256_update(&s, img_start, size);
    sha256_final(&s, h);
    for (int i = 0; i < 32; i++)
        sprintf(hex + 2 * i, "%02x", h[i]);
    port_ota_init();
    int64_t t0 = esp_timer_get_time();
    ota_on_begin(size, hex, "1.1.0");
    static uint8_t frame[5 + 16384];
    for (size_t off = 0; off < size && !errors; off += 16384) {
        size_t n = size - off < 16384 ? size - off : 16384;
        frame[0] = 0x03;
        frame[1] = (uint8_t)off, frame[2] = (uint8_t)(off >> 8), frame[3] = (uint8_t)(off >> 16),
        frame[4] = (uint8_t)(off >> 24);
        memcpy(frame + 5, img_start + off, n);
        ota_on_data(frame, 5 + n);
    }
    printf("OTATEST: %u bytes sent, %d acks, %lld ms\n", (unsigned)size, acks, (esp_timer_get_time() - t0) / 1000);
    ota_on_end();        /* verifies, selects ota_1 and reboots */
    printf("OTATEST: ready %d done %d errors %d\n", ready, done, errors);
}
