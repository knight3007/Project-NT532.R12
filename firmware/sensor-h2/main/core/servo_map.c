#include "servo_map.h"

#include <math.h>

static bool valid_map(const servo_map_t *map, double *home_angle)
{
    if (!map || !map->points || map->count < 2 || map->pulse_min_us <= 0 ||
        map->pulse_min_us >= map->pulse_home_us || map->pulse_home_us >= map->pulse_max_us) {
        return false;
    }

    int direction = 0;
    bool has_home = false;
    for (size_t i = 0; i < map->count; i++) {
        const servo_map_point_t *p = &map->points[i];
        if (!isfinite(p->angle_deg) || p->pulse_us < map->pulse_min_us ||
            p->pulse_us > map->pulse_max_us) return false;
        if (p->pulse_us == map->pulse_home_us) {
            *home_angle = p->angle_deg;
            has_home = true;
        }
        if (i == 0) continue;
        const servo_map_point_t *prev = &map->points[i - 1];
        if (p->pulse_us <= prev->pulse_us || p->angle_deg == prev->angle_deg) return false;
        int step = p->angle_deg > prev->angle_deg ? 1 : -1;
        if (direction != 0 && step != direction) return false;
        direction = step;
    }
    return has_home;
}

bool servo_map_to_pulse(const servo_map_t *map, float relative_deg, float offset_deg,
                        bool invert, float *pulse_us)
{
    double home;
    if (!pulse_us || !isfinite(relative_deg) || !isfinite(offset_deg) ||
        !valid_map(map, &home)) return false;

    double target = home + (invert ? -(double)relative_deg : (double)relative_deg) + offset_deg;
    for (size_t i = 1; i < map->count; i++) {
        const servo_map_point_t *a = &map->points[i - 1], *b = &map->points[i];
        double fraction = (target - a->angle_deg) / (b->angle_deg - a->angle_deg);
        if (fraction >= 0.0 && fraction <= 1.0) {
            double pulse = a->pulse_us + fraction * (b->pulse_us - a->pulse_us);
            if (!(pulse >= map->pulse_min_us && pulse <= map->pulse_max_us)) return false;
            *pulse_us = (float)pulse;
            return true;
        }
    }
    return false; // outside the supplied point span: never extrapolate or clamp
}

bool servo_map_to_relative(const servo_map_t *map, float pulse_us, float offset_deg,
                           bool invert, float *relative_deg)
{
    double home;
    if (!relative_deg || !isfinite(pulse_us) || !isfinite(offset_deg) ||
        !valid_map(map, &home) || pulse_us < map->pulse_min_us ||
        pulse_us > map->pulse_max_us) return false;

    for (size_t i = 1; i < map->count; i++) {
        const servo_map_point_t *a = &map->points[i - 1], *b = &map->points[i];
        if (pulse_us >= a->pulse_us && pulse_us <= b->pulse_us) {
            double fraction = ((double)pulse_us - a->pulse_us) /
                              ((double)b->pulse_us - a->pulse_us);
            double mechanical = a->angle_deg + fraction * (b->angle_deg - a->angle_deg);
            double relative = mechanical - home - offset_deg;
            float result = (float)(invert ? -relative : relative);
            if (!isfinite(result)) return false;
            *relative_deg = result;
            return true;
        }
    }
    return false;
}
