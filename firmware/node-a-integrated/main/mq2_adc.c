#include "mq2_adc.h"

#include "driver/gpio.h"
#include "esp_adc/adc_cali.h"
#include "esp_adc/adc_cali_scheme.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_log.h"
#include "mq2_config.h"

static const char *TAG = "mq2_adc";
static adc_oneshot_unit_handle_t s_unit;
static adc_cali_handle_t s_calibration;
static adc_channel_t s_channel;

// GPIO1 -> ADC1_CH0, verified against ESP-IDF v5.4 ESP32-H2 GPIO/ADC docs.
// https://docs.espressif.com/projects/esp-idf/en/v5.4/esp32h2/api-reference/peripherals/gpio.html
// https://docs.espressif.com/projects/esp-idf/en/v5.4/esp32h2/api-reference/peripherals/adc_oneshot.html
esp_err_t mq2_adc_init(void)
{
    if (s_unit != NULL) return ESP_ERR_INVALID_STATE;
    adc_unit_t unit;
    adc_channel_t channel;
    esp_err_t error = adc_oneshot_io_to_channel(MQ2_AO_GPIO, &unit, &channel);
    if (error != ESP_OK) return error;
    if (unit != ADC_UNIT_1 || channel != ADC_CHANNEL_0) return ESP_ERR_NOT_SUPPORTED;

    adc_oneshot_unit_init_cfg_t unit_config = {
        .unit_id = unit,
        .ulp_mode = ADC_ULP_MODE_DISABLE,
    };
    error = adc_oneshot_new_unit(&unit_config, &s_unit);
    if (error != ESP_OK) return error;
    s_channel = channel;
    adc_oneshot_chan_cfg_t channel_config = {
        .atten = MQ2_ADC_ATTEN,
        .bitwidth = MQ2_ADC_BITWIDTH,
    };
    error = adc_oneshot_config_channel(s_unit, channel, &channel_config);
    if (error != ESP_OK) {
        mq2_adc_deinit();
        return error;
    }
    if (MQ2_DO_ENABLED) {
        gpio_config_t do_config = {
            .pin_bit_mask = 1ULL << MQ2_DO_GPIO,
            .mode = GPIO_MODE_INPUT,
            .pull_up_en = GPIO_PULLUP_DISABLE,
            .pull_down_en = GPIO_PULLDOWN_DISABLE,
            .intr_type = GPIO_INTR_DISABLE,
        };
        error = gpio_config(&do_config);
        if (error != ESP_OK) {
            mq2_adc_deinit();
            return error;
        }
    }

    // Curve fitting provides GPIO voltage in mV. Raw ADC still works if
    // eFuse calibration is unavailable; no guessed raw-to-mV formula is used.
    // https://docs.espressif.com/projects/esp-idf/en/v5.4/esp32h2/api-reference/peripherals/adc_calibration.html
    adc_cali_curve_fitting_config_t cal_config = {
        .unit_id = unit,
        .chan = channel,
        .atten = MQ2_ADC_ATTEN,
        .bitwidth = MQ2_ADC_BITWIDTH,
    };
    error = adc_cali_create_scheme_curve_fitting(&cal_config, &s_calibration);
    if (error != ESP_OK) {
        s_calibration = NULL;
        ESP_LOGW(TAG, "ADC mV calibration unavailable (%d); reporting raw only", error);
    }
    return ESP_OK;
}

void mq2_adc_deinit(void)
{
    if (s_calibration != NULL) {
        (void)adc_cali_delete_scheme_curve_fitting(s_calibration);
        s_calibration = NULL;
    }
    if (s_unit != NULL) {
        (void)adc_oneshot_del_unit(s_unit);
        s_unit = NULL;
    }
}

esp_err_t mq2_adc_read(int *raw)
{
    if (s_unit == NULL) return ESP_ERR_INVALID_STATE;
    if (raw == NULL) return ESP_ERR_INVALID_ARG;
    return adc_oneshot_read(s_unit, s_channel, raw);
}

esp_err_t mq2_adc_calibrate(int raw, int *mv)
{
    if (s_calibration == NULL) return ESP_ERR_NOT_SUPPORTED;
    if (mv == NULL) return ESP_ERR_INVALID_ARG;
    return adc_cali_raw_to_voltage(s_calibration, raw, mv);
}

bool mq2_adc_read_do(int *level)
{
    if (!MQ2_DO_ENABLED || level == NULL) return false;
    *level = gpio_get_level((gpio_num_t)MQ2_DO_GPIO);
    return *level == 0 || *level == 1;
}
