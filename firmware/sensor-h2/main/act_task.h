// Task chấp hành: sở hữu act_t. Lệnh từ CoAP vào qua hàng đợi, /status ra qua coap_node_send.
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "actuator.h"
#include "node_cfg.h"

typedef enum { CMD_AIM, CMD_FIRE, CMD_STOP, CMD_HB } act_cmd_type_t;

typedef struct {
    act_cmd_type_t type;
    uint32_t id;          // aim, fire, stop
    float pan, tilt;      // aim
    uint32_t ttl_ms;      // aim
    act_dev_t dev;        // fire
    uint32_t ms;          // fire
} act_cmd_t;

void act_task_start(const node_cfg_t *cfg);
// Gọi từ task CoAP. false nếu hàng đợi đầy. /stop được chèn lên đầu hàng đợi.
bool act_task_post(const act_cmd_t *cmd);
uint32_t act_now_ms(void);
