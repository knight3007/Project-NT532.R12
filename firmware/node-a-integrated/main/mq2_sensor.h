#pragma once

#include "esp_err.h"
#include "mq2_logic.h"

esp_err_t mq2_sensor_start(void);
mq2_state_t mq2_sensor_snapshot(void);
