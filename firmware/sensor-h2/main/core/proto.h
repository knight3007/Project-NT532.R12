// Payload JSON của hợp đồng CoAP (kế hoạch mục 4, pi/src/nt532/net/protocol.py). C thuần, test trên máy.
//
// Bộ parse chỉ đủ cho object phẳng một tầng với giá trị số, chuỗi ngắn (không escape), true/false/null:
// đúng những gì Pi gửi. Thiếu key bắt buộc, sai kiểu, JSON hỏng -> trả false (lớp CoAP trả 4.00).
// Key lạ bỏ qua. Bộ tạo ghi gọn không khoảng trắng, số thực tối đa 2 chữ số thập phân, bỏ key không có.

#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "actuator.h"

#ifdef __cplusplus
extern "C" {
#endif

#define PROTO_MAX 96  // bộ đệm đủ cho mọi bản tin; hợp đồng nhắm dưới ~60 byte
#define PROTO_INFO_MAX 160  // riêng cho /info (GET một lần, không đi qua hàng đợi ra)

typedef struct { uint32_t id; float pan, tilt; uint32_t ttl; } proto_aim_t;
typedef struct { uint32_t id; act_dev_t dev; uint32_t ms; } proto_fire_t;
typedef struct { uint32_t id; } proto_stop_t;
typedef struct { char n[8]; uint32_t s; } proto_alarm_t;  // /alarm multicast từ node khác

bool proto_parse_aim(const char *buf, size_t len, proto_aim_t *out);
bool proto_parse_fire(const char *buf, size_t len, proto_fire_t *out);   // dev "pump" hoặc "laser"
bool proto_parse_stop(const char *buf, size_t len, proto_stop_t *out);
bool proto_parse_alarm(const char *buf, size_t len, proto_alarm_t *out);

// Trả số byte đã ghi (không tính '\0'), hoặc 0 nếu không vừa `cap`.
// /t: {"n","s","up","t","g"[,"h"]}; has_h=false thì bỏ "h".
size_t proto_telemetry(char *buf, size_t cap, const char *n, uint32_t s, uint32_t up_s, float t, float g,
                       bool has_h, float h);
// /a: {"n","s","k","t","g"}; k là "alert" hoặc "clear".
size_t proto_alert(char *buf, size_t cap, const char *n, uint32_t s, const char *k, float t, float g);
// /status: {"id","st","pan","tilt"[,"err"],"n"}; err NULL thì bỏ.
size_t proto_status(char *buf, size_t cap, uint32_t id, const char *st, float pan, float tilt, const char *err,
                    const char *n);
// /alarm: {"n","s"}
size_t proto_alarm(char *buf, size_t cap, const char *n, uint32_t s);
// /info (GET, một lần khi Pi khởi động): {"n","fw","pan":[lo,hi],"tilt":[lo,hi],"hb","fire","srp":0|1}.
// Dài hơn 60 byte (khoảng 95), nên bộ đệm riêng PROTO_INFO_MAX; hb là hb_timeout_ms, fire là max_fire_ms.
size_t proto_info(char *buf, size_t cap, const char *n, const char *fw, float pan_lo, float pan_hi, float tilt_lo,
                  float tilt_hi, uint32_t hb_ms, uint32_t fire_ms, bool srp);

#ifdef __cplusplus
}
#endif
