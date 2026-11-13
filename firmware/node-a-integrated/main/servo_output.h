#pragma once

#include <stdbool.h>
#include "esp_err.h"

// GPIO10 relay is active HIGH. GPIO4/5 servo signals stay LOW without PWM.
esp_err_t servo_output_init_safe(void);
esp_err_t servo_output_start_home(void);
esp_err_t servo_output_set_us(int pan_us, int tilt_us);
void servo_output_stop(void);
esp_err_t servo_output_set_relay(bool on);
