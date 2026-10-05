/* sat_port.h for the benchmark: like main/port_esp.c without the network. */
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "sat_port.h"

struct sat_lock {
    SemaphoreHandle_t m;
};
sat_lock_t *sat_lock_new(void)
{
    sat_lock_t *l = calloc(1, sizeof *l);
    l->m = xSemaphoreCreateRecursiveMutex();
    return l;
}
void sat_lock(sat_lock_t *l) { xSemaphoreTakeRecursive(l->m, portMAX_DELAY); }
void sat_unlock(sat_lock_t *l) { xSemaphoreGiveRecursive(l->m); }
void *sat_calloc_big(size_t n)
{
    void *p = heap_caps_calloc(1, n, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    return p ? p : heap_caps_calloc(1, n, MALLOC_CAP_8BIT);
}
void *sat_calloc_fast(size_t n)
{
    if (heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT) > n + 48 * 1024) {
        void *p = heap_caps_calloc(1, n, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
        if (p)
            return p;
    }
    return sat_calloc_big(n);
}
void *sat_calloc_hot(size_t n)
{
    void *p = heap_caps_calloc(1, n, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    return p ? p : sat_calloc_big(n);
}
void sat_free(void *p) { heap_caps_free(p); }
void sat_yield(void) {}
int64_t sat_mono_us(void) { return esp_timer_get_time(); }
int64_t sat_real_ns(void) { return 0; }
void sat_set_time_ns(int64_t ns) {}
void sat_log(int level, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vprintf(fmt, ap);
    va_end(ap);
    printf("\n");
}
extern int g_wakes;
int sat_send_text(const char *json)
{
    if (strstr(json, "\"type\":\"wake\""))
        g_wakes++;
    return 0;
}
int sat_send_audio(const int16_t *pcm, int samples) { return 0; }
void sat_template_msg(const char *k, const char *n, const char *b, size_t l, int i, int c) {}
void sat_template_done(int count) {}
void sat_templates_begin(void) {}
