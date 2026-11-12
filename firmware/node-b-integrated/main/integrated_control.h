#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

enum {
    CONTROL_HOME_US = 1500,
    CONTROL_PAN_CAL_MIN_US = 500,
    CONTROL_PAN_CAL_MAX_US = 2500,
    CONTROL_TILT_CAL_MIN_US = 600,
    CONTROL_TILT_CAL_MAX_US = 2500,
    CONTROL_TEST_MIN_US = 1300,
    CONTROL_TEST_MAX_US = 1700,
    CONTROL_STEP_US = 20,
    CONTROL_HB_TIMEOUT_MS = 1500,
};

typedef enum {
    CONTROL_OK,
    CONTROL_BAD_REQUEST,
    CONTROL_FORBIDDEN,
    CONTROL_DISABLED,
} control_result_t;

typedef struct {
    bool attached;
    bool armed;
    bool home_requested;
    bool pwm_active;
    bool laser_on; // Relay LED simulation command; no physical laser is controlled.
    bool hb_seen;
    uint64_t hb_ms;
    int pan_us;
    int tilt_us;
    int target_pan_us;
    int target_tilt_us;
} control_state_t;

void control_init(control_state_t *state);
void control_set_attached(control_state_t *state, bool attached);
void control_heartbeat(control_state_t *state, uint64_t now_ms);
void control_stop(control_state_t *state);
bool control_watchdog(control_state_t *state, uint64_t now_ms);
control_result_t control_pantilt(control_state_t *state, const char *body, size_t length,
                                 uint64_t now_ms);
control_result_t control_laser_request(control_state_t *state, const char *body, size_t length,
                                       uint64_t now_ms);
bool control_pwm_started(control_state_t *state);
bool control_next_step(control_state_t *state, int *pan_us, int *tilt_us);
