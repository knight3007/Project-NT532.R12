#pragma once

#include "dht11_logic.h"
#include "esp_err.h"

esp_err_t dht11_sensor_start(void);
dht11_sample_t dht11_sensor_snapshot(void);
