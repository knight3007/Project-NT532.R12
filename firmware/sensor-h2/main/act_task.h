// Task chấp hành: sở hữu act_t. Lệnh từ CoAP vào qua hàng đợi, /status ra qua coap_node_send.
// Task chỉ thức khi có lệnh hoặc tới hạn của act_next_ms (tới nơi, hết ttl, hết thời gian bật,
// hết nhịp khi đang bật); lúc nghỉ không có tick định kỳ.
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "actuator.h"
#include "node_cfg.h"

typedef enum { CMD_AIM, CMD_FIRE, CMD_STOP } act_cmd_type_t;

typedef struct {
    act_cmd_type_t type;
    uint32_t id;
    float pan, tilt;      // aim
    uint32_t ttl_ms;      // aim
    act_dev_t dev;        // fire
    uint32_t ms;          // fire
} act_cmd_t;

void act_task_start(const node_cfg_t *cfg);
// Gọi từ task CoAP. false nếu hàng đợi đầy. /stop được chèn lên đầu hàng đợi.
bool act_task_post(const act_cmd_t *cmd);
// Gọi từ task CoAP khi nhận /hb: chỉ ghi mốc thời gian, không đánh thức task chấp hành.
void act_task_hb(void);
uint32_t act_now_ms(void);
