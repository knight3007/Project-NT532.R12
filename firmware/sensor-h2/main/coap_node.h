// CoAP của node: server /aim /fire /stop /hb /alarm và client gửi /status /a /t /alarm.
// Chỉ task CoAP được gọi libcoap (libcoap không an toàn đa luồng); task khác đẩy bản tin qua coap_node_send.
// Task CoAP ngủ trong select tới khi có gói vào, tới hạn gửi lại CON, hoặc có bản tin ra (eventfd).
#pragma once

#include <stdbool.h>
#include <stddef.h>

#include "node_cfg.h"

typedef enum {
    COAP_OUT_STATUS,  // CON  -> Pi /status
    COAP_OUT_ALERT,   // CON  -> Pi /a
    COAP_OUT_TELEM,   // NON  -> Pi /t
    COAP_OUT_ALARM,   // NON  -> ff03::1 /alarm
} coap_out_kind_t;

// Tạo hàng đợi ra; gọi sớm để các task khác đẩy được ngay (bản tin chờ tới khi mạng lên).
void coap_node_init(const node_cfg_t *cfg);
// Tạo task CoAP: chờ Thread gắn vào mạng rồi mở server.
void coap_node_start(void);
// Sao chép json vào hàng đợi rồi đánh thức task CoAP; false nếu đầy hoặc quá dài. Gọi từ task bất kỳ.
bool coap_node_send(coap_out_kind_t kind, const char *json, size_t len);
