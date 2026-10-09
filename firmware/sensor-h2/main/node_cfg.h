// Cấu hình node: mặc định từ Kconfig, ghi đè từ NVS namespace "nt532" nếu có key.
// Số thực lưu trong NVS dạng chuỗi ("47.5") để viết bằng nvs_partition_gen cho dễ.
#pragma once

#include <stdbool.h>
#include <stdint.h>

typedef struct {
    char node_id[8];
    float temp_c, gas;                 // ngưỡng báo động
    uint32_t telemetry_s, warmup_s, remote_alarm_s;
    int pulse_min_us, pulse_max_us;    // xung servo ứng với -90 và +90 độ
    float pan_off, tilt_off;           // độ
    bool pan_inv, tilt_inv;
    float pan_min, pan_max, tilt_min, tilt_max;
    uint32_t hb_timeout_ms, max_fire_ms;
} node_cfg_t;

// Cần nvs_flash_init() trước. Key NVS thiếu hoặc hỏng thì giữ mặc định Kconfig.
void node_cfg_load(node_cfg_t *c);
const node_cfg_t *node_cfg(void);
