#include "mq2_logic.h"

#include <inttypes.h>
#include <stdio.h>
#include <string.h>

enum { MQ2_RAW_MAX = 4095 };

void mq2_logic_init(mq2_state_t *state, const mq2_policy_t *policy, uint64_t now_ms)
{
    memset(state, 0, sizeof(*state));
    state->policy = *policy;
    state->started_ms = now_ms;
    state->error = MQ2_NOT_READY;
}

bool mq2_average_raw(const int *samples, size_t count, int *average)
{
    if (samples == NULL || average == NULL || count == 0) return false;
    int64_t sum = 0;
    for (size_t i = 0; i < count; ++i) {
        if (samples[i] < 0 || samples[i] > MQ2_RAW_MAX) return false;
        sum += samples[i];
    }
    *average = (int)((sum + (int64_t)count / 2) / (int64_t)count);
    return true;
}

void mq2_logic_fail(mq2_state_t *state, mq2_error_t error)
{
    state->adc_ok = false;
    state->mv_valid = false;
    state->do_valid = false;
    state->do_candidate_count = 0;
    state->alarm_valid = false;
    state->error = error;
}

void mq2_logic_record(mq2_state_t *state, int raw, bool mv_valid, int mv,
                      bool do_sample_valid, int do_level, uint64_t now_ms)
{
    if (raw < 0 || raw > MQ2_RAW_MAX) {
        mq2_logic_fail(state, MQ2_ADC_RANGE_ERROR);
        return;
    }
    state->adc_ok = true;
    state->has_sample = true;
    state->sample_ms = now_ms;
    state->adc_raw = raw;
    state->mv_valid = mv_valid && mv >= 0;
    state->adc_mv = mv;
    state->error = MQ2_NO_ERROR;

    bool warming_up = now_ms < state->started_ms ||
                      now_ms - state->started_ms < state->policy.warmup_ms;
    if (state->policy.alarm_threshold_raw >= 0 && !warming_up) {
        int off_threshold = state->policy.alarm_threshold_raw - state->policy.alarm_hysteresis_raw;
        state->alarm = state->alarm_valid && state->alarm
                           ? raw >= off_threshold : raw >= state->policy.alarm_threshold_raw;
        state->alarm_valid = true;
    } else {
        state->alarm_valid = false;
    }

    if (!state->policy.do_enabled || !do_sample_valid || (do_level != 0 && do_level != 1)) {
        state->do_valid = false;
        state->do_candidate_count = 0;
        return;
    }
    if (do_level == state->do_candidate) {
        if (state->do_candidate_count < state->policy.do_debounce_samples)
            ++state->do_candidate_count;
    } else {
        state->do_candidate = do_level;
        state->do_candidate_count = 1;
    }
    if (state->do_candidate_count >= state->policy.do_debounce_samples) {
        state->do_level = do_level;
        state->do_valid = true;
    }
}

static const char *error_name(mq2_error_t error)
{
    switch (error) {
    case MQ2_NO_ERROR: return "none";
    case MQ2_ADC_INIT_ERROR: return "adc_init";
    case MQ2_ADC_READ_ERROR: return "adc_read";
    case MQ2_ADC_RANGE_ERROR: return "adc_range";
    default: return "not_ready";
    }
}

typedef struct {
    bool sensor_ok;
    bool warming_up;
    bool do_valid;
    bool alarm_valid;
    uint64_t sample_age_ms;
    const char *error;
} mq2_view_t;

static mq2_view_t view_at(const mq2_state_t *state, uint64_t now_ms)
{
    mq2_view_t view = {0};
    view.warming_up = now_ms < state->started_ms ||
                      now_ms - state->started_ms < state->policy.warmup_ms;
    bool has_age = state->has_sample && now_ms >= state->sample_ms;
    if (has_age) view.sample_age_ms = now_ms - state->sample_ms;
    bool fresh = has_age && view.sample_age_ms <= state->policy.max_sample_age_ms;
    view.sensor_ok = state->adc_ok && fresh;
    view.do_valid = view.sensor_ok && state->do_valid && state->policy.do_enabled;
    view.alarm_valid = view.sensor_ok && !view.warming_up && state->alarm_valid;
    view.error = !state->adc_ok ? error_name(state->error) : fresh ? "none" : "stale";
    return view;
}

bool mq2_status_json(const mq2_state_t *state, uint64_t now_ms, char *body, size_t capacity)
{
    mq2_view_t view = view_at(state, now_ms);
    char raw[16] = "null", mv[16] = "null", level[8] = "null", age[24] = "null";
    if (view.sensor_ok) {
        snprintf(raw, sizeof(raw), "%d", state->adc_raw);
        if (state->mv_valid) snprintf(mv, sizeof(mv), "%d", state->adc_mv);
    }
    if (view.do_valid) snprintf(level, sizeof(level), "%d", state->do_level);
    if (state->has_sample && now_ms >= state->sample_ms)
        snprintf(age, sizeof(age), "%" PRIu64, view.sample_age_ms);
    const char *active = !view.do_valid ? "null"
                           : state->do_level == state->policy.do_active_level ? "true" : "false";
    const char *alarm = !view.alarm_valid ? "null" : state->alarm ? "true" : "false";
    int written = snprintf(body, capacity,
        "{\"sensor\":\"mq2\",\"sensor_ok\":%s,\"warming_up\":%s,"
        "\"adc_raw\":%s,\"adc_mv\":%s,\"do_level\":%s,\"do_active\":%s,"
        "\"alarm\":%s,\"sample_age_ms\":%s,\"error\":\"%s\"}",
        view.sensor_ok ? "true" : "false", view.warming_up ? "true" : "false",
        raw, mv, level, active, alarm, age, view.error);
    return written >= 0 && (size_t)written < capacity;
}

bool mq2_status_fields(const mq2_state_t *state, uint64_t now_ms, char *body, size_t capacity)
{
    mq2_view_t view = view_at(state, now_ms);
    char raw[16] = "null";
    if (view.sensor_ok) snprintf(raw, sizeof(raw), "%d", state->adc_raw);
    const char *alarm = !view.alarm_valid ? "null" : state->alarm ? "true" : "false";
    int written = snprintf(body, capacity,
        ",\"mq2_sensor_ok\":%s,\"mq2_adc_raw\":%s,\"mq2_alarm\":%s",
        view.sensor_ok ? "true" : "false", raw, alarm);
    return written >= 0 && (size_t)written < capacity;
}
