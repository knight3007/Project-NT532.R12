#pragma once

#include <stdint.h>
#include "driver/gpio.h"

typedef int ledc_mode_t;
typedef int ledc_channel_t;
enum { LEDC_LOW_SPEED_MODE = 0, LEDC_CHANNEL_0 = 0, LEDC_CHANNEL_1 = 1,
       LEDC_TIMER_0 = 0, LEDC_TIMER_14_BIT = 14, LEDC_AUTO_CLK = 0,
       LEDC_INTR_DISABLE = 0 };

typedef struct {
    ledc_mode_t speed_mode;
    int duty_resolution;
    int timer_num;
    int freq_hz;
    int clk_cfg;
} ledc_timer_config_t;

typedef struct {
    gpio_num_t gpio_num;
    ledc_mode_t speed_mode;
    ledc_channel_t channel;
    int timer_sel;
    int intr_type;
    uint32_t duty;
    uint32_t hpoint;
} ledc_channel_config_t;

esp_err_t ledc_timer_config(const ledc_timer_config_t *config);
esp_err_t ledc_channel_config(const ledc_channel_config_t *config);
esp_err_t ledc_set_duty_and_update(ledc_mode_t mode, ledc_channel_t channel,
                                   uint32_t duty, uint32_t hpoint);
esp_err_t ledc_set_duty(ledc_mode_t mode, ledc_channel_t channel, uint32_t duty);
esp_err_t ledc_update_duty(ledc_mode_t mode, ledc_channel_t channel);
esp_err_t ledc_stop(ledc_mode_t mode, ledc_channel_t channel, uint32_t idle_level);
