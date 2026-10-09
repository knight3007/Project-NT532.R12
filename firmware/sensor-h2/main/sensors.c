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

#define BLINK_MS 200           // nửa chu kỳ nháy: 2,5 Hz
#define IND_REMOTE (1u << 0)   // vừa nhận /alarm của node khác
#define IND_LOCAL (1u << 1)    // báo động tại chỗ vừa bật hoặc tắt

static node_cfg_t s_cfg;
static volatile bool s_local_alarm;
static TaskHandle_t s_indic;
static uint32_t s_seq;  // s: tăng cho mỗi /t và /a

static uint32_t up_ms(void)
{
    return (uint32_t)(esp_timer_get_time() / 1000);
}

void sensors_remote_alarm(void)
{
    if (s_indic) {
        xTaskNotify(s_indic, IND_REMOTE, eSetBits);
    }
}

static void set_local_alarm(bool on)
{
    s_local_alarm = on;
    xTaskNotify(s_indic, IND_LOCAL, eSetBits);
}

// Chỉ task này ghi GPIO còi: sáng liên tục khi báo động tại chỗ, nháy 2,5 Hz trong remote_alarm_s sau mỗi
// /alarm từ node kia. Không có gì thì ngủ hẳn trên thông báo; chỉ thức theo nhịp nháy khi đang nháy.
static void indicator_task(void *arg)
{
    (void)arg;
    bool remote = false;
    uint32_t start = 0, until = 0;  // ms; nháy từ start tới until
    TickType_t wait = portMAX_DELAY;
    for (;;) {
        uint32_t bits = 0;
        if (xTaskNotifyWait(0, UINT32_MAX, &bits, wait) != pdTRUE) {
            bits = 0;  // hết giờ: tới lần đổi nhịp
        }
        uint32_t now = up_ms();
        if (bits & IND_REMOTE) {
            if (!remote) {
                remote = true;
                start = now;
            }
            until = now + s_cfg.remote_alarm_s * 1000u;  // đang nháy thì chỉ kéo dài
        }
        if (remote && (int32_t)(now - until) >= 0) {
            remote = false;
        }
        bool on = s_local_alarm;
        wait = portMAX_DELAY;  // báo động tại chỗ đổi thì task cảm biến đánh thức
        if (!on && remote) {
            uint32_t el = now - start;
            on = (el / BLINK_MS) % 2u == 0u;
            wait = pdMS_TO_TICKS(BLINK_MS - el % BLINK_MS);  // tới đúng lần đổi kế tiếp
            if (wait == 0) {
                wait = 1;
            }
        }
        hal_buzzer_set(on);
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
    uint32_t telem_left = s_cfg.telemetry_s;  // số mẫu còn lại tới /t kế tiếp
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
            set_local_alarm(true);
            uint32_t s = ++s_seq;
            ESP_LOGW(TAG, "BÁO ĐỘNG t=%.1f g=%.0f s=%u", t, g, (unsigned)s);
            push(COAP_OUT_ALERT, proto_alert(buf, sizeof(buf), s_cfg.node_id, s, "alert", t, g), buf);
            push(COAP_OUT_ALARM, proto_alarm(buf, sizeof(buf), s_cfg.node_id, s), buf);
        } else if (ev == ALARM_CLEAR) {
            set_local_alarm(false);
            uint32_t s = ++s_seq;
            ESP_LOGI(TAG, "hủy báo động t=%.1f g=%.0f s=%u", t, g, (unsigned)s);
            push(COAP_OUT_ALERT, proto_alert(buf, sizeof(buf), s_cfg.node_id, s, "clear", t, g), buf);
        }

        if (--telem_left == 0) {
            telem_left = s_cfg.telemetry_s;
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
    xTaskCreate(indicator_task, "indic", 2048, NULL, 2, &s_indic);
    xTaskCreate(sensor_task, "sensors", 4096, NULL, 3, NULL);
}
