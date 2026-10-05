#pragma once
#include <stdarg.h>
#include <stdio.h>
typedef int (*vprintf_like_t)(const char *, va_list);
vprintf_like_t esp_log_set_vprintf(vprintf_like_t f);
void test_log(const char *fmt, ...);
#define ESP_LOGI(tag, fmt, ...) test_log("I (0) %s: " fmt "\n", tag, ##__VA_ARGS__)
#define ESP_LOGE(tag, fmt, ...) test_log("E (0) %s: " fmt "\n", tag, ##__VA_ARGS__)
