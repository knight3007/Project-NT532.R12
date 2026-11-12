#include "probe_state.h"

#include <string.h>

uint8_t probe_apply(probe_state_t *state, int resource, int method, uint64_t now_ms)
{
    if (resource == PROBE_INFO || resource == PROBE_STATUS) {
        return method == PROBE_GET ? PROBE_CONTENT : PROBE_METHOD_NOT_ALLOWED;
    }
    if (method != PROBE_POST) {
        return PROBE_METHOD_NOT_ALLOWED;
    }
    if (resource == PROBE_HB) {
        state->last_hb_ms = now_ms;
        state->has_hb = true;
        ++state->hb_count;
    } else if (resource == PROBE_STOP) {
        state->stopped = true;
        ++state->stop_count;
    } else {
        return PROBE_METHOD_NOT_ALLOWED;
    }
    return PROBE_CHANGED;
}

int64_t probe_hb_age_ms(const probe_state_t *state, uint64_t now_ms)
{
    if (!state->has_hb || now_ms < state->last_hb_ms) {
        return -1;
    }
    return (int64_t)(now_ms - state->last_hb_ms);
}

static int hex_digit(char ch)
{
    if (ch >= '0' && ch <= '9') return ch - '0';
    if (ch >= 'a' && ch <= 'f') return ch - 'a' + 10;
    if (ch >= 'A' && ch <= 'F') return ch - 'A' + 10;
    return -1;
}

bool probe_parse_tlv(const char *line, uint8_t *out, size_t capacity, uint8_t *length)
{
    if (strncmp(line, "TLV:", 4) != 0) return false;
    const char *hex = line + 4;
    size_t digits = strcspn(hex, "\r\n");
    if (digits == 0 || digits % 2 != 0 || digits / 2 > capacity || digits / 2 > UINT8_MAX) return false;
    if (hex[digits] == '\r' && hex[digits + 1] == '\n' && hex[digits + 2] == '\0') {
        // CRLF from a serial terminal.
    } else if (hex[digits] == '\n' && hex[digits + 1] == '\0') {
        // LF from a serial terminal.
    } else if (hex[digits] != '\0') {
        return false;
    }
    for (size_t i = 0; i < digits; i += 2) {
        int hi = hex_digit(hex[i]);
        int lo = hex_digit(hex[i + 1]);
        if (hi < 0 || lo < 0) return false;
        out[i / 2] = (uint8_t)((hi << 4) | lo);
    }
    *length = (uint8_t)(digits / 2);
    return true;
}
