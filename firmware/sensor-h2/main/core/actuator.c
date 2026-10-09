// State machine chấp hành: xem actuator.h.

#include "actuator.h"

#include <math.h>
#include <string.h>

#define NEVER_FIRED UINT32_MAX  // fire_id ban đầu; chỉ coi là trùng khi recent cũng có trạng thái fire

static bool time_ge(uint32_t now, uint32_t t)
{
    return (int32_t)(now - t) >= 0;
}

static bool hb_ok(const act_t *a, uint32_t now)
{
    if (!a->hb_seen) {
        return false;
    }
    uint32_t el = now - a->hb_last_ms;
    // el "âm" (hb ghi muộn hơn now một chút) coi là mới
    return (int32_t)el < 0 || el <= a->cfg.hb_timeout_ms;
}

static void dev_off_all(act_t *a)
{
    a->io.set_dev(a->io.ctx, ACT_DEV_PUMP, false);
    a->io.set_dev(a->io.ctx, ACT_DEV_LASER, false);
}

static void send(act_t *a, uint32_t id, const char *st, const char *err)
{
    act_recent_t *r = &a->recent[a->recent_next % ACT_RECENT];
    r->id = id;
    r->st = st;
    r->err = err;
    a->recent_next = (a->recent_next + 1u) % ACT_RECENT;
    a->io.send_status(a->io.ctx, id, st, a->pan, a->tilt, err);
}

// Trạng thái ghi gần nhất của id, NULL nếu chưa có.
static const act_recent_t *find_recent(const act_t *a, uint32_t id)
{
    for (unsigned i = 1; i <= ACT_RECENT; i++) {
        const act_recent_t *r = &a->recent[(a->recent_next + ACT_RECENT - i) % ACT_RECENT];
        if (r->st != NULL && r->id == id) {
            return r;
        }
    }
    return NULL;
}

// Gửi lại trạng thái đã ghi, không ghi thêm.
static void resend(act_t *a, const act_recent_t *r)
{
    uint32_t id = r->id;
    const char *st = r->st, *err = r->err;
    a->io.send_status(a->io.ctx, id, st, a->pan, a->tilt, err);
}

static void fire_abort(act_t *a, const char *err)
{
    if (a->fire_active) {
        a->io.set_dev(a->io.ctx, a->fire_dev, false);
        a->fire_active = false;
        send(a, a->fire_id, "fault", err);
    }
}

void act_default_config(act_config_t *cfg)
{
    cfg->pan_min = -50.0f;
    cfg->pan_max = 50.0f;
    cfg->tilt_min = -35.0f;
    cfg->tilt_max = 45.0f;
    cfg->deg_per_s = 150.0f;
    cfg->settle_ms = 150u;
    cfg->hb_timeout_ms = 1500u;
    cfg->max_fire_ms = 5000u;
}

void act_init(act_t *a, const act_config_t *cfg, const act_io_t *io)
{
    memset(a, 0, sizeof *a);
    a->cfg = *cfg;
    a->io = *io;
    a->fire_id = NEVER_FIRED;
    dev_off_all(a);
    a->io.set_angles(a->io.ctx, 0.0f, 0.0f);
}

void act_all_off(act_t *a)
{
    dev_off_all(a);
    a->fire_active = false;
}

void act_hb(act_t *a, uint32_t now_ms)
{
    a->hb_last_ms = now_ms;
    a->hb_seen = true;
}

void act_aim(act_t *a, uint32_t now_ms, uint32_t id, float pan, float tilt, uint32_t ttl_ms)
{
    const act_recent_t *dup = find_recent(a, id);
    if (dup != NULL) {
        resend(a, dup);
        return;
    }
    if (id < a->last_stop_id) {
        send(a, id, "rejected", "stopped");
        return;
    }
    // viết dạng phủ định để NaN cũng bị loại
    if (!(pan >= a->cfg.pan_min && pan <= a->cfg.pan_max && tilt >= a->cfg.tilt_min &&
          tilt <= a->cfg.tilt_max)) {
        send(a, id, "rejected", "limits");
        return;
    }
    fire_abort(a, "stopped");

    float d = fmaxf(fabsf(pan - a->pan), fabsf(tilt - a->tilt));
    float move_ms = 0.0f;
    if (a->cfg.deg_per_s > 0.0f) {
        move_ms = ceilf(d / a->cfg.deg_per_s * 1000.0f);
    }
    if (move_ms > 3600000.0f) {
        move_ms = 3600000.0f;
    }
    a->pan = pan;
    a->tilt = tilt;
    a->io.set_angles(a->io.ctx, pan, tilt);
    a->aim_id = id;
    a->aim_active = true;
    a->aim_reached = false;
    a->aim_reach_ms = now_ms + a->cfg.settle_ms + (uint32_t)move_ms;
    a->aim_deadline_ms = now_ms + ttl_ms;
    send(a, id, "accepted", NULL);
}

void act_fire(act_t *a, uint32_t now_ms, uint32_t id, act_dev_t dev, uint32_t ms)
{
    // Trùng thật: id này đã được bắn (fire_id), chứ không phải trạng thái "reached" của aim cùng id.
    if (a->fire_id == id) {
        const act_recent_t *r = find_recent(a, id);
        if (r != NULL && (strcmp(r->st, "accepted") == 0 || strcmp(r->st, "done") == 0 ||
                          strcmp(r->st, "fault") == 0)) {
            resend(a, r);
            return;
        }
    }
    if (id < a->last_stop_id) {
        send(a, id, "rejected", "stopped");
        return;
    }
    if (!hb_ok(a, now_ms)) {
        send(a, id, "rejected", "hb");
        return;
    }
    if (a->aim_id != id || !a->aim_reached) {
        send(a, id, "rejected", "not reached");
        return;
    }
    if (ms < 1u) {
        ms = 1u;
    }
    if (ms > a->cfg.max_fire_ms) {
        ms = a->cfg.max_fire_ms;
    }
    fire_abort(a, "stopped");
    a->fire_active = true;
    a->fire_id = id;
    a->fire_dev = dev;
    a->fire_end_ms = now_ms + ms;
    a->io.set_dev(a->io.ctx, dev, true);
    send(a, id, "accepted", NULL);
}

void act_stop(act_t *a, uint32_t now_ms, uint32_t id)
{
    (void)now_ms;
    if (id > a->last_stop_id) {
        a->last_stop_id = id;
    }
    dev_off_all(a);
    fire_abort(a, "stopped");
    send(a, id, "done", NULL);
}

void act_tick(act_t *a, uint32_t now_ms)
{
    if (a->hb_seen && !hb_ok(a, now_ms)) {
        dev_off_all(a);
        fire_abort(a, "hb");
    }
    if (a->fire_active && time_ge(now_ms, a->fire_end_ms)) {
        a->io.set_dev(a->io.ctx, a->fire_dev, false);
        a->fire_active = false;
        send(a, a->fire_id, "done", NULL);
    }
    if (a->aim_active && !a->aim_reached) {
        if (time_ge(a->aim_deadline_ms, a->aim_reach_ms)) {  // tới nơi kịp ttl
            if (time_ge(now_ms, a->aim_reach_ms)) {
                a->aim_reached = true;
                send(a, a->aim_id, "reached", NULL);
            }
        } else if (time_ge(now_ms, a->aim_deadline_ms)) {
            a->aim_active = false;
            send(a, a->aim_id, "fault", "ttl");
        }
    }
}
