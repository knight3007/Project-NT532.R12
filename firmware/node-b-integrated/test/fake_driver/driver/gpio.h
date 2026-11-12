#pragma once

#include <stdint.h>
#include "esp_err.h"

typedef int gpio_num_t;
enum { GPIO_NUM_4 = 4, GPIO_NUM_5 = 5, GPIO_NUM_10 = 10, GPIO_MODE_OUTPUT = 2,
       GPIO_PULLUP_DISABLE = 0, GPIO_PULLDOWN_ENABLE = 1, GPIO_INTR_DISABLE = 0 };

typedef struct {
    uint64_t pin_bit_mask;
    int mode;
    int pull_up_en;
    int pull_down_en;
    int intr_type;
} gpio_config_t;

esp_err_t gpio_reset_pin(gpio_num_t pin);
esp_err_t gpio_set_level(gpio_num_t pin, int level);
esp_err_t gpio_config(const gpio_config_t *config);
