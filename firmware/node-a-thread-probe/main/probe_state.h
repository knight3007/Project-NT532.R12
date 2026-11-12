#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

enum { PROBE_GET = 1, PROBE_POST = 2 };
enum { PROBE_INFO, PROBE_HB, PROBE_STATUS, PROBE_STOP };
enum { PROBE_CHANGED = 68, PROBE_CONTENT = 69, PROBE_METHOD_NOT_ALLOWED = 133 };

typedef struct {
    uint64_t last_hb_ms;
    uint32_t hb_count;
    uint32_t stop_count;
    bool has_hb;
    bool stopped;
} probe_state_t;

uint8_t probe_apply(probe_state_t *state, int resource, int method, uint64_t now_ms);
int64_t probe_hb_age_ms(const probe_state_t *state, uint64_t now_ms);
bool probe_parse_tlv(const char *line, uint8_t *out, size_t capacity, uint8_t *length);
