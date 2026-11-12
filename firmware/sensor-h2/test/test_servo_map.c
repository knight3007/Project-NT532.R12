#include <math.h>

#include "check.h"
#include "servo_map.h"

// Synthetic Phase C mechanical-angle fixtures; not measured laser aim profiles.
static const servo_map_point_t pan_points[] = {{135, 1000}, {90, 1500}, {45, 2000}};
static const servo_map_point_t tilt_points[] = {{45, 1000}, {90, 1500}, {135, 2000}};
static const servo_map_t pan = {pan_points, 3, 500, 1500, 2500};
static const servo_map_t tilt = {tilt_points, 3, 600, 1500, 2500};
// Boundary knots below are mathematical fixtures, not observed safe mechanical travel.
static const servo_map_point_t pan_bounds[] = {{180, 500}, {90, 1500}, {0, 2500}};
static const servo_map_point_t tilt_bounds[] = {{9, 600}, {90, 1500}, {180, 2500}};
static const servo_map_t pan_full = {pan_bounds, 3, 500, 1500, 2500};
static const servo_map_t tilt_full = {tilt_bounds, 3, 600, 1500, 2500};

static void t_home_and_mechanical_direction(void)
{
    float us = -1.0f, deg = NAN;
    CHECK(servo_map_to_pulse(&pan, 0, 0, false, &us) && us == 1500.0f);
    CHECK(servo_map_to_pulse(&tilt, 0, 0, false, &us) && us == 1500.0f);
    CHECK(servo_map_to_pulse(&pan, 45, 0, false, &us) && us == 1000.0f);
    CHECK(servo_map_to_pulse(&pan, -45, 0, false, &us) && us == 2000.0f);
    CHECK(servo_map_to_pulse(&tilt, -45, 0, false, &us) && us == 1000.0f);
    CHECK(servo_map_to_pulse(&tilt, 45, 0, false, &us) && us == 2000.0f);
    CHECK(servo_map_to_relative(&pan, 1500, 0, false, &deg) && deg == 0.0f);
    CHECK(servo_map_to_relative(&tilt, 2000, 0, false, &deg) && deg == 45.0f);
}

static void t_independent_pulse_bounds(void)
{
    float us = -1.0f, deg = -1.0f;
    CHECK(servo_map_to_pulse(&pan_full, 90, 0, false, &us) && us == 500.0f);
    CHECK(servo_map_to_pulse(&pan_full, 0, 0, false, &us) && us == 1500.0f);
    CHECK(servo_map_to_pulse(&pan_full, -90, 0, false, &us) && us == 2500.0f);
    CHECK(servo_map_to_pulse(&tilt_full, -81, 0, false, &us) && us == 600.0f);
    CHECK(servo_map_to_pulse(&tilt_full, 0, 0, false, &us) && us == 1500.0f);
    CHECK(servo_map_to_pulse(&tilt_full, 90, 0, false, &us) && us == 2500.0f);
    CHECK(servo_map_to_relative(&pan_full, 500, 0, false, &deg) && deg == 90.0f);
    CHECK(servo_map_to_relative(&tilt_full, 600, 0, false, &deg) && deg == -81.0f);
    CHECK(!servo_map_to_pulse(&pan_full, 91, 0, false, &us));
    CHECK(!servo_map_to_pulse(&tilt_full, -82, 0, false, &us));
    CHECK(!servo_map_to_relative(&pan_full, 499, 0, false, &deg));
    CHECK(!servo_map_to_relative(&tilt_full, 599, 0, false, &deg));
    CHECK(!servo_map_to_pulse(&pan, 46, 0, false, &us)); // no extrapolation to JSON bound
    CHECK(!servo_map_to_pulse(&tilt, -46, 0, false, &us));
    CHECK(!servo_map_to_relative(&pan, 500, 0, false, &deg));
    CHECK(!servo_map_to_relative(&tilt, 600, 0, false, &deg));
}

static void t_offset_and_inversion_once(void)
{
    float us = -1.0f, deg = -1.0f;
    CHECK(servo_map_to_pulse(&pan, 45, 0, true, &us) && us == 2000.0f);
    CHECK(servo_map_to_relative(&pan, us, 0, true, &deg) && deg == 45.0f);
    CHECK(servo_map_to_pulse(&tilt, 45, 0, true, &us) && us == 1000.0f);
    CHECK(servo_map_to_pulse(&tilt, 0, 9, false, &us) && us == 1600.0f);
    CHECK(servo_map_to_relative(&tilt, us, 9, false, &deg) && deg == 0.0f);
    CHECK(servo_map_to_pulse(&pan, 20, 5, true, &us) && fabsf(us - 1666.6666f) < 0.01f);
    CHECK(servo_map_to_relative(&pan, us, 5, true, &deg) && fabsf(deg - 20.0f) < 0.01f);
}

static void t_invalid_input_and_profile(void)
{
    float us = 4321.0f, deg = 123.0f;
    CHECK(!servo_map_to_pulse(NULL, 0, 0, false, &us));
    CHECK(!servo_map_to_pulse(&pan, 0, 0, false, NULL));
    CHECK(!servo_map_to_relative(&pan, 1500, 0, false, NULL));
    CHECK(!servo_map_to_pulse(&pan, NAN, 0, false, &us));
    CHECK(!servo_map_to_pulse(&pan, INFINITY, 0, false, &us));
    CHECK(!servo_map_to_pulse(&pan, 0, -INFINITY, false, &us));
    CHECK(!servo_map_to_relative(&pan, NAN, 0, false, &deg));
    CHECK(!servo_map_to_relative(&pan, INFINITY, 0, false, &deg));
    CHECK(!servo_map_to_relative(&pan, 1500, NAN, false, &deg));

    const servo_map_t missing = {NULL, 3, 500, 1500, 2500};
    const servo_map_t short_map = {pan_points, 1, 500, 1500, 2500};
    const servo_map_t bad_limits = {pan_points, 3, 2500, 1500, 500};
    const servo_map_point_t no_home[] = {{135, 1000}, {45, 2000}};
    const servo_map_point_t duplicate_pulse[] = {{135, 1000}, {90, 1500}, {45, 1500}};
    const servo_map_point_t duplicate_angle[] = {{135, 1000}, {90, 1500}, {90, 2000}};
    const servo_map_point_t reversal[] = {{135, 1000}, {90, 1500}, {100, 2000}};
    const servo_map_point_t outside[] = {{135, 1000}, {90, 1500}, {45, 2600}};
    const servo_map_point_t nonfinite[] = {{135, 1000}, {NAN, 1500}, {45, 2000}};
    const servo_map_t bad[] = {
        missing, short_map, bad_limits,
        {no_home, 2, 500, 1500, 2500},
        {duplicate_pulse, 3, 500, 1500, 2500},
        {duplicate_angle, 3, 500, 1500, 2500},
        {reversal, 3, 500, 1500, 2500},
        {outside, 3, 500, 1500, 2500},
        {nonfinite, 3, 500, 1500, 2500},
    };
    for (size_t i = 0; i < sizeof(bad) / sizeof(bad[0]); i++) {
        CHECK(!servo_map_to_pulse(&bad[i], 0, 0, false, &us));
        CHECK(!servo_map_to_relative(&bad[i], 1500, 0, false, &deg));
    }
    CHECK(us == 4321.0f && deg == 123.0f); // no output mutation on failure
}

static void t_monotonic_and_round_trip(void)
{
    float pan_prev = 2501.0f, tilt_prev = 599.0f;
    for (int angle = -45; angle <= 45; angle += 5) {
        float pan_us, tilt_us, back;
        CHECK(servo_map_to_pulse(&pan, (float)angle, 0, false, &pan_us));
        CHECK(servo_map_to_pulse(&tilt, (float)angle, 0, false, &tilt_us));
        CHECK(pan_us < pan_prev && pan_us >= 500.0f && pan_us <= 2500.0f);
        CHECK(tilt_us > tilt_prev && tilt_us >= 600.0f && tilt_us <= 2500.0f);
        CHECK(servo_map_to_relative(&pan, pan_us, 0, false, &back) &&
              fabsf(back - (float)angle) < 0.01f);
        CHECK(servo_map_to_relative(&tilt, tilt_us, 0, false, &back) &&
              fabsf(back - (float)angle) < 0.01f);
        pan_prev = pan_us;
        tilt_prev = tilt_us;
    }
}

void test_servo_map(void)
{
    t_home_and_mechanical_direction();
    t_independent_pulse_bounds();
    t_offset_and_inversion_once();
    t_invalid_input_and_profile();
    t_monotonic_and_round_trip();
}

#ifdef SERVO_MAP_STANDALONE
int g_checks, g_fails;

int main(void)
{
    test_servo_map();
    fprintf(stderr, "%s: %d checks, %d fails\n", g_fails ? "FAIL" : "OK", g_checks, g_fails);
    return g_fails ? 1 : 0;
}
#endif
