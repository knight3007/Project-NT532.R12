#include <assert.h>
#include <string.h>

#include "integrated_control.h"

int main(void)
{
    control_state_t state;
    control_init(&state);
    assert(!state.armed && !state.pwm_active && !state.laser_on);
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
    assert(control_laser_request(&state, "invalid", 7, 100) == CONTROL_BAD_REQUEST);
    assert(!state.laser_on);
    assert(control_laser_request(&state, "ON", 2, 100) == CONTROL_OK);
    assert(state.laser_on);
    assert(control_laser_request(&state, "OFF", 3, 100) == CONTROL_OK);
    assert(!state.laser_on);

    assert(control_pantilt(&state, "1600,1400", 9, 100) == CONTROL_OK);
    int pan = 0, tilt = 0;
    assert(control_next_step(&state, &pan, &tilt));
    assert(pan == 1520 && tilt == 1480);
    assert(control_pantilt(&state, "HOME", 4, 120) == CONTROL_OK);
    assert(control_next_step(&state, &pan, &tilt));
    assert(pan == 1500 && tilt == 1500);

    const char *invalid[] = {
        "", "0,0", "1299,1500", "1701,1500", "1500,1299", "1500,1701",
        "-1500,1500", "1500,1500x", "1500,1500,1500", "9999999999,1500",
        "1500 ,1500", "1500\n,1500"
    };
    for (size_t i = 0; i < sizeof(invalid) / sizeof(invalid[0]); ++i) {
        int target_pan = state.target_pan_us, target_tilt = state.target_tilt_us;
        assert(control_pantilt(&state, invalid[i], strlen(invalid[i]), 130) == CONTROL_BAD_REQUEST);
        assert(state.target_pan_us == target_pan && state.target_tilt_us == target_tilt);
    }

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
    return 0;
}
