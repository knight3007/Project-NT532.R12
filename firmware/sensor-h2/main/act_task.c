#include "act_task.h"

#include <string.h>

#include "coap_node.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "hal_esp.h"
#include "proto.h"

static const char *TAG = "act";

#define TICK_MS 10

static act_t s_act;
static QueueHandle_t s_q;
static char s_node[8];

uint32_t act_now_ms(void)
{
    return (uint32_t)(esp_timer_get_time() / 1000);
}

// Các hàm io được gọi đồng bộ từ act_*, trong task này.
static void io_set_angles(void *ctx, float pan, float tilt)
{
    (void)ctx;
    hal_servo_set(pan, tilt);
}

static void io_set_dev(void *ctx, act_dev_t dev, bool on)
{
    (void)ctx;
    hal_dev_set(dev, on);
    ESP_LOGI(TAG, "%s %s", dev == ACT_DEV_PUMP ? "bơm" : "laser", on ? "BẬT" : "tắt");
}

static void io_send_status(void *ctx, uint32_t id, const char *st, float pan, float tilt, const char *err)
{
    (void)ctx;
    char buf[PROTO_MAX];
    size_t n = proto_status(buf, sizeof(buf), id, st, pan, tilt, err, s_node);
    if (n == 0) {
        ESP_LOGE(TAG, "/status id=%u không vừa bộ đệm", (unsigned)id);
        return;
    }
    ESP_LOGI(TAG, "status id=%u %s%s%s", (unsigned)id, st, err ? " err=" : "", err ? err : "");
    coap_node_send(COAP_OUT_STATUS, buf, n);  // không gọi libcoap trực tiếp
}

static void handle(const act_cmd_t *c, uint32_t now)
{
    switch (c->type) {
    case CMD_AIM:
        act_aim(&s_act, now, c->id, c->pan, c->tilt, c->ttl_ms);
        break;
    case CMD_FIRE:
        act_fire(&s_act, now, c->id, c->dev, c->ms);
        break;
    case CMD_STOP:
        act_stop(&s_act, now, c->id);
        break;
    case CMD_HB:
        act_hb(&s_act, now);
        break;
    }
}

static void act_task(void *arg)
{
    (void)arg;
    act_cmd_t c;
    for (;;) {
        if (xQueueReceive(s_q, &c, pdMS_TO_TICKS(TICK_MS)) == pdTRUE) {
            handle(&c, act_now_ms());
        }
        act_tick(&s_act, act_now_ms());
    }
}

bool act_task_post(const act_cmd_t *cmd)
{
    if (!s_q) {
        return false;
    }
    if (cmd->type == CMD_STOP) {
        return xQueueSendToFront(s_q, cmd, 0) == pdTRUE;
    }
    return xQueueSend(s_q, cmd, 0) == pdTRUE;
}

void act_task_start(const node_cfg_t *cfg)
{
    strncpy(s_node, cfg->node_id, sizeof(s_node) - 1);
    act_config_t ac;
    act_default_config(&ac);
    ac.pan_min = cfg->pan_min;
    ac.pan_max = cfg->pan_max;
    ac.tilt_min = cfg->tilt_min;
    ac.tilt_max = cfg->tilt_max;
    ac.hb_timeout_ms = cfg->hb_timeout_ms;
    ac.max_fire_ms = cfg->max_fire_ms;
    act_io_t io = {
        .set_angles = io_set_angles,
        .set_dev = io_set_dev,
        .send_status = io_send_status,
        .ctx = NULL,
    };
    act_init(&s_act, &ac, &io);
    act_all_off(&s_act);
    s_q = xQueueCreate(16, sizeof(act_cmd_t));
    configASSERT(s_q);
    xTaskCreate(act_task, "act", 4096, NULL, 6, NULL);
}
