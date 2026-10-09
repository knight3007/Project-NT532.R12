// State machine chấp hành của một node: pan–tilt, bơm, laser. C thuần, không phụ thuộc ESP-IDF,
// để test trên máy (firmware/sensor-h2/test). Mọi thời gian tính bằng ms theo đồng hồ đơn điệu
// do lớp gọi truyền vào; module không tự đọc đồng hồ, không tự tạo luồng.
//
// Hợp đồng: kế hoạch mục 4, docs/orchestrator.md và SimLink (pi/src/nt532/sim/world.py), phải giữ
// đúng các chuỗi `st` và `err` dưới đây vì Pi so khớp theo chúng.
//
//   /aim  {id,pan,tilt,ttl}  -> "accepted" rồi "reached" khi servo tới nơi (ước tính theo tốc độ quay),
//                               "rejected"/"limits" nếu ngoài giới hạn, "fault"/"ttl" nếu chưa tới mà hết ttl
//   /fire {id,dev,ms}        -> chỉ khi aim cùng id đang "reached": "accepted", hết ms thì "done";
//                               bị /stop hoặc mất heartbeat thì "fault" với err "stopped" / "hb";
//                               aim chưa reached hoặc đã bị aim khác thay thì "rejected"/"not reached"
//   /stop {id}               -> tắt bơm và laser ngay, "done"
//   /hb                      -> làm mới watchdog; mất quá hb_timeout_ms thì tắt bơm và laser
//
// Quy tắc /stop: id lệnh tăng dần trên toàn hệ thống. Nhớ id /stop lớn nhất; /aim, /fire có id nhỏ
// hơn là gói cũ (UDP đảo thứ tự, gửi lại) -> "rejected"/"stopped", không bật gì.
// Lệnh trùng id (CoAP gửi lại): không xử lý lại, chỉ gửi lại trạng thái gần nhất của id đó.

#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum { ACT_DEV_PUMP = 0, ACT_DEV_LASER = 1 } act_dev_t;

// Giới hạn góc (độ) trong act_default_config chỉ là giá trị dự phòng của lõi cho test trên máy chủ
// (pan ±50, tilt -35..45). act_task.c luôn ghi đè từ Kconfig/NVS (mặc định pan ±70, tilt -35..45).
typedef struct {
    float pan_min, pan_max, tilt_min, tilt_max;  // độ
    float deg_per_s;                              // tốc độ quay ước tính để báo "reached"
    uint32_t settle_ms;                           // cộng thêm sau khi quay xong cho servo ổn định
    uint32_t hb_timeout_ms;                       // mất heartbeat quá chừng này thì tắt (3 nhịp x 500)
    uint32_t max_fire_ms;                         // chặn trên cho ms của /fire (an toàn bơm)
} act_config_t;

// Lớp gọi cài các hàm này. Được gọi đồng bộ từ bên trong act_*; không được gọi ngược lại act_*.
typedef struct {
    void (*set_angles)(void *ctx, float pan, float tilt);
    void (*set_dev)(void *ctx, act_dev_t dev, bool on);
    // gửi /status tới Pi; err NULL nếu không có. pan, tilt là góc hiện tại đã đặt.
    void (*send_status)(void *ctx, uint32_t id, const char *st, float pan, float tilt, const char *err);
    void *ctx;
} act_io_t;

#define ACT_RECENT 8  // số lệnh gần nhất nhớ trạng thái để trả lời lệnh trùng id

typedef struct {
    uint32_t id;
    const char *st;   // trỏ tới chuỗi hằng
    const char *err;  // NULL hoặc chuỗi hằng
} act_recent_t;

typedef struct {
    act_config_t cfg;
    act_io_t io;
    float pan, tilt;            // góc đang đặt
    uint32_t last_stop_id;
    uint32_t hb_last_ms;
    bool hb_seen;               // chưa có nhịp nào thì không bật được bơm/laser
    // aim đang chạy hoặc đã tới
    uint32_t aim_id;
    bool aim_active, aim_reached;
    uint32_t aim_deadline_ms;   // lúc hết ttl
    uint32_t aim_reach_ms;      // lúc ước tính servo tới nơi
    // fire đang chạy (một thiết bị tại một thời điểm)
    bool fire_active;
    uint32_t fire_id;
    act_dev_t fire_dev;
    uint32_t fire_end_ms;
    act_recent_t recent[ACT_RECENT];
    unsigned recent_next;
} act_t;

void act_default_config(act_config_t *cfg);
void act_init(act_t *a, const act_config_t *cfg, const act_io_t *io);

void act_aim(act_t *a, uint32_t now_ms, uint32_t id, float pan, float tilt, uint32_t ttl_ms);
void act_fire(act_t *a, uint32_t now_ms, uint32_t id, act_dev_t dev, uint32_t ms);
void act_stop(act_t *a, uint32_t now_ms, uint32_t id);
void act_hb(act_t *a, uint32_t now_ms);
// Gọi đều (mỗi 10–20 ms): báo "reached", hết ttl, hết thời gian bật, watchdog heartbeat.
void act_tick(act_t *a, uint32_t now_ms);

// Tắt tất cả ngay (lỗi mạng, khởi động). Không gửi /status.
void act_all_off(act_t *a);

#ifdef __cplusplus
}
#endif
