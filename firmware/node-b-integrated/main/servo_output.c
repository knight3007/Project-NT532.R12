#include "servo_output.h"

#include <stdbool.h>
#include <stdint.h>

#include "driver/gpio.h"
#include "driver/ledc.h"
#include "esp_log.h"
#include "esp_rom_gpio.h"
#include "soc/gpio_sig_map.h"

enum { SERVO_HZ = 50, SERVO_PERIOD_US = 20000, SERVO_RES_BITS = 14 };
static bool s_started;
static bool s_pan_configured;
static bool s_tilt_configured;
static const char *TAG = "servo_output";

// ESP32-H2 LEDC supports low-speed mode. The existing sensor-h2 HAL also uses
// 50 Hz / 14 bit; no channel is configured until an explicit HOME command.
// https://docs.espressif.com/projects/esp-idf/en/v5.1/esp32h2/api-reference/peripherals/ledc.html
static uint32_t pulse_to_duty(int us)
{
    return ((uint32_t)us * (1U << SERVO_RES_BITS) + SERVO_PERIOD_US / 2) /
           SERVO_PERIOD_US;
}

static esp_err_t drive_low(gpio_num_t pin)
{
    // Preload LOW, then keep output enabled while handing the pin from LEDC to GPIO.
    // gpio_reset_pin() would briefly disable output and enable a pull-up.
    esp_err_t error = gpio_set_level(pin, 0);
    if (error != ESP_OK) return error;
    gpio_config_t config = {
        .pin_bit_mask = 1ULL << pin,
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_ENABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    error = gpio_config(&config);
    if (error == ESP_OK) esp_rom_gpio_connect_out_signal(pin, SIG_GPIO_OUT_IDX, false, false);
    return error;
}

esp_err_t servo_output_init_safe(void)
{
    esp_err_t error = drive_low(GPIO_NUM_10); // Relay OFF; external pulldown is still required during reset.
    if (error == ESP_OK) error = drive_low(GPIO_NUM_4);
    if (error == ESP_OK) error = drive_low(GPIO_NUM_5);
    return error;
}

esp_err_t servo_output_set_relay(bool on)
{
    return gpio_set_level(GPIO_NUM_10, on ? 1 : 0);
}

void servo_output_stop(void)
{
    esp_err_t pan_error = s_pan_configured ? ledc_stop(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0, 0) : ESP_OK;
    esp_err_t tilt_error = s_tilt_configured ? ledc_stop(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_1, 0) : ESP_OK;
    s_started = false;
    s_pan_configured = false;
    s_tilt_configured = false;
    // Also disconnect LEDC from the pins, including after a partial init failure.
    esp_err_t pan_gpio_error = drive_low(GPIO_NUM_4);
    esp_err_t tilt_gpio_error = drive_low(GPIO_NUM_5);
    if (pan_error != ESP_OK || tilt_error != ESP_OK ||
        pan_gpio_error != ESP_OK || tilt_gpio_error != ESP_OK) {
        ESP_LOGE(TAG, "STOP output error: LEDC pan=%s tilt=%s GPIO pan=%s tilt=%s",
                 esp_err_to_name(pan_error), esp_err_to_name(tilt_error),
                 esp_err_to_name(pan_gpio_error), esp_err_to_name(tilt_gpio_error));
    }
}

esp_err_t servo_output_set_us(int pan_us, int tilt_us)
{
    if (!s_started || pan_us < 500 || pan_us > 2500 || tilt_us < 600 || tilt_us > 2500)
        return ESP_ERR_INVALID_ARG;
    // Only the servo task writes LEDC; these APIs do not require the fade service.
    esp_err_t error = ledc_set_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0,
                                    pulse_to_duty(pan_us));
    if (error == ESP_OK) error = ledc_update_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0);
    if (error == ESP_OK)
        error = ledc_set_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_1,
                              pulse_to_duty(tilt_us));
    if (error == ESP_OK) error = ledc_update_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_1);
    if (error != ESP_OK) servo_output_stop();
    return error;
}

esp_err_t servo_output_start_home(void)
{
    if (s_started) return ESP_ERR_INVALID_STATE;
    ledc_timer_config_t timer = {
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .duty_resolution = LEDC_TIMER_14_BIT,
        .timer_num = LEDC_TIMER_0,
        .freq_hz = SERVO_HZ,
        .clk_cfg = LEDC_AUTO_CLK,
    };
    esp_err_t error = ledc_timer_config(&timer);
    if (error != ESP_OK) return error;
    const gpio_num_t pins[] = {GPIO_NUM_4, GPIO_NUM_5};
    for (int i = 0; i < 2; ++i) {
        ledc_channel_config_t channel = {
            .gpio_num = pins[i],
            .speed_mode = LEDC_LOW_SPEED_MODE,
            .channel = (ledc_channel_t)(LEDC_CHANNEL_0 + i),
            .timer_sel = LEDC_TIMER_0,
            .intr_type = LEDC_INTR_DISABLE,
            .duty = 0,
            .hpoint = 0,
        };
        error = ledc_channel_config(&channel);
        if (error != ESP_OK) {
            servo_output_stop();
            return error;
        }
        if (i == 0) s_pan_configured = true;
        else s_tilt_configured = true;
    }
    s_started = true;
    error = servo_output_set_us(1500, 1500);
    if (error != ESP_OK) servo_output_stop();
    return error;
}
