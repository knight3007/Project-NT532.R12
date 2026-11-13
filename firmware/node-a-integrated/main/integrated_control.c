#include "integrated_control.h"

#include <stdio.h>
#include <string.h>

_Static_assert(CONTROL_PAN_CAL_MIN_US <= CONTROL_HOME_US &&
               CONTROL_HOME_US <= CONTROL_PAN_CAL_MAX_US &&
               CONTROL_TILT_CAL_MIN_US <= CONTROL_HOME_US &&
               CONTROL_HOME_US <= CONTROL_TILT_CAL_MAX_US,
               "HOME must fit both calibrated servo pulse ranges");

void control_stop(control_state_t *state)
{
    state->armed = false;
    state->home_requested = false;
    state->pwm_active = false;
    state->laser_on = false;
    state->hb_seen = false; // A fresh heartbeat is required before the next ARM.
    state->pan_us = 0; // Physical position is unknown when no pulse is sent.
    state->tilt_us = 0;
    state->target_pan_us = CONTROL_HOME_US;
    state->target_tilt_us = CONTROL_HOME_US;
}

void control_init(control_state_t *state)
{
    memset(state, 0, sizeof(*state));
    control_stop(state);
}

void control_set_attached(control_state_t *state, bool attached)
{
    state->attached = attached;
    if (!attached) control_stop(state);
}

void control_heartbeat(control_state_t *state, uint64_t now_ms)
{
    state->hb_seen = true;
    state->hb_ms = now_ms;
}

static bool hb_fresh(const control_state_t *state, uint64_t now_ms)
{
    return state->hb_seen && now_ms >= state->hb_ms &&
           now_ms - state->hb_ms <= CONTROL_HB_TIMEOUT_MS;
}

bool control_watchdog(control_state_t *state, uint64_t now_ms)
{
    if (state->attached && hb_fresh(state, now_ms)) return false;
    bool was_active = state->armed || state->home_requested || state->pwm_active || state->laser_on;
    if (was_active) control_stop(state);
    return was_active;
}

static bool parse_uint(const char *body, size_t length, size_t *index, int max_us, int *value)
{
    if (*index == length || body[*index] < '0' || body[*index] > '9') return false;
    int number = 0;
    do {
        number = number * 10 + body[(*index)++] - '0';
        if (number > max_us) return false;
    } while (*index < length && body[*index] >= '0' && body[*index] <= '9');
    *value = number;
    return true;
}

static bool parse_pair(const char *body, size_t length, int *pan_us, int *tilt_us)
{
    if (length == 0 || length > 16) return false;
    size_t index = 0;
    if (!parse_uint(body, length, &index, CONTROL_PAN_CAL_MAX_US, pan_us) ||
        index == length || body[index++] != ',')
        return false;
    return parse_uint(body, length, &index, CONTROL_TILT_CAL_MAX_US, tilt_us) && index == length;
}

control_result_t control_pantilt(control_state_t *state, const char *body, size_t length,
                                 uint64_t now_ms)
{
    if (length == 3 && memcmp(body, "ARM", 3) == 0) {
        if (!state->attached || !hb_fresh(state, now_ms)) return CONTROL_FORBIDDEN;
        state->armed = true;
        return CONTROL_OK;
    }
    if (length == 4 && memcmp(body, "HOME", 4) == 0) {
        if (!state->attached || !hb_fresh(state, now_ms) || !state->armed)
            return CONTROL_FORBIDDEN;
        state->target_pan_us = CONTROL_HOME_US;
        state->target_tilt_us = CONTROL_HOME_US;
        if (!state->pwm_active) state->home_requested = true;
        return CONTROL_OK;
    }
    int pan_us, tilt_us;
    if (!parse_pair(body, length, &pan_us, &tilt_us) ||
        pan_us < CONTROL_PAN_CAL_MIN_US || pan_us > CONTROL_PAN_CAL_MAX_US ||
        tilt_us < CONTROL_TILT_CAL_MIN_US || tilt_us > CONTROL_TILT_CAL_MAX_US)
        return CONTROL_BAD_REQUEST;
    if (!state->attached || !hb_fresh(state, now_ms) || !state->armed || !state->pwm_active)
        return CONTROL_FORBIDDEN;
    state->target_pan_us = pan_us;
    state->target_tilt_us = tilt_us;
    return CONTROL_OK;
}

control_result_t control_laser_request(control_state_t *state, const char *body, size_t length,
                                       uint64_t now_ms)
{
    if (length == 3 && memcmp(body, "OFF", 3) == 0) {
        state->laser_on = false;
        return CONTROL_OK;
    }
    if (length == 2 && memcmp(body, "ON", 2) == 0) {
        if (!state->attached || !hb_fresh(state, now_ms) || !state->armed || !state->pwm_active)
            return CONTROL_FORBIDDEN;
        state->laser_on = true;
        return CONTROL_OK;
    }
    return CONTROL_BAD_REQUEST;
}

bool control_pwm_started(control_state_t *state)
{
    if (!state->armed || !state->home_requested || !state->attached) return false;
    state->home_requested = false;
    state->pwm_active = true;
    state->pan_us = CONTROL_HOME_US;
    state->tilt_us = CONTROL_HOME_US;
    return true;
}

static int step_toward(int current, int target)
{
    if (current < target) return target - current < CONTROL_STEP_US ? target : current + CONTROL_STEP_US;
    if (current > target) return current - target < CONTROL_STEP_US ? target : current - CONTROL_STEP_US;
    return current;
}

bool control_next_step(control_state_t *state, int *pan_us, int *tilt_us)
{
    if (!state->pwm_active) return false;
    int next_pan = step_toward(state->pan_us, state->target_pan_us);
    int next_tilt = step_toward(state->tilt_us, state->target_tilt_us);
    if (next_pan == state->pan_us && next_tilt == state->tilt_us) return false;
    state->pan_us = next_pan;
    state->tilt_us = next_tilt;
    *pan_us = next_pan;
    *tilt_us = next_tilt;
    return true;
}

bool control_pantilt_status_json(const control_state_t *state, char *body, size_t capacity)
{
    char pan[16] = "null", tilt[16] = "null";
    if (state->pwm_active) {
        snprintf(pan, sizeof(pan), "%d", state->pan_us);
        snprintf(tilt, sizeof(tilt), "%d", state->tilt_us);
    }
    int written = snprintf(body, capacity,
        "{\"armed\":%s,\"pwm_active\":%s,\"pan_us\":%s,\"tilt_us\":%s,"
        "\"target_pan_us\":%d,\"target_tilt_us\":%d,\"pan_min_us\":%d,"
        "\"pan_max_us\":%d,\"tilt_min_us\":%d,\"tilt_max_us\":%d}",
        state->armed ? "true" : "false", state->pwm_active ? "true" : "false",
        pan, tilt, state->target_pan_us, state->target_tilt_us,
        CONTROL_PAN_CAL_MIN_US, CONTROL_PAN_CAL_MAX_US,
        CONTROL_TILT_CAL_MIN_US, CONTROL_TILT_CAL_MAX_US);
    return written >= 0 && (size_t)written < capacity;
}
