#include <assert.h>
#include <string.h>

#include "node_a_response.h"
#include "mq2_config.h"

int main(void)
{
    char body[512];
    assert(node_a_info_json("a-integrated-0.1.2", body, sizeof(body)));
    assert(strcmp(body, "{\"n\":\"A\",\"fw\":\"a-integrated-0.1.2\","
                        "\"mode\":\"integrated\",\"sensor\":\"mq2\","
                        "\"outputs\":\"servo_gated;relay_led_simulation;laser_disabled\"}") == 0);
    assert(!node_a_info_json("a-integrated-0.1.2", body, 8));

    control_state_t control;
    control_init(&control);
    assert(control.target_pan_us == 1500 && control.target_tilt_us == 1700);
    const mq2_policy_t policy = {
        .warmup_ms = MQ2_WARMUP_MS, .max_sample_age_ms = MQ2_MAX_SAMPLE_AGE_MS,
        .alarm_threshold_raw = MQ2_ALARM_THRESHOLD_RAW,
        .alarm_hysteresis_raw = MQ2_ALARM_HYSTERESIS_RAW,
    };
    mq2_state_t mq2;
    mq2_logic_init(&mq2, &policy, 0);
    assert(node_a_status_json("child", -1, 0, &control, &mq2, 10, body, sizeof(body)));
    assert(strstr(body, "\"n\":\"A\",\"role\":\"child\",\"stopped\":true") != NULL);
    assert(strstr(body, "\"hb_age_ms\":-1,\"hb_fresh\":false,\"stop_count\":0") != NULL);
    assert(strstr(body, "\"servo_armed\":false,\"servo_pwm\":false,\"relay_on\":false,"
                        "\"laser_on\":false,\"mq2_sensor_ok\":false,\"mq2_adc_raw\":null,"
                        "\"mq2_alarm\":null") != NULL);
    assert(strstr(body, "temperature_c") == NULL);
    assert(strstr(body, "humidity_pct") == NULL);

    control_set_attached(&control, true);
    control_heartbeat(&control, 100);
    assert(control_pantilt(&control, "ARM", 3, 100) == CONTROL_OK);
    assert(control_pantilt(&control, "HOME", 4, 100) == CONTROL_OK);
    assert(control_pwm_started(&control));
    assert(control.pan_us == 1500 && control.tilt_us == 1700);
    assert(control_laser_request(&control, "ON", 2, 100) == CONTROL_OK);
    mq2_logic_record(&mq2, 1234, false, 0, false, 0, 100);
    assert(node_a_status_json("router", 0, 2, &control, &mq2, 100, body, sizeof(body)));
    assert(strstr(body, "\"servo_armed\":true,\"servo_pwm\":true,\"relay_on\":true") != NULL);
    assert(strstr(body, "\"mq2_sensor_ok\":true,\"mq2_adc_raw\":1234,\"mq2_alarm\":null") != NULL);
    assert(!node_a_status_json("router", 0, 2, &control, &mq2, 100, body, 8));
    return 0;
}
