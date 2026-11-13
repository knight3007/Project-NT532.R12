#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "integrated_control.h"
#include "mq2_logic.h"

bool node_a_info_json(const char *version, char *body, size_t capacity);
bool node_a_status_json(const char *role, int64_t hb_age_ms, uint32_t stop_count,
                        const control_state_t *control, const mq2_state_t *mq2,
                        uint64_t now_ms, char *body, size_t capacity);
