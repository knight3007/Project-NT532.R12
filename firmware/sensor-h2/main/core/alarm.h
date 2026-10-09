// Logic báo động trên sensor node (kế hoạch mục 4, "Logic trên sensor node"). C thuần, test trên máy.
//
// Mỗi giây đưa một mẫu vào alarm_feed. Báo động khi 3 mẫu liên tiếp vượt ngưỡng (nhiệt >= temp_c HOẶC
// gas >= gas); hủy khi 5 mẫu liên tiếp dưới ngưỡng cả hai. Trong warmup_ms đầu sau khi bật, bỏ qua gas
// (MQ-2 chưa nóng) nhưng vẫn xét nhiệt. Ngưỡng mặc định khớp config/site.yaml (sensors: 45 °C, 600).

#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    float temp_c;          // ngưỡng nhiệt
    float gas;             // ngưỡng gas (đơn vị như số đọc gửi lên /t)
    unsigned n_on;         // số mẫu liên tiếp vượt để báo động (3)
    unsigned n_off;        // số mẫu liên tiếp dưới để hủy (5)
    uint32_t warmup_ms;    // bỏ qua gas trong khoảng này sau khi bật
} alarm_config_t;

typedef enum { ALARM_NONE = 0, ALARM_RAISE, ALARM_CLEAR } alarm_event_t;

typedef struct {
    alarm_config_t cfg;
    bool active;
    unsigned over, under;
} alarm_t;

void alarm_default_config(alarm_config_t *cfg);
void alarm_init(alarm_t *al, const alarm_config_t *cfg);
// up_ms: thời gian từ lúc bật. Trả sự kiện chuyển trạng thái (chỉ một lần mỗi lần chuyển).
alarm_event_t alarm_feed(alarm_t *al, uint32_t up_ms, float temp_c, float gas);

#ifdef __cplusplus
}
#endif
