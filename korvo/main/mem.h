/*
 * Memory allocation for the ESP32. Every buffer here is accessed byte- or
 * halfword-wise, so it must be byte-addressable: on the original ESP32,
 * MALLOC_CAP_INTERNAL alone may return the IRAM heap (0x4008_0000..), where
 * only 32-bit accesses work (an int16 load there is a LoadStoreError panic).
 * All helpers ask for MALLOC_CAP_8BIT and verify the returned pointer.
 */
#pragma once

#include <stddef.h>

void *mem_alloc_internal(size_t n);     /* internal, byte-addressable (not zeroed) */
void *mem_calloc_internal(size_t n);
void *mem_alloc_big(size_t n);          /* PSRAM if present, else internal */
void *mem_calloc_big(size_t n);
void *mem_realloc_big(void *p, size_t n);
void *mem_calloc_fast(size_t n);        /* internal if > 48 KB stays free, else big */
int mem_has_psram(void);
void *mem_calloc_hot(size_t n);         /* read every audio hop: internal while 32 KB stay free */
void mem_hot_stats(size_t *internal, size_t *psram);

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
/* A task whose stack is in PSRAM: only for tasks that never write flash. */
BaseType_t task_create_psram(TaskFunction_t fn, const char *name, uint32_t stack, void *arg, UBaseType_t prio,
                             TaskHandle_t *handle, BaseType_t core);
