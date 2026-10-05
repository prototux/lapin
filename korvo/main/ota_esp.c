/* Firmware updates over the WebSocket: the esp_ota backend of satcore/ota.c
 * (writes to the other app slot; rollback if the new image never reaches the
 * server, see sat_on_online in port_esp.c). */
#include <stdio.h>

#include "esp_log.h"
#include "esp_ota_ops.h"
#include "audio.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "ota.h"

/* --------------------------------------------------- firmware updates --- */


static esp_ota_handle_t ota_h;
static const esp_partition_t *ota_part;

static int ob_begin(size_t size, char *err, size_t errn)
{
    ota_part = esp_ota_get_next_update_partition(NULL);
    if (!ota_part) {
        snprintf(err, errn, "no update partition");
        return -1;
    }
    if (size > ota_part->size) {
        snprintf(err, errn, "image %u > slot %u", (unsigned)size, (unsigned)ota_part->size);
        return -1;
    }
    /* sequential writes: sectors are erased as the data arrives, so this
     * returns at once instead of erasing 1.6 MB first */
    esp_err_t e = esp_ota_begin(ota_part, OTA_WITH_SEQUENTIAL_WRITES, &ota_h);
    if (e != ESP_OK) {
        snprintf(err, errn, "esp_ota_begin: %s", esp_err_to_name(e));
        return -1;
    }
    ESP_LOGI("ota", "writing to %s at 0x%lx", ota_part->label, (unsigned long)ota_part->address);
    /* flash writes stall the caches: the heavy audio tasks pause meanwhile
     * (they tripped the task watchdog during an update) */
    audio_set_paused(1);
    return 0;
}

static int ob_write(const uint8_t *d, size_t n, char *err, size_t errn)
{
    esp_err_t e = esp_ota_write(ota_h, d, n);
    if (e != ESP_OK) {
        snprintf(err, errn, "esp_ota_write: %s", esp_err_to_name(e));
        return -1;
    }
    return 0;
}

static int ob_finish(char *err, size_t errn)
{
    esp_err_t e = esp_ota_end(ota_h);       /* checks the image (header, segments, hash) */
    ota_h = 0;
    if (e == ESP_OK)
        e = esp_ota_set_boot_partition(ota_part);
    if (e != ESP_OK) {
        snprintf(err, errn, "image rejected: %s", esp_err_to_name(e));
        audio_set_paused(0);
        return -1;
    }
    return 0;                       /* audio stays paused: the reboot follows */
}

static void ob_abort(void)
{
    if (ota_h)
        esp_ota_abort(ota_h);
    ota_h = 0;
    audio_set_paused(0);
}

static void reboot_task(void *arg)
{
    vTaskDelay(pdMS_TO_TICKS(1500));        /* let "done" go out */
    esp_restart();
}

static void ob_reboot(void) { xTaskCreate(reboot_task, "reboot", 2048, NULL, 5, NULL); }

/* the audio pauses during flash writes; weak so the QEMU test app can do without it */
__attribute__((weak)) void audio_set_paused(int on) {}
/* UI hook (the LED ring); weak so the QEMU test app can do without it */
__attribute__((weak)) void leds_set_progress(int active, float f) {}
static void ob_progress(int active, float f) { leds_set_progress(active, f); }

static const ota_backend_t ota_backend = {ob_begin, ob_write, ob_finish, ob_abort, ob_reboot, ob_progress};

void port_ota_init(void)
{
    const esp_partition_t *next = esp_ota_get_next_update_partition(NULL);
    ota_init(&ota_backend, next ? next->size : 0);
}

