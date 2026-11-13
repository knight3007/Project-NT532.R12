#pragma once
#include <stdint.h>
#include "esp_err.h"
typedef int gpio_num_t;
enum {
    GPIO_NUM_12 = 12, GPIO_MODE_INPUT = 1, GPIO_PULLUP_DISABLE = 0,
    GPIO_PULLDOWN_DISABLE = 0, GPIO_INTR_DISABLE = 0
};
typedef struct {
    uint64_t pin_bit_mask;
    int mode;
    int pull_up_en;
    int pull_down_en;
    int intr_type;
} gpio_config_t;
esp_err_t gpio_config(const gpio_config_t *config);
int gpio_get_level(gpio_num_t pin);
