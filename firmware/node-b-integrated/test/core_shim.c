// Lớp mỏng phẳng quanh lõi điều khiển C (integrated_control + probe_state) để Python (ctypes) dùng
// mà không phải khai lại bố cục struct. Chỉ phục vụ node giả trên máy (pi/src/nt532/net/fakenode.py);
// không vào firmware.

#include <stdlib.h>
#include <string.h>

#include "integrated_control.h"
#include "probe_state.h"

#if defined(_WIN32)
#define IC_API __declspec(dllexport)
#else
#define IC_API
#endif

typedef struct {
    control_state_t ctl;
    probe_state_t probe;
} ic_t;

IC_API void *ic_new(void)
{
    ic_t *s = malloc(sizeof *s);
    if (!s) return NULL;
    memset(s, 0, sizeof *s);
    control_init(&s->ctl);
    control_set_attached(&s->ctl, false);
    return s;
}

IC_API void ic_free(void *h) { free(h); }

IC_API void ic_set_attached(void *h, int attached)
{
    control_set_attached(&((ic_t *)h)->ctl, attached != 0);
}

IC_API int ic_probe(void *h, int resource, int method, uint64_t now_ms)
{
    ic_t *s = h;
    uint8_t code = probe_apply(&s->probe, resource, method, now_ms);
    if (code == PROBE_CHANGED) {
        if (resource == PROBE_HB) control_heartbeat(&s->ctl, now_ms);
        else if (resource == PROBE_STOP) control_stop(&s->ctl);
    }
    return code;
}

IC_API int ic_pantilt_post(void *h, const char *body, size_t len, uint64_t now_ms)
{
    return (int)control_pantilt(&((ic_t *)h)->ctl, body, len, now_ms);
}

IC_API int ic_laser_post(void *h, const char *body, size_t len, uint64_t now_ms)
{
    return (int)control_laser_request(&((ic_t *)h)->ctl, body, len, now_ms);
}

IC_API int ic_watchdog(void *h, uint64_t now_ms)
{
    return control_watchdog(&((ic_t *)h)->ctl, now_ms) ? 1 : 0;
}

IC_API int ic_pwm_started(void *h) { return control_pwm_started(&((ic_t *)h)->ctl) ? 1 : 0; }

IC_API int ic_next_step(void *h, uint64_t now_ms, int *pan_us, int *tilt_us)
{
    return control_next_step(&((ic_t *)h)->ctl, now_ms, pan_us, tilt_us) ? 1 : 0;
}

IC_API void ic_snapshot(void *h, int *flags, int *pan_us, int *tilt_us, int *target_pan_us,
                        int *target_tilt_us)
{
    const control_state_t *c = &((ic_t *)h)->ctl;
    *flags = (c->attached ? 1 : 0) | (c->armed ? 2 : 0) | (c->home_requested ? 4 : 0) |
             (c->pwm_active ? 8 : 0) | (c->laser_on ? 16 : 0) | (c->hb_seen ? 32 : 0);
    *pan_us = c->pan_us;
    *tilt_us = c->tilt_us;
    *target_pan_us = c->target_pan_us;
    *target_tilt_us = c->target_tilt_us;
}

IC_API int64_t ic_hb_age_ms(void *h, uint64_t now_ms)
{
    return probe_hb_age_ms(&((ic_t *)h)->probe, now_ms);
}

IC_API uint32_t ic_hb_count(void *h) { return ((ic_t *)h)->probe.hb_count; }
IC_API uint32_t ic_stop_count(void *h) { return ((ic_t *)h)->probe.stop_count; }

IC_API size_t ic_pantilt_json(void *h, char *buf, size_t cap)
{
    if (!control_pantilt_status_json(&((ic_t *)h)->ctl, buf, cap)) return 0;
    return strlen(buf);
}

IC_API int ic_hb_timeout_ms(void) { return CONTROL_HB_TIMEOUT_MS; }

IC_API int ic_limits(int *pan_min, int *pan_max, int *tilt_min, int *tilt_max, int *home_us,
                     int *step_us, int *step_period_ms)
{
    *pan_min = CONTROL_PAN_CAL_MIN_US;
    *pan_max = CONTROL_PAN_CAL_MAX_US;
    *tilt_min = CONTROL_TILT_CAL_MIN_US;
    *tilt_max = CONTROL_TILT_CAL_MAX_US;
    *home_us = CONTROL_HOME_US;
    *step_us = CONTROL_STEP_US;
    *step_period_ms = CONTROL_STEP_PERIOD_MS;
    return 0;
}
