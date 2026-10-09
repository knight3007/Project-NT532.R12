// Task cảm biến (đọc mỗi giây, báo động, telemetry) và task chỉ thị còi/LED (ngủ khi không báo động).
#pragma once

#include "node_cfg.h"

void sensors_start(const node_cfg_t *cfg);
// Gọi khi nhận /alarm từ node khác: nháy còi vài giây, không cần Pi.
void sensors_remote_alarm(void);
