#pragma once

#include "esp_err.h"

#define ESP_LOGE(tag, format, ...) ((void)(tag), (void)(format))

static inline const char *esp_err_to_name(esp_err_t error)
{
    return error == ESP_OK ? "ESP_OK" : "ESP_FAIL";
}
