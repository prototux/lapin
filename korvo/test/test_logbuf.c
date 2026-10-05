/* Host test of main/logbuf.c (device log capture and the "log" message):
 * compiled with stub ESP-IDF headers (test/stubs). */
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "esp_log.h"
#include "esp_system.h"
#include "logbuf.h"

static vprintf_like_t hook;
static int quiet(const char *f, va_list ap) { return 0; }
vprintf_like_t esp_log_set_vprintf(vprintf_like_t f)
{
    hook = f;
    return quiet;
}
void test_log(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    if (hook)
        hook(fmt, ap);
    va_end(ap);
}
static esp_reset_reason_t rr = ESP_RST_POWERON;
esp_reset_reason_t esp_reset_reason(void) { return rr; }
static int64_t now_us;
int64_t esp_timer_get_time(void) { return now_us; }
void *mem_alloc_big(size_t n) { return malloc(n); }
void *mem_alloc_internal(size_t n) { return malloc(n); }

static int sent;
int link_send_text(const char *json)
{
    char path[64];
    snprintf(path, sizeof path, "/tmp/test_logbuf_%d.json", sent++);
    FILE *f = fopen(path, "w");
    fputs(json, f);
    fclose(f);
    return 0;
}

int main(void)
{
    /* boot 1: lots of lines, then a "crash" (software reset keeps RTC) */
    logbuf_init();
    for (int i = 0; i < 300; i++)
        ESP_LOGI("test", "boot one line %d \"quoted\" \\ back\tslash", i);
    ESP_LOGE("test", "the last words before the crash");
    /* boot 2 */
    rr = ESP_RST_PANIC;
    logbuf_init();
    logbuf_set_boot_count(7);
    for (int i = 0; i < 1000; i++)
        ESP_LOGI("test", "boot two line %d %s", i, "................................................................");
    logbuf_poll(1);                 /* full report */
    now_us = 5000000;
    ESP_LOGE("wifi", "a new error");
    logbuf_poll(1);                 /* too soon: rate limited */
    now_us = 16000000;
    logbuf_poll(1);                 /* incremental */
    char txt[4000];
    size_t n = logbuf_text(txt, sizeof txt, 1);
    printf("sent %d messages, setup page text %zu bytes, starts with: %.40s\n", sent, n, txt);
    return 0;
}
