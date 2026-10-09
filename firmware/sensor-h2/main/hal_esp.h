// Lớp phần cứng của node H2: bơm, laser, servo, còi, MQ-2 (ADC), SHT31 (I2C).
// Không có trạng thái logic ở đây; actuator.c và alarm.c quyết định khi nào bật.
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "actuator.h"
#include "node_cfg.h"

// Việc đầu tiên khi khởi động, trước NVS và mọi thứ khác: bơm và laser thành output mức thấp.
void hal_safe_gpio_init(void);
// Sau khi có cấu hình: LEDC cho servo (về giữa), ADC, I2C.
void hal_init(const node_cfg_t *cfg);

void hal_servo_set(float pan_deg, float tilt_deg);  // góc lệnh; chặn theo giới hạn, áp lệch và chiều
void hal_dev_set(act_dev_t dev, bool on);
void hal_buzzer_set(bool on);

// MQ-2: trung bình 8 mẫu ADC thô 12 bit (0..4095) trên chân sau cầu phân áp.
float hal_gas_read(void);
// SHT31: false nếu lỗi bus hoặc sai CRC; khi đó giữ nguyên *t, *h của người gọi.
bool hal_sht31_read(float *t_c, float *h_pct);
