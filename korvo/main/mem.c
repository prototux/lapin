#include "mem.h"

#include <string.h>

#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_memory_utils.h"

static const char *TAG = "mem";

/* last line of defence: never hand out memory that faults on byte access */
static void *check(void *p, size_t n, const char *what)
{
    if (p && !esp_ptr_byte_accessible(p)) {
        ESP_LOGE(TAG, "%s: %u bytes at %p are not byte-addressable: discarded", what, (unsigned)n, p);
        heap_caps_free(p);
        return NULL;
    }
    return p;
}

void *mem_alloc_internal(size_t n)
{
    return check(heap_caps_malloc(n, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT), n, "internal");
}

void *mem_calloc_internal(size_t n)
{
    return check(heap_caps_calloc(1, n, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT), n, "internal");
}

int mem_has_psram(void) { return heap_caps_get_total_size(MALLOC_CAP_SPIRAM) > 0; }

void *mem_alloc_big(size_t n)
{
    void *p = check(heap_caps_malloc(n, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT), n, "psram");
    return p ? p : mem_alloc_internal(n);
}

void *mem_calloc_big(size_t n)
{
    void *p = check(heap_caps_calloc(1, n, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT), n, "psram");
    return p ? p : mem_calloc_internal(n);
}

void *mem_realloc_big(void *old, size_t n)
{
    void *p = heap_caps_realloc(old, n, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!p)
        p = heap_caps_realloc(old, n, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    if (p && !esp_ptr_byte_accessible(p)) {
        ESP_LOGE(TAG, "realloc returned non byte-addressable memory");
        heap_caps_free(p);
        return NULL;
    }
    return p;
}

void *mem_calloc_fast(size_t n)
{
    /* keep ~96 KB of internal RAM for Wi-Fi / lwIP / the WebSocket (48 KB
     * was not enough: the free minimum fell to ~1 KB during a template download) */
    if (heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT) > n + 96 * 1024) {
        void *p = mem_calloc_internal(n);
        if (p)
            return p;
    }
    return mem_calloc_big(n);
}

/* Linked in place of heap_caps_check_integrity_all() (-Wl,--wrap, see
 * CMakeLists.txt): prebuilt ESP-SR code calls it in its audio path, and a
 * full heap walk every 32 ms starves a core. */
bool __wrap_heap_caps_check_integrity_all(bool print_errors)
{
    (void)print_errors;
    return true;
}


/* Wake word features and DTW state (~21 KB, read every 16 ms): from PSRAM
 * they cost ~3 ms of cache refills per hop. Internal RAM as long as 32 KB
 * stay free for Wi-Fi / lwIP. */
static size_t hot_internal, hot_psram;
void *mem_calloc_hot(size_t n)
{
    if (heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT) > n + 32 * 1024 &&
        heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT) > n) {
        void *p = mem_calloc_internal(n);
        if (p) {
            hot_internal += n;
            return p;
        }
    }
    hot_psram += n;
    return mem_calloc_big(n);
}
void mem_hot_stats(size_t *internal, size_t *psram)
{
    *internal = hot_internal;
    *psram = hot_psram;
}

/* Tasks that never write flash (so never run while the cache is off) can keep
 * their stack in PSRAM: ~30 KB of internal RAM back for Wi-Fi and the wake
 * word. Falls back to an internal stack. */
#include "freertos/FreeRTOS.h"
#include "freertos/idf_additions.h"
#include "freertos/task.h"
BaseType_t task_create_psram(TaskFunction_t fn, const char *name, uint32_t stack, void *arg, UBaseType_t prio,
                             TaskHandle_t *handle, BaseType_t core)
{
    if (mem_has_psram() && xTaskCreatePinnedToCoreWithCaps(fn, name, stack, arg, prio, handle, core,
                                                            MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT) == pdPASS)
        return pdPASS;
    ESP_LOGW(TAG, "%s: stack in internal RAM", name);
    return xTaskCreatePinnedToCore(fn, name, stack, arg, prio, handle, core);
}
