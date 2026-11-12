#include <assert.h>
#include <stdint.h>

#include "probe_state.h"

int main(void)
{
    probe_state_t state = {0};
    assert(probe_apply(&state, PROBE_INFO, PROBE_GET, 100) == PROBE_CONTENT);
    assert(probe_apply(&state, PROBE_STATUS, PROBE_GET, 100) == PROBE_CONTENT);
    assert(probe_apply(&state, PROBE_HB, PROBE_GET, 100) == PROBE_METHOD_NOT_ALLOWED);
    assert(probe_hb_age_ms(&state, 100) == -1);

    assert(probe_apply(&state, PROBE_HB, PROBE_POST, 100) == PROBE_CHANGED);
    assert(state.hb_count == 1);
    assert(probe_hb_age_ms(&state, 259) == 159);
    assert(probe_hb_age_ms(&state, 99) == -1);

    assert(probe_apply(&state, PROBE_STOP, PROBE_POST, 300) == PROBE_CHANGED);
    assert(state.stopped);
    assert(state.stop_count == 1);
    assert(probe_apply(&state, PROBE_HB, PROBE_POST, 400) == PROBE_CHANGED);
    assert(state.stopped);
    assert(probe_apply(&state, PROBE_STOP, PROBE_GET, 500) == PROBE_METHOD_NOT_ALLOWED);

    uint8_t tlv[4] = {0};
    uint8_t length = 0;
    assert(probe_parse_tlv("TLV:0aFF\n", tlv, sizeof(tlv), &length));
    assert(length == 2 && tlv[0] == 0x0a && tlv[1] == 0xff);
    assert(!probe_parse_tlv("TLV:0aF\n", tlv, sizeof(tlv), &length));
    assert(!probe_parse_tlv("TLV:0aFG\n", tlv, sizeof(tlv), &length));
    assert(!probe_parse_tlv("TLV:0aFF00\n", tlv, 2, &length));
    return 0;
}
