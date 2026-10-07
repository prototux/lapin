#pragma once
/* the task types main/mem.h declares task_create_psram() with */
#include <stdint.h>
typedef int BaseType_t;
typedef unsigned int UBaseType_t;
typedef void (*TaskFunction_t)(void *);
typedef void *TaskHandle_t;
