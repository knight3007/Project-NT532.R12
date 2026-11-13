#include "mq2_sensor.h"

#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "mq2_adc.h"
#include "mq2_config.h"

static portMUX_TYPE s_mq2_lock = portMUX_INITIALIZER_UNLOCKED;
static mq2_state_t s_mq2;

static const mq2_policy_t s_policy = {
    .warmup_ms = MQ2_WARMUP_MS,
    .max_sample_age_ms = MQ2_MAX_SAMPLE_AGE_MS,
    .alarm_threshold_raw = MQ2_ALARM_THRESHOLD_RAW,
    .alarm_hysteresis_raw = MQ2_ALARM_HYSTERESIS_RAW,
    .do_enabled = MQ2_DO_ENABLED,
    .do_active_level = MQ2_DO_ACTIVE_LEVEL,
    .do_debounce_samples = MQ2_DO_DEBOUNCE_SAMPLES,
};

static void mq2_task(void *unused)
{
    (void)unused;
    TickType_t last_wake = xTaskGetTickCount();
    for (;;) {
        int samples[MQ2_ADC_SAMPLES];
        esp_err_t error = ESP_OK;
        for (size_t i = 0; i < MQ2_ADC_SAMPLES; ++i) {
            error = mq2_adc_read(&samples[i]);
            if (error != ESP_OK) break;
            if (i + 1 < MQ2_ADC_SAMPLES) vTaskDelay(pdMS_TO_TICKS(2));
        }
        int raw = 0, mv = 0, do_level = 0;
        bool average_ok = error == ESP_OK && mq2_average_raw(samples, MQ2_ADC_SAMPLES, &raw);
        bool mv_valid = average_ok && mq2_adc_calibrate(raw, &mv) == ESP_OK;
        bool do_valid = average_ok && mq2_adc_read_do(&do_level);
        uint64_t now_ms = (uint64_t)esp_timer_get_time() / 1000;
        portENTER_CRITICAL(&s_mq2_lock);
        if (error != ESP_OK) mq2_logic_fail(&s_mq2, MQ2_ADC_READ_ERROR);
        else if (!average_ok) mq2_logic_fail(&s_mq2, MQ2_ADC_RANGE_ERROR);
        else mq2_logic_record(&s_mq2, raw, mv_valid, mv, do_valid, do_level, now_ms);
        portEXIT_CRITICAL(&s_mq2_lock);
        vTaskDelayUntil(&last_wake, pdMS_TO_TICKS(MQ2_SAMPLE_PERIOD_MS));
    }
}

esp_err_t mq2_sensor_start(void)
{
    uint64_t now_ms = (uint64_t)esp_timer_get_time() / 1000;
    portENTER_CRITICAL(&s_mq2_lock);
    mq2_logic_init(&s_mq2, &s_policy, now_ms);
    portEXIT_CRITICAL(&s_mq2_lock);
    esp_err_t error = mq2_adc_init();
    if (error != ESP_OK) {
        portENTER_CRITICAL(&s_mq2_lock);
        mq2_logic_fail(&s_mq2, MQ2_ADC_INIT_ERROR);
        portEXIT_CRITICAL(&s_mq2_lock);
        return error;
    }
    if (xTaskCreate(mq2_task, "mq2", 3072, NULL, 2, NULL) != pdPASS) {
        mq2_adc_deinit();
        portENTER_CRITICAL(&s_mq2_lock);
        mq2_logic_fail(&s_mq2, MQ2_ADC_INIT_ERROR);
        portEXIT_CRITICAL(&s_mq2_lock);
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

mq2_state_t mq2_sensor_snapshot(void)
{
    portENTER_CRITICAL(&s_mq2_lock);
    mq2_state_t snapshot = s_mq2;
    portEXIT_CRITICAL(&s_mq2_lock);
    return snapshot;
}
