#pragma once

#include <stdbool.h>
#include <stddef.h>

// Mechanical angle (degrees) and pulse width (microseconds) for one axis.
typedef struct {
    float angle_deg;
    int pulse_us;
} servo_map_point_t;

typedef struct {
    const servo_map_point_t *points; // ascending pulse; angle strictly monotonic
    size_t count;
    int pulse_min_us;                // tested pulse bounds, not approved travel
    int pulse_home_us;
    int pulse_max_us;
} servo_map_t;

// relative_deg is mechanical displacement from HOME, not laser aim angle.
// target mechanical angle = HOME angle + (invert ? -relative_deg : relative_deg) + offset_deg.
// offset/invert here are a single transform; they are not legacy NVS key semantics.
// No extrapolation or clamping. Output is unquantized microseconds and is unchanged on failure.
bool servo_map_to_pulse(const servo_map_t *map, float relative_deg, float offset_deg,
                        bool invert, float *pulse_us);

// Inverse of servo_map_to_pulse with the same offset/invert, within the point span.
bool servo_map_to_relative(const servo_map_t *map, float pulse_us, float offset_deg,
                           bool invert, float *relative_deg);
