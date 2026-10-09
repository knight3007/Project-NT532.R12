#include "sensors.h"

#include <string.h>

#include "alarm.h"
#include "coap_node.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "hal_esp.h"
#include "proto.h"

static const char *TAG = "sensors";

static node_cfg_t s_cfg;
static volatile bool s_local_alarm;
static volatile int64_t s_remote_until_us;  // 0: không nháy
static uint32_t s_seq;                      // s: tăng cho mỗi /t và /a

static uint32_t up_ms(void)
{
    return (uint32_t)(esp_timer_get_time() / 1000);
}

void sensors_remote_alarm(void)
{
    s_remote_until_us = esp_timer_get_time() + (int64_t)s_cfg.remote_alarm_s * 1000000;
}

// Chỉ task này ghi GPIO còi: sáng liên tục khi báo động tại chỗ, nháy 2,5 Hz khi nhận /alarm từ node kia.
static void indicator_task(void *arg)
{
    (void)arg;
    bool blink = false;
    for (;;) {
        bool on = false;
        if (s_local_alarm) {
            on = true;
        } else if (esp_timer_get_time() < s_remote_until_us) {
            blink = !blink;
            on = blink;
        }
        hal_buzzer_set(on);
        vTaskDelay(pdMS_TO_TICKS(200));
    }
}

static void push(coap_out_kind_t kind, size_t n, const char *buf)
{
    if (n == 0) {
        ESP_LOGE(TAG, "bản tin loại %d không vừa bộ đệm", (int)kind);
        return;
    }
    coap_node_send(kind, buf, n);
}

static void sensor_task(void *arg)
{
    (void)arg;
    alarm_config_t ac;
    alarm_default_config(&ac);
    ac.temp_c = s_cfg.temp_c;
    ac.gas = s_cfg.gas;
    ac.warmup_ms = s_cfg.warmup_s * 1000;
    alarm_t al;
    alarm_init(&al, &ac);

    float t = 25.0f, h = 0.0f;  // số đọc hợp lệ gần nhất
    bool have_h = false;
    uint32_t tick = 0;
    char buf[PROTO_MAX];
    TickType_t last = xTaskGetTickCount();

    for (;;) {
        vTaskDelayUntil(&last, pdMS_TO_TICKS(1000));
        float g = hal_gas_read();
        if (hal_sht31_read(&t, &h)) {
            have_h = true;
        }  // lỗi: giữ số cũ (đã log trong HAL)

        alarm_event_t ev = alarm_feed(&al, up_ms(), t, g);
        if (ev == ALARM_RAISE) {
            s_local_alarm = true;
            uint32_t s = ++s_seq;
            ESP_LOGW(TAG, "BÁO ĐỘNG t=%.1f g=%.0f s=%u", t, g, (unsigned)s);
            push(COAP_OUT_ALERT, proto_alert(buf, sizeof(buf), s_cfg.node_id, s, "alert", t, g), buf);
            push(COAP_OUT_ALARM, proto_alarm(buf, sizeof(buf), s_cfg.node_id, s), buf);
        } else if (ev == ALARM_CLEAR) {
            s_local_alarm = false;
            uint32_t s = ++s_seq;
            ESP_LOGI(TAG, "hủy báo động t=%.1f g=%.0f s=%u", t, g, (unsigned)s);
            push(COAP_OUT_ALERT, proto_alert(buf, sizeof(buf), s_cfg.node_id, s, "clear", t, g), buf);
        }

        if (++tick % s_cfg.telemetry_s == 0) {
            uint32_t s = ++s_seq;
            ESP_LOGI(TAG, "t=%.1f h=%.1f g=%.0f s=%u", t, h, g, (unsigned)s);
            push(COAP_OUT_TELEM,
                 proto_telemetry(buf, sizeof(buf), s_cfg.node_id, s, up_ms() / 1000, t, g, have_h, h), buf);
        }
    }
}

void sensors_start(const node_cfg_t *cfg)
{
    s_cfg = *cfg;
    xTaskCreate(indicator_task, "indic", 2048, NULL, 2, NULL);
    xTaskCreate(sensor_task, "sensors", 4096, NULL, 3, NULL);
}
