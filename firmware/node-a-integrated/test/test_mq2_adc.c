#include <assert.h>
#include <stdbool.h>

#include "esp_adc/adc_oneshot.h"
#include "esp_adc/adc_cali_scheme.h"
#include "driver/gpio.h"
#include "mq2_adc.h"

static int fail_mapping, fail_new, fail_config, fail_calibration, fail_read;
static int deleted_units, deleted_calibrations, gpio_configs, reads;

esp_err_t adc_oneshot_io_to_channel(int io_num, adc_unit_t *unit, adc_channel_t *channel)
{
    assert(io_num == 1);
    if (fail_mapping) return ESP_FAIL;
    *unit = ADC_UNIT_1;
    *channel = ADC_CHANNEL_0;
    return ESP_OK;
}
esp_err_t adc_oneshot_new_unit(const adc_oneshot_unit_init_cfg_t *config,
                               adc_oneshot_unit_handle_t *handle)
{
    assert(config->unit_id == ADC_UNIT_1 && config->ulp_mode == ADC_ULP_MODE_DISABLE);
    if (fail_new) return ESP_FAIL;
    *handle = (void *)1;
    return ESP_OK;
}
esp_err_t adc_oneshot_config_channel(adc_oneshot_unit_handle_t handle, adc_channel_t channel,
                                      const adc_oneshot_chan_cfg_t *config)
{
    assert(handle == (void *)1 && channel == ADC_CHANNEL_0);
    assert(config->atten == ADC_ATTEN_DB_12 && config->bitwidth == ADC_BITWIDTH_12);
    return fail_config ? ESP_FAIL : ESP_OK;
}
esp_err_t adc_oneshot_read(adc_oneshot_unit_handle_t handle, adc_channel_t channel, int *raw)
{
    assert(handle == (void *)1 && channel == ADC_CHANNEL_0);
    ++reads;
    if (fail_read) return ESP_FAIL;
    *raw = 1520;
    return ESP_OK;
}
esp_err_t adc_oneshot_del_unit(adc_oneshot_unit_handle_t handle)
{
    assert(handle == (void *)1);
    ++deleted_units;
    return ESP_OK;
}
esp_err_t adc_cali_create_scheme_curve_fitting(const adc_cali_curve_fitting_config_t *config,
                                                adc_cali_handle_t *handle)
{
    assert(config->unit_id == ADC_UNIT_1 && config->chan == ADC_CHANNEL_0);
    assert(config->atten == ADC_ATTEN_DB_12 && config->bitwidth == ADC_BITWIDTH_12);
    if (fail_calibration) return ESP_ERR_NOT_SUPPORTED;
    *handle = (void *)2;
    return ESP_OK;
}
esp_err_t adc_cali_raw_to_voltage(adc_cali_handle_t handle, int raw, int *mv)
{
    assert(handle == (void *)2 && raw == 1520);
    *mv = 1210; // Host fake only; not a measurement or MQ-2 calibration.
    return ESP_OK;
}
esp_err_t adc_cali_delete_scheme_curve_fitting(adc_cali_handle_t handle)
{
    assert(handle == (void *)2);
    ++deleted_calibrations;
    return ESP_OK;
}
esp_err_t gpio_config(const gpio_config_t *config)
{
    (void)config;
    ++gpio_configs;
    return ESP_OK;
}
int gpio_get_level(gpio_num_t pin)
{
    assert(pin == GPIO_NUM_12);
    return 0;
}

int main(void)
{
    int raw = -1, mv = -1, level = -1;
    assert(mq2_adc_read(&raw) == ESP_ERR_INVALID_STATE);
    fail_mapping = 1;
    assert(mq2_adc_init() == ESP_FAIL);
    fail_mapping = 0;
    fail_new = 1;
    assert(mq2_adc_init() == ESP_FAIL);
    fail_new = 0;
    fail_config = 1;
    assert(mq2_adc_init() == ESP_FAIL && deleted_units == 1);
    fail_config = 0;
    fail_calibration = 1;
    assert(mq2_adc_init() == ESP_OK);
    assert(gpio_configs == 0); // DO is disabled until wiring/polarity are verified.
    assert(mq2_adc_init() == ESP_ERR_INVALID_STATE);
    assert(mq2_adc_read(&raw) == ESP_OK && raw == 1520);
    assert(mq2_adc_calibrate(raw, &mv) == ESP_ERR_NOT_SUPPORTED);
    assert(!mq2_adc_read_do(&level));
    fail_read = 1;
    assert(mq2_adc_read(&raw) == ESP_FAIL && reads == 2);
    mq2_adc_deinit();
    assert(deleted_units == 2 && deleted_calibrations == 0);
    fail_read = 0;
    fail_calibration = 0;
    assert(mq2_adc_init() == ESP_OK);
    assert(mq2_adc_read(&raw) == ESP_OK);
    assert(mq2_adc_calibrate(raw, &mv) == ESP_OK && mv == 1210);
    mq2_adc_deinit();
    assert(deleted_units == 3 && deleted_calibrations == 1);
    return 0;
}
