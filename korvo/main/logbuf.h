#pragma once
/*
 * Diagnostics without a serial cable: every ESP_LOG line is also kept in a
 * RAM ring (this boot) and in an RTC_NOINIT ring that survives a crash,
 * watchdog or brownout reset (not a power cut). At each connection the
 * device sends {"type":"log","reset_reason":..,"boot_count":N,"lines":[..]}
 * with the previous boot's last lines, the crash summary (core dump) and this
 * boot's lines; afterwards new warnings / errors are sent as they appear.
 */
#include <stddef.h>
#include <stdint.h>

void logbuf_init(void);                 /* first thing in app_main */
void logbuf_set_boot_count(uint32_t n);
const char *logbuf_reset_reason(void);
uint32_t logbuf_boot_count(void);
void logbuf_on_online(void);            /* queues the full report */
void logbuf_poll(int online);           /* sends what is pending (call ~1/s) */
/* The log as plain text, newest last (for the setup page); returns length. */
size_t logbuf_text(char *out, size_t n, int include_prev);
