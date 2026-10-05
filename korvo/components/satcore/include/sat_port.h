/*
 * Platform services used by the portable core. Implemented by
 * main/port_esp.c on the device and by test/port_host.c on a PC.
 */
#ifndef SAT_PORT_H
#define SAT_PORT_H

#include <stddef.h>
#include <stdint.h>

typedef struct sat_lock sat_lock_t;

sat_lock_t *sat_lock_new(void);
void sat_lock(sat_lock_t *l);
void sat_unlock(sat_lock_t *l);

/* Large buffers (PSRAM on the device), zero-filled. */
void *sat_calloc_big(size_t n);
/* Small hot buffers (internal RAM if there is room, else PSRAM), zero-filled. */
void *sat_calloc_fast(size_t n);
/* Data read on every audio hop (wake word features and DTW state): internal
 * RAM unless that would leave too little for Wi-Fi, else PSRAM. */
void *sat_calloc_hot(size_t n);
void sat_free(void *p);

int64_t sat_mono_us(void);      /* monotonic clock */
int64_t sat_real_ns(void);      /* wall clock (UTC), 0 if unknown */
/* Long computations (template building) call this often: it gives the CPU
 * back now and then (ESP32: a tick every ~20 ms of work), a no-op on the PC. */
void sat_yield(void);

void sat_log(int level, const char *fmt, ...) __attribute__((format(printf, 2, 3)));

/* Transport to the server (WebSocket). Both return 0 on success. */
int sat_send_text(const char *json);
int sat_send_audio(const int16_t *pcm, int samples);   /* adds the 0x01 header */

/* Sets the wall clock (from the server's welcome) when it is not known yet. */
void sat_set_time_ns(int64_t real_ns);

/* Wake word templates arriving from the server. The platform either calls
 * tpl_collect_add() / tpl_collect_done() right away (host) or copies the
 * data to a worker task (device: decoding takes ~100 ms per recording). */
void sat_template_msg(const char *keyword, const char *name, const char *wav_b64, size_t len,
                      int index, int count);
void sat_template_done(int count);
void sat_templates_begin(void);

/* The server accepted the device (welcome): mark a new firmware valid, send
 * the diagnostic log, etc. */
void sat_on_online(void);

/* The wake word model changed (new templates, a learned negative): persist it
 * with tpl_save_current(). The device defers flash writes while audio plays
 * (a flash write stalls the cache, hence the speaker). */
void sat_store_changed(void);

#endif
