#include "node_a_response.h"

#include <inttypes.h>
#include <stdio.h>

bool node_a_info_json(const char *version, char *body, size_t capacity)
{
    if (version == NULL || body == NULL || capacity == 0) return false;
    int written = snprintf(body, capacity,
        "{\"n\":\"A\",\"fw\":\"%s\",\"mode\":\"integrated\","
        "\"sensor\":\"mq2\",\"outputs\":\"servo_gated;relay_led_simulation;laser_disabled\"}",
        version);
    return written >= 0 && (size_t)written < capacity;
}

bool node_a_status_json(const char *role, int64_t hb_age_ms, uint32_t stop_count,
                        const control_state_t *control, const mq2_state_t *mq2,
                        uint64_t now_ms, char *body, size_t capacity)
{
    if (role == NULL || control == NULL || mq2 == NULL || body == NULL || capacity == 0)
        return false;
    char sensor_fields[128];
    if (!mq2_status_fields(mq2, now_ms, sensor_fields, sizeof(sensor_fields))) return false;
    int written = snprintf(body, capacity,
        "{\"n\":\"A\",\"role\":\"%s\",\"stopped\":%s,"
        "\"hb_age_ms\":%" PRId64 ",\"hb_fresh\":%s,\"stop_count\":%" PRIu32 ","
        "\"servo_armed\":%s,\"servo_pwm\":%s,\"relay_on\":%s,"
        "\"laser_on\":false%s}",
        role, control->pwm_active ? "false" : "true", hb_age_ms,
        control->hb_seen && hb_age_ms >= 0 && hb_age_ms <= CONTROL_HB_TIMEOUT_MS
            ? "true" : "false",
        stop_count, control->armed ? "true" : "false",
        control->pwm_active ? "true" : "false",
        control->laser_on ? "true" : "false", sensor_fields);
    return written >= 0 && (size_t)written < capacity;
}
