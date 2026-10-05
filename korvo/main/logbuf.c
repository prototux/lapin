#include "logbuf.h"

#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "esp_attr.h"
#include "esp_core_dump.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "link.h"
#include "mem.h"
#include "sat_common.h"

#define RAM_RING   (16 * 1024)
#define RTC_WORDS  (3072 / 4)
#define RTC_MAGIC  0x4B4C4F47u      /* "KLOG" */
#define MAX_LINES  200
#define MAX_MSG    (16 * 1024)

/* RTC slow memory keeps its content across every reset but a power cut. It
 * is word-addressed: bytes are packed into 32-bit words. */
typedef struct {
    uint32_t magic, head, len, boots;
    uint32_t w[RTC_WORDS];
} rtc_ring_t;
static RTC_NOINIT_ATTR rtc_ring_t rtc;

static portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;
static char *ram;                   /* RAM ring */
static size_t ram_cap;
static uint64_t ram_total;          /* bytes ever written */
static uint64_t sent_upto;          /* ram_total already sent */
static volatile int new_problems;   /* E/W lines since the last send */
static int full_pending;
static char *prev;                  /* previous boot, as text */
static size_t prev_len;
static char crash[400];
static const char *reset_reason = "unknown";
static uint32_t boot_count;
static vprintf_like_t orig;
static int64_t last_send_us;

static const char *reason_name(esp_reset_reason_t r)
{
    switch (r) {
    case ESP_RST_POWERON: return "poweron";
    case ESP_RST_EXT: return "external";
    case ESP_RST_SW: return "sw";
    case ESP_RST_PANIC: return "panic";
    case ESP_RST_INT_WDT: return "int_wdt";
    case ESP_RST_TASK_WDT: return "task_wdt";
    case ESP_RST_WDT: return "wdt";
    case ESP_RST_DEEPSLEEP: return "deepsleep";
    case ESP_RST_BROWNOUT: return "brownout";
    case ESP_RST_SDIO: return "sdio";
    default: return "unknown";
    }
}

static void rtc_put(char c)
{
    uint32_t i = rtc.head;
    uint32_t *w = &rtc.w[i / 4];
    int sh = (int)(i % 4) * 8;
    *w = (*w & ~(0xFFu << sh)) | ((uint32_t)(uint8_t)c << sh);
    rtc.head = (i + 1) % (RTC_WORDS * 4);
    if (rtc.len < RTC_WORDS * 4)
        rtc.len++;
}

static char rtc_get(uint32_t i) { return (char)(rtc.w[i / 4] >> ((i % 4) * 8)); }

static void append(const char *s, size_t n)
{
    portENTER_CRITICAL_SAFE(&mux);
    for (size_t k = 0; k < n; k++) {
        if (ram)
            ram[ram_total % ram_cap] = s[k];
        ram_total++;
        rtc_put(s[k]);
    }
    portEXIT_CRITICAL_SAFE(&mux);
}

static int hook(const char *fmt, va_list ap)
{
    char line[200];
    va_list ap2;
    va_copy(ap2, ap);
    int n = vsnprintf(line, sizeof line, fmt, ap2);
    va_end(ap2);
    if (n > 0) {
        size_t len = n < (int)sizeof line ? (size_t)n : sizeof line - 1;
        if (len >= sizeof line - 1)
            line[len - 1] = '\n';       /* truncated: keep line structure */
        append(line, len);
        if ((line[0] == 'E' || line[0] == 'W') && line[1] == ' ' && line[2] == '(')
            new_problems = 1;
    }
    return orig ? orig(fmt, ap) : vprintf(fmt, ap);
}

void logbuf_init(void)
{
    esp_reset_reason_t rr = esp_reset_reason();
    reset_reason = reason_name(rr);
    /* the previous boot's lines, if the RTC ring survived */
    if (rtc.magic == RTC_MAGIC && rtc.len <= RTC_WORDS * 4 && rtc.head < RTC_WORDS * 4 && rr != ESP_RST_POWERON) {
        prev = mem_alloc_big(rtc.len + 1);
        if (prev) {
            uint32_t start = (rtc.head + RTC_WORDS * 4 - rtc.len) % (RTC_WORDS * 4);
            for (uint32_t i = 0; i < rtc.len; i++)
                prev[i] = rtc_get((start + i) % (RTC_WORDS * 4));
            prev_len = rtc.len;
            prev[prev_len] = 0;
            /* drop a partial first line */
            char *nl = memchr(prev, '\n', prev_len);
            if (nl && rtc.len == RTC_WORDS * 4) {
                size_t cut = (size_t)(nl - prev) + 1;
                memmove(prev, prev + cut, prev_len - cut + 1);
                prev_len -= cut;
            }
        }
    }
    memset(&rtc, 0, sizeof rtc);
    rtc.magic = RTC_MAGIC;
    ram_cap = RAM_RING;
    ram = mem_alloc_big(ram_cap);
    if (!ram) {
        ram_cap = 4096;
        ram = mem_alloc_internal(ram_cap);
    }
    orig = esp_log_set_vprintf(hook);
    ESP_LOGI("log", "reset reason: %s, previous boot: %u bytes of log kept", reset_reason, (unsigned)prev_len);
#if CONFIG_ESP_COREDUMP_ENABLE_TO_FLASH
    esp_core_dump_summary_t *sum = mem_alloc_big(sizeof *sum);
    if (sum && esp_core_dump_image_check() == ESP_OK && esp_core_dump_get_summary(sum) == ESP_OK) {
        int o = snprintf(crash, sizeof crash, "crash: task %s, pc 0x%08lx, cause %lu, vaddr 0x%08lx, backtrace",
                         sum->exc_task, (unsigned long)sum->exc_pc, (unsigned long)sum->ex_info.exc_cause,
                         (unsigned long)sum->ex_info.exc_vaddr);
        for (uint32_t i = 0; i < sum->exc_bt_info.depth && i < 16 && o < (int)sizeof crash - 12; i++)
            o += snprintf(crash + o, sizeof crash - o, " 0x%08lx", (unsigned long)sum->exc_bt_info.bt[i]);
        ESP_LOGE("log", "%s (app elf %.16s)", crash, (const char *)sum->app_elf_sha256);
        esp_core_dump_image_erase();    /* report it once */
    }
    if (sum)
        heap_caps_free(sum);
#endif
    full_pending = 1;
}

void logbuf_set_boot_count(uint32_t n) { boot_count = n; }
uint32_t logbuf_boot_count(void) { return boot_count; }
const char *logbuf_reset_reason(void) { return reset_reason; }
void logbuf_on_online(void) { full_pending = 1; }

/* ------------------------------------------------------------ report --- */

typedef struct {
    char *buf;
    size_t n, cap;
    int lines;
} jb_t;

static void jb_add_line(jb_t *j, const char *s, size_t len)
{
    while (len && (s[len - 1] == '\n' || s[len - 1] == '\r'))
        len--;
    if (!len || j->lines >= MAX_LINES || j->n + len * 2 + 16 > j->cap)
        return;
    j->buf[j->n++] = j->lines ? ',' : '[';
    j->buf[j->n++] = '"';
    for (size_t i = 0; i < len; i++) {
        unsigned char c = (unsigned char)s[i];
        if (c == '"' || c == '\\') {
            j->buf[j->n++] = '\\';
            j->buf[j->n++] = (char)c;
        } else if (c < 0x20 || c == 0x7f) {
            j->buf[j->n++] = ' ';       /* control characters (escape codes...) */
        } else {
            j->buf[j->n++] = (char)c;
        }
    }
    j->buf[j->n++] = '"';
    j->lines++;
}

/* adds the lines of text [s, s+len): the newest ones, at most `max` lines
 * and `share` (0..1) of the room left in the message */
static void add_text(jb_t *j, const char *s, size_t len, int max, float share, const char *prefix)
{
    const size_t pl = strlen(prefix);
    const size_t budget = (size_t)((float)(j->cap - j->n) * share * 0.85f);
    int count = 0;
    size_t start = len, bytes = 0;
    while (start > 0 && count < max) {
        size_t e = start - 1;
        while (e > 0 && s[e - 1] != '\n')
            e--;
        size_t l = start - e;
        if (bytes + l + pl + 4 > budget)
            break;
        bytes += l + pl + 4;
        start = e;
        count++;
    }
    size_t p = start;
    char tmp[230];
    while (p < len) {
        const char *nl = memchr(s + p, '\n', len - p);
        size_t l = nl ? (size_t)(nl - (s + p)) : len - p;
        size_t k = l < sizeof tmp - pl - 1 ? l : sizeof tmp - pl - 1;
        memcpy(tmp, prefix, pl);
        memcpy(tmp + pl, s + p, k);
        jb_add_line(j, tmp, pl + k);
        p += l + 1;
    }
}

/* copy of RAM ring bytes [from, ram_total) */
static char *ram_copy(uint64_t from, size_t *len)
{
    portENTER_CRITICAL_SAFE(&mux);
    uint64_t total = ram_total;
    portEXIT_CRITICAL_SAFE(&mux);
    if (from + ram_cap < total)
        from = total - ram_cap;
    size_t n = (size_t)(total - from);
    char *c = mem_alloc_big(n + 1);
    if (!c)
        return NULL;
    portENTER_CRITICAL_SAFE(&mux);
    for (size_t i = 0; i < n; i++)
        c[i] = ram[(from + i) % ram_cap];
    portEXIT_CRITICAL_SAFE(&mux);
    c[n] = 0;
    *len = n;
    sent_upto = total;
    return c;
}

static void send_report(int full)
{
    jb_t j = {mem_alloc_big(MAX_MSG), 0, MAX_MSG - 64, 0};
    if (!j.buf)
        return;
    int o = snprintf(j.buf, MAX_MSG,
                     "{\"type\":\"log\",\"version\":\"%s\",\"reset_reason\":\"%s\",\"boot_count\":%lu,"
                     "\"uptime_s\":%lld,\"full\":%s,\"lines\":",
                     SAT_VERSION, reset_reason, (unsigned long)boot_count,
                     (long long)(esp_timer_get_time() / 1000000), full ? "true" : "false");
    j.n = (size_t)o;
    size_t len = 0;
    char *cur = ram_copy(full ? 0 : sent_upto, &len);
    if (full) {
        if (crash[0])
            jb_add_line(&j, crash, strlen(crash));
        if (prev && prev_len)
            add_text(&j, prev, prev_len, 70, 0.4f, "[prev] ");
    }
    if (cur)
        add_text(&j, cur, len, MAX_LINES - j.lines, 1.0f, "");
    heap_caps_free(cur);
    if (!j.lines) {
        j.buf[j.n++] = '[';
    }
    j.buf[j.n++] = ']';
    j.buf[j.n++] = '}';
    j.buf[j.n] = 0;
    link_send_text(j.buf);
    heap_caps_free(j.buf);
}

void logbuf_poll(int online)
{
    if (!online)
        return;
    int64_t now = esp_timer_get_time();
    if (full_pending) {
        full_pending = 0;
        new_problems = 0;
        last_send_us = now;
        send_report(1);
    } else if (new_problems && now - last_send_us > 10000000LL) {
        new_problems = 0;
        last_send_us = now;
        send_report(0);
    }
}

size_t logbuf_text(char *out, size_t n, int include_prev)
{
    size_t o = 0;
    if (!n)
        return 0;
    if (include_prev && crash[0])
        o += (size_t)snprintf(out + o, n - o, "%s\n", crash);
    if (include_prev && prev && o < n) {
        size_t k = prev_len < (n - o) / 3 ? prev_len : (n - o) / 3;
        o += (size_t)snprintf(out + o, n - o, "--- previous boot ---\n%s\n--- this boot ---\n", prev + prev_len - k);
    }
    portENTER_CRITICAL_SAFE(&mux);
    uint64_t total = ram_total;
    size_t avail = total < ram_cap ? (size_t)total : ram_cap;
    size_t k = avail < n - o - 1 ? avail : n - o - 1;
    for (size_t i = 0; ram && i < k; i++)
        out[o + i] = ram[(total - k + i) % ram_cap];
    portEXIT_CRITICAL_SAFE(&mux);
    o += k;
    out[o < n ? o : n - 1] = 0;
    return o;
}
