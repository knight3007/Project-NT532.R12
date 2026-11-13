#pragma once
#include "esp_err.h"
typedef void *adc_oneshot_unit_handle_t;
typedef int adc_unit_t;
typedef int adc_channel_t;
typedef int adc_atten_t;
typedef int adc_bitwidth_t;
enum {
    ADC_UNIT_1 = 0, ADC_CHANNEL_0 = 0, ADC_ATTEN_DB_12 = 3,
    ADC_BITWIDTH_12 = 12, ADC_ULP_MODE_DISABLE = 0
};
typedef struct {
    adc_unit_t unit_id;
    int clk_src;
    int ulp_mode;
} adc_oneshot_unit_init_cfg_t;
typedef struct {
    adc_atten_t atten;
    adc_bitwidth_t bitwidth;
} adc_oneshot_chan_cfg_t;
esp_err_t adc_oneshot_io_to_channel(int io_num, adc_unit_t *unit, adc_channel_t *channel);
esp_err_t adc_oneshot_new_unit(const adc_oneshot_unit_init_cfg_t *config,
                               adc_oneshot_unit_handle_t *handle);
esp_err_t adc_oneshot_config_channel(adc_oneshot_unit_handle_t handle, adc_channel_t channel,
                                      const adc_oneshot_chan_cfg_t *config);
esp_err_t adc_oneshot_read(adc_oneshot_unit_handle_t handle, adc_channel_t channel, int *raw);
esp_err_t adc_oneshot_del_unit(adc_oneshot_unit_handle_t handle);
