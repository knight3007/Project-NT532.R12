#pragma once

#include <stdbool.h>
#include "esp_err.h"

esp_err_t mq2_adc_init(void);
void mq2_adc_deinit(void);
esp_err_t mq2_adc_read(int *raw);
esp_err_t mq2_adc_calibrate(int raw, int *mv);
bool mq2_adc_read_do(int *level);
