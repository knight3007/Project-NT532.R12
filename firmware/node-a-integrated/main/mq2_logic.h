#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef struct {
    uint32_t warmup_ms;
    uint32_t max_sample_age_ms;
    int alarm_threshold_raw; // -1 disables the uncalibrated alarm.
    int alarm_hysteresis_raw;
    bool do_enabled;
    int do_active_level;
    unsigned do_debounce_samples;
} mq2_policy_t;

typedef enum {
    MQ2_NOT_READY,
    MQ2_NO_ERROR,
    MQ2_ADC_INIT_ERROR,
    MQ2_ADC_READ_ERROR,
    MQ2_ADC_RANGE_ERROR,
} mq2_error_t;

typedef struct {
    mq2_policy_t policy;
    uint64_t started_ms;
    uint64_t sample_ms;
    bool has_sample;
    bool adc_ok;
    int adc_raw;
    bool mv_valid;
    int adc_mv;
    bool do_valid;
    int do_level;
    int do_candidate;
    unsigned do_candidate_count;
    bool alarm_valid;
    bool alarm;
    mq2_error_t error;
} mq2_state_t;

void mq2_logic_init(mq2_state_t *state, const mq2_policy_t *policy, uint64_t now_ms);
bool mq2_average_raw(const int *samples, size_t count, int *average);
void mq2_logic_record(mq2_state_t *state, int raw, bool mv_valid, int mv,
                      bool do_sample_valid, int do_level, uint64_t now_ms);
void mq2_logic_fail(mq2_state_t *state, mq2_error_t error);
bool mq2_status_json(const mq2_state_t *state, uint64_t now_ms, char *body, size_t capacity);
bool mq2_status_fields(const mq2_state_t *state, uint64_t now_ms, char *body, size_t capacity);
