#include <assert.h>
#include <string.h>

#include "mq2_config.h"
#include "mq2_logic.h"

int main(void)
{
    const mq2_policy_t policy = {
        .warmup_ms = MQ2_WARMUP_MS,
        .max_sample_age_ms = MQ2_MAX_SAMPLE_AGE_MS,
        .alarm_threshold_raw = MQ2_ALARM_THRESHOLD_RAW,
        .alarm_hysteresis_raw = MQ2_ALARM_HYSTERESIS_RAW,
        .do_enabled = MQ2_DO_ENABLED,
        .do_active_level = MQ2_DO_ACTIVE_LEVEL,
        .do_debounce_samples = MQ2_DO_DEBOUNCE_SAMPLES,
    };
    mq2_state_t state;
    char json[256];
    mq2_logic_init(&state, &policy, 100);
    assert(mq2_status_json(&state, 100, json, sizeof(json)));
    assert(strstr(json, "\"sensor_ok\":false,\"warming_up\":true") != NULL);
    assert(strstr(json, "\"adc_raw\":null,\"adc_mv\":null,\"do_level\":null") != NULL);
    assert(strstr(json, "\"alarm\":null,\"sample_age_ms\":null,\"error\":\"not_ready\"") != NULL);

    int samples[] = {1000, 1001, 999, 1000, 1000, 1000, 1000, 1000};
    int average = -1;
    assert(mq2_average_raw(samples, 8, &average) && average == 1000);
    assert(!mq2_average_raw(samples, 0, &average));
    samples[4] = 4096;
    assert(!mq2_average_raw(samples, 8, &average));
    samples[4] = 1000;

    mq2_logic_record(&state, average, false, 0, false, 0, 1100);
    assert(mq2_status_json(&state, 1100, json, sizeof(json)));
    assert(strstr(json, "\"sensor_ok\":true,\"warming_up\":true") != NULL);
    assert(strstr(json, "\"adc_raw\":1000,\"adc_mv\":null,\"do_level\":null") != NULL);
    assert(strstr(json, "\"alarm\":null") != NULL);
    assert(mq2_status_fields(&state, 1100, json, sizeof(json)));
    assert(strstr(json, "\"mq2_sensor_ok\":true,\"mq2_adc_raw\":1000,\"mq2_alarm\":null") != NULL);

    mq2_logic_record(&state, 1520, true, 1210, false, 0, 300100);
    assert(mq2_status_json(&state, 300100, json, sizeof(json)));
    assert(strstr(json, "\"warming_up\":false,\"adc_raw\":1520,\"adc_mv\":1210") != NULL);
    assert(strstr(json, "\"alarm\":null") != NULL);
    assert(mq2_status_json(&state, 303101, json, sizeof(json)));
    assert(strstr(json, "\"sensor_ok\":false") != NULL);
    assert(strstr(json, "\"adc_raw\":null,\"adc_mv\":null") != NULL);
    assert(strstr(json, "\"error\":\"stale\"") != NULL);
    mq2_logic_fail(&state, MQ2_ADC_READ_ERROR);
    assert(mq2_status_json(&state, 300200, json, sizeof(json)));
    assert(strstr(json, "\"error\":\"adc_read\"") != NULL);
    assert(!mq2_status_json(&state, 300200, json, 8));

    mq2_policy_t do_policy = policy;
    do_policy.do_enabled = true;
    do_policy.do_active_level = 0;
    do_policy.do_debounce_samples = 3;
    do_policy.warmup_ms = 0;
    mq2_logic_init(&state, &do_policy, 0);
    mq2_logic_record(&state, 1000, false, 0, true, 0, 1000);
    mq2_logic_record(&state, 1000, false, 0, true, 0, 2000);
    assert(mq2_status_json(&state, 2000, json, sizeof(json)));
    assert(strstr(json, "\"do_level\":null,\"do_active\":null") != NULL);
    mq2_logic_record(&state, 1000, false, 0, true, 0, 3000);
    assert(mq2_status_json(&state, 3000, json, sizeof(json)));
    assert(strstr(json, "\"do_level\":0,\"do_active\":true") != NULL);
    mq2_logic_record(&state, 1000, false, 0, true, 1, 4000);
    assert(mq2_status_json(&state, 4000, json, sizeof(json)));
    assert(strstr(json, "\"do_level\":0,\"do_active\":true") != NULL);
    mq2_logic_fail(&state, MQ2_ADC_READ_ERROR);
    mq2_logic_record(&state, 1000, false, 0, true, 1, 5000);
    assert(mq2_status_json(&state, 5000, json, sizeof(json)));
    assert(strstr(json, "\"do_level\":null,\"do_active\":null") != NULL);

    mq2_policy_t alarm_policy = do_policy;
    alarm_policy.do_enabled = false;
    alarm_policy.alarm_threshold_raw = 1500;
    alarm_policy.alarm_hysteresis_raw = 100;
    mq2_logic_init(&state, &alarm_policy, 0);
    mq2_logic_record(&state, 1499, false, 0, false, 0, 1000);
    assert(mq2_status_json(&state, 1000, json, sizeof(json)));
    assert(strstr(json, "\"alarm\":false") != NULL);
    mq2_logic_record(&state, 1500, false, 0, false, 0, 2000);
    assert(mq2_status_json(&state, 2000, json, sizeof(json)));
    assert(strstr(json, "\"alarm\":true") != NULL);
    mq2_logic_record(&state, 1450, false, 0, false, 0, 3000);
    assert(mq2_status_json(&state, 3000, json, sizeof(json)));
    assert(strstr(json, "\"alarm\":true") != NULL);
    mq2_logic_record(&state, 1399, false, 0, false, 0, 4000);
    assert(mq2_status_json(&state, 4000, json, sizeof(json)));
    assert(strstr(json, "\"alarm\":false") != NULL);
    return 0;
}
