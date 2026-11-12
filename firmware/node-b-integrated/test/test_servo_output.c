#include <assert.h>
#include <stdint.h>

#include "driver/gpio.h"
#include "driver/ledc.h"
#include "soc/gpio_sig_map.h"
#include "integrated_control.h"
#include "servo_output.h"

static int pin_level[11];
static uint32_t pending_duty[2];
static uint32_t duty[2];
static int configured_channels;
static int fail_channel = -1;
static int fail_update_channel = -1;
static int fade_api_calls;
static int reset_after_pwm;
static int pwm_seen;
static int gpio_route[11];
static int relay_high_calls;

esp_err_t gpio_reset_pin(gpio_num_t pin)
{
    // ESP-IDF v5.4.4 resets output pins to disabled with pull-up enabled.
    if (pwm_seen && (pin == GPIO_NUM_4 || pin == GPIO_NUM_5)) ++reset_after_pwm;
    pin_level[pin] = 0;
    return ESP_OK;
}
esp_err_t gpio_set_level(gpio_num_t pin, int level)
{
    pin_level[pin] = level;
    if (pin == GPIO_NUM_10 && level == 1) ++relay_high_calls;
    return ESP_OK;
}
esp_err_t gpio_config(const gpio_config_t *config)
{
    assert(config->mode == GPIO_MODE_OUTPUT);
    assert(config->pull_up_en == GPIO_PULLUP_DISABLE);
    assert(config->pull_down_en == GPIO_PULLDOWN_ENABLE);
    return ESP_OK;
}
void esp_rom_gpio_connect_out_signal(uint32_t pin, uint32_t signal, bool invert, bool oen_invert)
{
    assert(signal == SIG_GPIO_OUT_IDX && !invert && !oen_invert);
    gpio_route[pin] = 1;
}
esp_err_t ledc_timer_config(const ledc_timer_config_t *config)
{
    assert(config->freq_hz == 50 && config->duty_resolution == 14);
    return ESP_OK;
}
esp_err_t ledc_channel_config(const ledc_channel_config_t *config)
{
    if (config->channel == fail_channel) return ESP_FAIL;
    ++configured_channels;
    gpio_route[config->gpio_num] = 0; // LEDC owns the pin until STOP hands it to GPIO.
    return ESP_OK;
}
esp_err_t ledc_set_duty_and_update(ledc_mode_t mode, ledc_channel_t channel,
                                   uint32_t value, uint32_t hpoint)
{
    (void)mode; (void)channel; (void)value; (void)hpoint;
    ++fade_api_calls;
    return ESP_FAIL; // ESP-IDF v5.4.4: requires ledc_fade_func_install().
}
esp_err_t ledc_set_duty(ledc_mode_t mode, ledc_channel_t channel, uint32_t value)
{
    (void)mode;
    pending_duty[channel] = value;
    return ESP_OK;
}
esp_err_t ledc_update_duty(ledc_mode_t mode, ledc_channel_t channel)
{
    (void)mode;
    if (channel == fail_update_channel) return ESP_FAIL;
    duty[channel] = pending_duty[channel];
    if (duty[channel] != 0) pwm_seen = 1;
    return ESP_OK;
}
esp_err_t ledc_stop(ledc_mode_t mode, ledc_channel_t channel, uint32_t idle_level)
{
    (void)mode;
    assert(idle_level == 0);
    pending_duty[channel] = 0;
    duty[channel] = 0;
    return ESP_OK;
}

int main(void)
{
    assert(servo_output_init_safe() == ESP_OK);
    assert(pin_level[4] == 0 && pin_level[5] == 0 && pin_level[10] == 0);
    assert(servo_output_start_home() == ESP_OK);
    assert(configured_channels == 2 && fade_api_calls == 0);
    assert(duty[0] == 1229 && duty[1] == 1229); // 1500 us at 50 Hz / 14 bit.
    assert(servo_output_set_us(1300, 1700) == ESP_OK);
    assert(duty[0] == 1065 && duty[1] == 1393);
    servo_output_stop();
    assert(reset_after_pwm == 0); // STOP must not enable GPIO pull-up after PWM.
    assert(duty[0] == 0 && duty[1] == 0);
    assert(gpio_route[4] == 1 && gpio_route[5] == 1);
    assert(pin_level[4] == 0 && pin_level[5] == 0 && pin_level[10] == 0);
    fail_channel = 1;
    assert(servo_output_start_home() == ESP_FAIL);
    assert(duty[0] == 0 && duty[1] == 0);
    assert(pin_level[4] == 0 && pin_level[5] == 0);
    fail_channel = -1;
    fail_update_channel = 1;
    assert(servo_output_start_home() == ESP_FAIL);
    assert(duty[0] == 0 && duty[1] == 0);
    assert(pin_level[4] == 0 && pin_level[5] == 0);

    fail_update_channel = -1;
    control_state_t control;
    control_init(&control);
    control_set_attached(&control, true);
    assert(control_laser_request(&control, "ON", 2, 100) == CONTROL_FORBIDDEN);
    assert(relay_high_calls == 0 && pin_level[10] == 0);
    control_heartbeat(&control, 100);
    assert(control_laser_request(&control, "ON", 2, 100) == CONTROL_FORBIDDEN);
    assert(control_pantilt(&control, "ARM", 3, 100) == CONTROL_OK);
    assert(control_laser_request(&control, "ON", 2, 100) == CONTROL_FORBIDDEN);
    for (uint64_t now = 120; now <= 200; now += 20) {
        assert(!control_watchdog(&control, now));
        assert(control.armed && !control.pwm_active);
    }
    assert(control_pantilt(&control, "HOME", 4, 200) == CONTROL_OK);
    assert(servo_output_start_home() == ESP_OK);
    assert(control_pwm_started(&control) && control.pwm_active);
    assert(duty[0] == 1229 && duty[1] == 1229);
    assert(control_laser_request(&control, "ON!", 3, 200) == CONTROL_BAD_REQUEST);
    assert(relay_high_calls == 0 && pin_level[10] == 0);
    assert(control_laser_request(&control, "ON", 2, 200) == CONTROL_OK);
    assert(servo_output_set_relay(control.laser_on) == ESP_OK);
    assert(pin_level[10] == 1 && relay_high_calls == 1);
    assert(control_laser_request(&control, "OFF", 3, 200) == CONTROL_OK);
    assert(servo_output_set_relay(control.laser_on) == ESP_OK);
    assert(pin_level[10] == 0);
    assert(control_laser_request(&control, "ON", 2, 200) == CONTROL_OK);
    assert(servo_output_set_relay(control.laser_on) == ESP_OK);
    assert(pin_level[10] == 1);

    control_stop(&control);
    assert(servo_output_set_relay(control.laser_on) == ESP_OK);
    servo_output_stop();
    assert(!control.pwm_active && duty[0] == 0 && duty[1] == 0 && pin_level[10] == 0);
    assert(gpio_route[4] == 1 && gpio_route[5] == 1);
    control_heartbeat(&control, 300);
    assert(control_pantilt(&control, "HOME", 4, 300) == CONTROL_FORBIDDEN);
    assert(control_pantilt(&control, "ARM", 3, 300) == CONTROL_OK);
    assert(control_pantilt(&control, "HOME", 4, 300) == CONTROL_OK);
    assert(servo_output_start_home() == ESP_OK);
    assert(control_pwm_started(&control) && control.pwm_active);
    assert(control_laser_request(&control, "ON", 2, 300) == CONTROL_OK);
    assert(servo_output_set_relay(control.laser_on) == ESP_OK);
    control.pwm_active = false; // Exercise the watchdog with only the relay still ON.
    assert(control_watchdog(&control, 1801));
    assert(servo_output_set_relay(control.laser_on) == ESP_OK);
    servo_output_stop();
    assert(!control.armed && !control.pwm_active);
    assert(duty[0] == 0 && duty[1] == 0 && pin_level[10] == 0);
    assert(gpio_route[4] == 1 && gpio_route[5] == 1);
    assert(control_pantilt(&control, "HOME", 4, 1801) == CONTROL_FORBIDDEN);
    control_heartbeat(&control, 2000);
    assert(control_pantilt(&control, "ARM", 3, 2000) == CONTROL_OK);
    assert(control_pantilt(&control, "HOME", 4, 2000) == CONTROL_OK);
    assert(servo_output_start_home() == ESP_OK);
    assert(control_pwm_started(&control));
    assert(control_laser_request(&control, "ON", 2, 2000) == CONTROL_OK);
    assert(servo_output_set_relay(control.laser_on) == ESP_OK);
    control_set_attached(&control, false);
    assert(servo_output_set_relay(control.laser_on) == ESP_OK);
    servo_output_stop();
    assert(pin_level[10] == 0 && duty[0] == 0 && duty[1] == 0);
    assert(control_laser_request(&control, "OFF", 3, 2001) == CONTROL_OK);
    assert(servo_output_set_relay(control.laser_on) == ESP_OK);

    assert(servo_output_set_relay(true) == ESP_OK);
    control_init(&control);
    assert(servo_output_init_safe() == ESP_OK);
    assert(!control.laser_on && pin_level[10] == 0);
    return 0;
}
