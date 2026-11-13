#include <assert.h>
#include <string.h>

#include "integrated_control.h"

int main(void)
{
    control_state_t state;
    control_init(&state);
    assert(!state.armed && !state.pwm_active && !state.laser_on);
    char status[256];
    assert(control_pantilt_status_json(&state, status, sizeof(status)));
    assert(strcmp(status, "{\"armed\":false,\"pwm_active\":false,\"pan_us\":null,\"tilt_us\":null,"
                          "\"target_pan_us\":1500,\"target_tilt_us\":1500,\"pan_min_us\":500,"
                          "\"pan_max_us\":2500,\"tilt_min_us\":600,\"tilt_max_us\":2500}") == 0);
    assert(control_laser_request(&state, "ON", 2, 100) == CONTROL_FORBIDDEN);
    assert(control_pantilt(&state, "ARM", 3, 100) == CONTROL_FORBIDDEN);

    control_set_attached(&state, true);
    assert(control_laser_request(&state, "ON", 2, 100) == CONTROL_FORBIDDEN);
    assert(control_pantilt(&state, "ARM", 3, 100) == CONTROL_FORBIDDEN);
    control_heartbeat(&state, 100);
    assert(control_laser_request(&state, "ON", 2, 100) == CONTROL_FORBIDDEN);
    assert(control_pantilt(&state, "ARM", 3, 100) == CONTROL_OK);
    assert(state.armed && !state.pwm_active);
    assert(control_laser_request(&state, "ON", 2, 100) == CONTROL_FORBIDDEN);
    assert(control_pantilt(&state, "1600,1400", 9, 100) == CONTROL_FORBIDDEN);
    assert(control_pantilt(&state, "HOME", 4, 100) == CONTROL_OK);
    assert(state.home_requested && !state.pwm_active);
    assert(control_pwm_started(&state));
    assert(state.pwm_active && state.pan_us == 1500 && state.tilt_us == 1500);
    assert(control_pantilt_status_json(&state, status, sizeof(status)));
    assert(strstr(status, "\"pan_us\":1500,\"tilt_us\":1500") != NULL);
    assert(!control_pantilt_status_json(&state, status, 8));
    assert(control_laser_request(&state, "invalid", 7, 100) == CONTROL_BAD_REQUEST);
    assert(!state.laser_on);
    assert(control_laser_request(&state, "ON", 2, 100) == CONTROL_OK);
    assert(state.laser_on);
    assert(control_laser_request(&state, "OFF", 3, 100) == CONTROL_OK);
    assert(!state.laser_on);

    assert(control_pantilt(&state, "1600,1400", 9, 100) == CONTROL_OK);
    int pan = 0, tilt = 0;
    assert(control_next_step(&state, 100, &pan, &tilt));
    assert(pan == 1520 && tilt == 1480);
    assert(control_pantilt(&state, "HOME", 4, 120) == CONTROL_OK);
    assert(control_next_step(&state, 120, &pan, &tilt));
    assert(pan == 1500 && tilt == 1500);

    const char *valid[] = {
        "500,1500", "2500,1500", "1500,600", "1500,2500", "500,600", "2500,2500",
        "1299,1500", "1500,1701"
    };
    for (size_t i = 0; i < sizeof(valid) / sizeof(valid[0]); ++i) {
        assert(control_pantilt(&state, valid[i], strlen(valid[i]), 130) == CONTROL_OK);
    }
    assert(state.target_pan_us == 1500 && state.target_tilt_us == 1701);

    const char *invalid[] = {
        "", "0,0", "499,1500", "2501,1500", "1500,599", "1500,2501",
        "-1500,1500", "1500,1500x", "1500,1500,1500", "9999999999,1500",
        "1500 ,1500", "1500\n,1500"
    };
    for (size_t i = 0; i < sizeof(invalid) / sizeof(invalid[0]); ++i) {
        int target_pan = state.target_pan_us, target_tilt = state.target_tilt_us;
        assert(control_pantilt(&state, invalid[i], strlen(invalid[i]), 130) == CONTROL_BAD_REQUEST);
        assert(state.target_pan_us == target_pan && state.target_tilt_us == target_tilt);
    }

    assert(control_pantilt(&state, "500,600", 7, 140) == CONTROL_OK);
    uint64_t step_now = 140;
    for (int i = 0; i < 50; ++i) {
        int previous_pan = state.pan_us, previous_tilt = state.tilt_us;
        step_now += CONTROL_STEP_PERIOD_MS;
        assert(control_next_step(&state, step_now, &pan, &tilt));
        assert(previous_pan - pan >= 0 && previous_pan - pan <= CONTROL_STEP_US);
        assert(previous_tilt - tilt >= 0 && previous_tilt - tilt <= CONTROL_STEP_US);
    }
    assert(state.pan_us == 500 && state.tilt_us == 600);
    assert(control_pantilt(&state, "2500,2500", 9, 150) == CONTROL_OK);
    step_now += CONTROL_STEP_PERIOD_MS;
    assert(control_next_step(&state, step_now, &pan, &tilt));
    assert(pan == 520 && tilt == 620);
    assert(control_pantilt(&state, "HOME", 4, 160) == CONTROL_OK);
    assert(state.target_pan_us == 1500 && state.target_tilt_us == 1500);
    step_now += CONTROL_STEP_PERIOD_MS;
    assert(control_next_step(&state, step_now, &pan, &tilt));
    assert(pan == 540 && tilt == 640);

    assert(control_laser_request(&state, "ON", 2, 200) == CONTROL_OK);
    assert(state.laser_on);
    control_stop(&state);
    assert(!state.armed && !state.pwm_active && !state.laser_on);
    assert(control_laser_request(&state, "OFF", 3, 201) == CONTROL_OK);
    assert(!control_pwm_started(&state)); // STOP during LEDC setup cannot restore PWM.
    assert(control_pantilt(&state, "HOME", 4, 201) == CONTROL_FORBIDDEN);

    control_heartbeat(&state, 300);
    assert(control_pantilt(&state, "ARM", 3, 300) == CONTROL_OK);
    for (uint64_t now = 320; now <= 400; now += 20) {
        assert(!control_watchdog(&state, now));
        assert(state.armed && !state.home_requested && !state.pwm_active);
    }
    assert(control_pantilt(&state, "HOME", 4, 400) == CONTROL_OK);
    assert(control_pwm_started(&state));
    assert(!control_watchdog(&state, 1800));
    assert(control_watchdog(&state, 1801));
    assert(!state.armed && !state.pwm_active && !state.laser_on);
    assert(control_laser_request(&state, "OFF", 3, 1801) == CONTROL_OK);
    assert(control_pantilt(&state, "HOME", 4, 1801) == CONTROL_FORBIDDEN);

    control_heartbeat(&state, 2000);
    assert(control_pantilt(&state, "HOME", 4, 2000) == CONTROL_FORBIDDEN);
    assert(control_pantilt(&state, "ARM", 3, 2000) == CONTROL_OK);
    assert(control_pantilt(&state, "HOME", 4, 2000) == CONTROL_OK);
    assert(control_pwm_started(&state));
    control_set_attached(&state, false);
    assert(!state.armed && !state.pwm_active && !state.laser_on);
    assert(control_laser_request(&state, "OFF", 3, 2001) == CONTROL_OK);
    control_set_attached(&state, true);
    assert(control_pantilt(&state, "ARM", 3, 2001) == CONTROL_FORBIDDEN);

    control_init(&state); // A reboot never restarts actuators.
    assert(!state.armed && !state.pwm_active && !state.home_requested && !state.laser_on);

    // Heartbeat handled after the caller sampled the clock must not cause a false STOP.
    control_set_attached(&state, true);
    control_heartbeat(&state, 900);
    assert(control_pantilt(&state, "ARM", 3, 900) == CONTROL_OK);
    assert(control_pantilt(&state, "HOME", 4, 900) == CONTROL_OK);
    assert(control_pwm_started(&state));
    control_heartbeat(&state, 1001);
    assert(!control_watchdog(&state, 1000));
    assert(state.armed && state.pwm_active);
    assert(control_pantilt(&state, "ARM", 3, 1000) == CONTROL_OK);
    assert(!control_watchdog(&state, 1001 + CONTROL_HB_TIMEOUT_MS));
    assert(control_watchdog(&state, 1001 + CONTROL_HB_TIMEOUT_MS + 1));
    assert(!state.armed && !state.pwm_active);

    // Ramp is limited to one step per CONTROL_STEP_PERIOD_MS regardless of caller wake-ups.
    control_heartbeat(&state, 2000);
    assert(control_pantilt(&state, "ARM", 3, 2000) == CONTROL_OK);
    assert(control_pantilt(&state, "HOME", 4, 2000) == CONTROL_OK);
    assert(control_pwm_started(&state));
    assert(control_pantilt(&state, "2500,2500", 9, 2000) == CONTROL_OK);
    assert(control_next_step(&state, 1000, &pan, &tilt)); // first step after HOME is not blocked
    assert(pan == 1520 && tilt == 1520);
    assert(!control_next_step(&state, 1005, &pan, &tilt));
    assert(state.pan_us == 1520 && state.tilt_us == 1520);
    assert(!control_next_step(&state, 1019, &pan, &tilt));
    assert(!control_next_step(&state, 900, &pan, &tilt)); // clock behind last step
    assert(state.pan_us == 1520);
    assert(control_next_step(&state, 1020, &pan, &tilt));
    assert(pan == 1540 && tilt == 1540);
    control_stop(&state);
    control_heartbeat(&state, 2010);
    assert(control_pantilt(&state, "ARM", 3, 2010) == CONTROL_OK);
    assert(control_pantilt(&state, "HOME", 4, 2010) == CONTROL_OK);
    assert(control_pwm_started(&state));
    assert(control_pantilt(&state, "2500,2500", 9, 2010) == CONTROL_OK);
    assert(control_next_step(&state, 1021, &pan, &tilt)); // not blocked by pre-STOP step time
    assert(pan == 1520 && tilt == 1520);
    return 0;
}
