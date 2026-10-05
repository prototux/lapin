/* sat_port.h for a PC: pthread locks, malloc, clock_gettime, stderr logs.
 * The transport and template hooks are provided by each test program. */
#define _GNU_SOURCE
#include <pthread.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

#include "sat_port.h"

struct sat_lock {
    pthread_mutex_t m;
};

sat_lock_t *sat_lock_new(void)
{
    sat_lock_t *l = calloc(1, sizeof *l);
    pthread_mutexattr_t a;
    pthread_mutexattr_init(&a);
    pthread_mutexattr_settype(&a, PTHREAD_MUTEX_RECURSIVE);
    pthread_mutex_init(&l->m, &a);
    return l;
}
void sat_lock(sat_lock_t *l) { pthread_mutex_lock(&l->m); }
void sat_yield(void) {}
void sat_unlock(sat_lock_t *l) { pthread_mutex_unlock(&l->m); }

size_t g_big_bytes, g_big_peak;
void *sat_calloc_big(size_t n) { return calloc(1, n); }
void *sat_calloc_fast(size_t n) { return calloc(1, n); }
void *sat_calloc_hot(size_t n) { return calloc(1, n); }
void sat_free(void *p) { free(p); }

int64_t g_fake_mono_us = -1;    /* tests may drive the clock */
int64_t sat_mono_us(void)
{
    if (g_fake_mono_us >= 0)
        return g_fake_mono_us;
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (int64_t)ts.tv_sec * 1000000 + ts.tv_nsec / 1000;
}

int64_t sat_real_ns(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    return (int64_t)ts.tv_sec * 1000000000LL + ts.tv_nsec;
}

void sat_set_time_ns(int64_t ns) { (void)ns; }

int g_log_level = 1;
void sat_log(int level, const char *fmt, ...)
{
    if (level > g_log_level)
        return;
    va_list ap;
    va_start(ap, fmt);
    fprintf(stderr, "%s", level == 0 ? "E: " : level == 1 ? "I: " : "D: ");
    vfprintf(stderr, fmt, ap);
    fputc('\n', stderr);
    va_end(ap);
}

/* default: nothing to do when the server welcomes the device */
__attribute__((weak)) void sat_on_online(void) {}

/* host: persist the wake word model right away */
#include "templates.h"
__attribute__((weak)) void sat_store_changed(void) { tpl_save_current(); }
