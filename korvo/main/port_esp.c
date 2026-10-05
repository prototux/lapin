/* sat_port.h on the ESP32: FreeRTOS locks, PSRAM, clocks, logs, transport,
 * and the worker task that turns the server's recordings into templates. */
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <sys/time.h>

#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "link.h"
#include "mem.h"
#include "sat_common.h"
#include "sat_port.h"
#include "templates.h"

struct sat_lock {
    SemaphoreHandle_t m;
};

sat_lock_t *sat_lock_new(void)
{
    sat_lock_t *l = mem_alloc_internal(sizeof *l);
    if (l)
        l->m = xSemaphoreCreateRecursiveMutex();
    return l;
}
void sat_lock(sat_lock_t *l) { xSemaphoreTakeRecursive(l->m, portMAX_DELAY); }

void sat_yield(void)
{
    /* per task: the idle task (watchdog) and Wi-Fi must run during a long build */
    static int64_t last;            /* only the template worker calls it */
    int64_t now = esp_timer_get_time();
    if (now - last > 20000) {
        vTaskDelay(1);
        last = esp_timer_get_time();
    }
}
void sat_unlock(sat_lock_t *l) { xSemaphoreGiveRecursive(l->m); }

void *sat_calloc_big(size_t n) { return mem_calloc_big(n); }
void *sat_calloc_fast(size_t n) { return mem_calloc_fast(n); }
void *sat_calloc_hot(size_t n) { return mem_calloc_hot(n); }

void sat_free(void *p) { heap_caps_free(p); }

int64_t sat_mono_us(void) { return esp_timer_get_time(); }

int64_t sat_real_ns(void)
{
    struct timeval tv;
    gettimeofday(&tv, NULL);
    if (tv.tv_sec < 1700000000)         /* not set yet (no NTP, no welcome) */
        return 0;
    return (int64_t)tv.tv_sec * 1000000000LL + (int64_t)tv.tv_usec * 1000;
}

void sat_set_time_ns(int64_t ns)
{
    struct timeval tv;
    gettimeofday(&tv, NULL);
    if (tv.tv_sec >= 1700000000)
        return;                         /* NTP already did it */
    tv.tv_sec = (time_t)(ns / 1000000000LL);
    tv.tv_usec = (suseconds_t)(ns % 1000000000LL / 1000);
    settimeofday(&tv, NULL);
    ESP_LOGI("time", "clock set from the server");
}

void sat_log(int level, const char *fmt, ...)
{
    char buf[256];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    if (level == 0)
        ESP_LOGE("core", "%s", buf);
    else if (level == 1)
        ESP_LOGI("core", "%s", buf);
    else
        ESP_LOGD("core", "%s", buf);
}

int sat_send_text(const char *json) { return link_send_text(json); }
int sat_send_audio(const int16_t *pcm, int samples) { return link_send_audio(pcm, samples); }

/* ------------------------------------------------- templates worker --- */

typedef enum { W_BEGIN, W_TEMPLATE, W_DONE, W_SAVE } wkind_t;

typedef struct {
    wkind_t kind;
    char keyword[48], name[40];
    char *b64;
    size_t len;
    int index, count;
} work_t;

static QueueHandle_t workq;

static void worker_task(void *arg)
{
    work_t w;
    for (;;) {
        if (xQueueReceive(workq, &w, portMAX_DELAY) != pdTRUE)
            continue;
        int64_t t0 = esp_timer_get_time();
        switch (w.kind) {
        case W_BEGIN:
            tpl_collect_begin();
            break;
        case W_TEMPLATE:
            tpl_collect_add(w.keyword, w.name, w.b64, w.len, w.index, w.count);
            ESP_LOGI("tpl", "template %d/%d %s: %lld ms", w.index + 1, w.count, w.name,
                     (esp_timer_get_time() - t0) / 1000);
            heap_caps_free(w.b64);
            break;
        case W_DONE:
            tpl_collect_done(w.count);
            ESP_LOGI("tpl", "model ready in %lld ms (threshold computation included)",
                     (esp_timer_get_time() - t0) / 1000);
            break;
        case W_SAVE:
            tpl_save_current();
            break;
        }
    }
}

void port_start_worker(void)
{
    workq = xQueueCreate(3, sizeof(work_t));
    /* no flash writes here (the store is saved by the main loop): PSRAM stack */
    task_create_psram(worker_task, "tpl", 8192, NULL, 2, NULL, 0);
}

static void post(work_t *w)
{
    /* a short queue: the socket waits while a recording is decoded, so the
     * server's burst of ~1.5 MB does not pile up in memory */
    if (xQueueSend(workq, w, pdMS_TO_TICKS(10000)) != pdTRUE) {
        ESP_LOGE("tpl", "worker stuck, dropping %d", (int)w->kind);
        heap_caps_free(w->b64);
    }
}

void sat_templates_begin(void)
{
    work_t w = {.kind = W_BEGIN};
    post(&w);
}

void sat_template_msg(const char *kw, const char *name, const char *b64, size_t len, int index, int count)
{
    work_t w = {.kind = W_TEMPLATE, .len = len, .index = index, .count = count};
    snprintf(w.keyword, sizeof w.keyword, "%s", kw);
    snprintf(w.name, sizeof w.name, "%s", name);
    w.b64 = mem_calloc_big(len + 1);
    if (!w.b64)
        return;
    memcpy(w.b64, b64, len);
    w.b64[len] = 0;
    post(&w);
}

void sat_template_done(int count)
{
    work_t w = {.kind = W_DONE, .count = count};
    post(&w);
}

/* Flash writes stall the cache on both cores (and with it the speaker, the
 * PSRAM jitter buffers and every task running from flash) for tens of ms per
 * erased sector: the model is saved by the main loop once the audio is quiet
 * (port_flash_quiet). */
static volatile int store_dirty;
void sat_store_changed(void) { store_dirty = 1; }
void port_save_templates(void) { store_dirty = 1; }
int port_take_store_dirty(void)
{
    int d = store_dirty;
    store_dirty = 0;
    return d;
}

/* ------------------------------------------------- server welcome --- */

#include "esp_ota_ops.h"
#include "logbuf.h"

static volatile int first_online_done;

#define OTA_PROOF_S 90

/* Called every second: marks a new firmware valid after OTA_PROOF_S seconds
 * online in a row (the wake word download included); a crash or a lost link
 * before that restarts the count, and the bootloader rolls back to the
 * previous firmware at the next reboot. */
void port_ota_tick(int online, int quiet)
{
    static int online_s, done;
    if (done)
        return;
    const esp_partition_t *run = esp_ota_get_running_partition();
    esp_ota_img_states_t st;
    if (esp_ota_get_state_partition(run, &st) != ESP_OK || st != ESP_OTA_IMG_PENDING_VERIFY) {
        done = 1;
        return;
    }
    online_s = online ? online_s + 1 : 0;
    /* writing otadata stalls the cache like any flash write: not while audio plays */
    if (online_s >= OTA_PROOF_S && quiet && esp_ota_mark_app_valid_cancel_rollback() == ESP_OK) {
        ESP_LOGW("ota", "new firmware %s stayed online %d s: marked valid", SAT_VERSION, OTA_PROOF_S);
        done = 1;
    }
}
void port_reference_chime(void);

void sat_on_online(void)
{
    /* a freshly updated firmware proves itself by staying online (port_ota_tick):
     * reaching the server once is not enough, 1.1.8 did and then crashed every
     * few seconds with no way back */
    logbuf_on_online();
    if (!first_online_done) {
        first_online_done = 1;
        port_reference_chime();
    }
}
