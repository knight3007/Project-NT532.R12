// State machine chấp hành: xem actuator.h.

#include "actuator.h"

#include <math.h>
#include <string.h>

#define NEVER_FIRED UINT32_MAX  // fire_id ban đầu; chỉ coi là trùng khi recent cũng có trạng thái fire

static const char *const ST_NAME[] = {NULL, "accepted", "reached", "done", "rejected", "fault"};
static const char *const ERR_NAME[] = {NULL, "limits", "stopped", "hb", "ttl", "not reached"};

static bool time_ge(uint32_t now, uint32_t t)
{
    return (int32_t)(now - t) >= 0;
}

// ms từ now tới t theo đúng phép so của time_ge: 0 khi time_ge(now, t).
static uint32_t until(uint32_t now, uint32_t t)
{
    return time_ge(now, t) ? 0u : t - now;
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

static void emit(act_t *a, uint32_t id, uint8_t st, uint8_t err)
{
    a->io.send_status(a->io.ctx, id, ST_NAME[st], a->pan, a->tilt, ERR_NAME[err]);
}

static void send(act_t *a, uint32_t id, act_st_t st, act_err_t err)
{
    act_recent_t *r = &a->recent[a->recent_next];
    r->id = id;
    r->st = (uint8_t)st;
    r->err = (uint8_t)err;
    a->recent_next = (a->recent_next + 1u) % ACT_RECENT;
    emit(a, id, r->st, r->err);
}

// Trạng thái ghi gần nhất của id, NULL nếu chưa có.
static const act_recent_t *find_recent(const act_t *a, uint32_t id)
{
    for (unsigned i = 1; i <= ACT_RECENT; i++) {
        const act_recent_t *r = &a->recent[(a->recent_next + ACT_RECENT - i) % ACT_RECENT];
        if (r->st != ACT_ST_NONE && r->id == id) {
            return r;
        }
    }
    return NULL;
}

static void fire_abort(act_t *a, act_err_t err)
{
    if (a->fire_active) {
        a->io.set_dev(a->io.ctx, a->fire_dev, false);
        a->fire_active = false;
        send(a, a->fire_id, ACT_ST_FAULT, err);
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
    a->hb_lost = false;
}

void act_aim(act_t *a, uint32_t now_ms, uint32_t id, float pan, float tilt, uint32_t ttl_ms)
{
    const act_recent_t *dup = find_recent(a, id);
    if (dup != NULL) {
        emit(a, dup->id, dup->st, dup->err);  // gửi lại trạng thái đã ghi, không ghi thêm
        return;
    }
    if (id < a->last_stop_id) {
        send(a, id, ACT_ST_REJECTED, ACT_ERR_STOPPED);
        return;
    }
    // viết dạng phủ định để NaN cũng bị loại
    if (!(pan >= a->cfg.pan_min && pan <= a->cfg.pan_max && tilt >= a->cfg.tilt_min &&
          tilt <= a->cfg.tilt_max)) {
        send(a, id, ACT_ST_REJECTED, ACT_ERR_LIMITS);
        return;
    }
    fire_abort(a, ACT_ERR_STOPPED);

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
    send(a, id, ACT_ST_ACCEPTED, ACT_ERR_NONE);
}

void act_fire(act_t *a, uint32_t now_ms, uint32_t id, act_dev_t dev, uint32_t ms)
{
    // Trùng thật: id này đã được bắn (fire_id), chứ không phải trạng thái "reached" của aim cùng id.
    if (a->fire_id == id) {
        const act_recent_t *r = find_recent(a, id);
        if (r != NULL && (r->st == ACT_ST_ACCEPTED || r->st == ACT_ST_DONE || r->st == ACT_ST_FAULT)) {
            emit(a, r->id, r->st, r->err);
            return;
        }
    }
    if (id < a->last_stop_id) {
        send(a, id, ACT_ST_REJECTED, ACT_ERR_STOPPED);
        return;
    }
    if (!hb_ok(a, now_ms)) {
        send(a, id, ACT_ST_REJECTED, ACT_ERR_HB);
        return;
    }
    if (a->aim_id != id || !a->aim_reached) {
        send(a, id, ACT_ST_REJECTED, ACT_ERR_NOT_REACHED);
        return;
    }
    if (ms < 1u) {
        ms = 1u;
    }
    if (ms > a->cfg.max_fire_ms) {
        ms = a->cfg.max_fire_ms;
    }
    fire_abort(a, ACT_ERR_STOPPED);
    a->fire_active = true;
    a->fire_id = id;
    a->fire_dev = dev;
    a->fire_end_ms = now_ms + ms;
    a->io.set_dev(a->io.ctx, dev, true);
    send(a, id, ACT_ST_ACCEPTED, ACT_ERR_NONE);
}

void act_stop(act_t *a, uint32_t now_ms, uint32_t id)
{
    (void)now_ms;
    if (id > a->last_stop_id) {
        a->last_stop_id = id;
    }
    dev_off_all(a);  // luôn ghi, kể cả khi tưởng đã tắt
    fire_abort(a, ACT_ERR_STOPPED);
    send(a, id, ACT_ST_DONE, ACT_ERR_NONE);
}

void act_tick(act_t *a, uint32_t now_ms)
{
    // Một lần cho mỗi lần mất nhịp: sau đó chỉ act_fire bật lại được thiết bị, mà act_fire đòi hb_ok.
    if (a->hb_seen && !a->hb_lost && !hb_ok(a, now_ms)) {
        a->hb_lost = true;
        dev_off_all(a);
        fire_abort(a, ACT_ERR_HB);
    }
    if (a->fire_active && time_ge(now_ms, a->fire_end_ms)) {
        a->io.set_dev(a->io.ctx, a->fire_dev, false);
        a->fire_active = false;
        send(a, a->fire_id, ACT_ST_DONE, ACT_ERR_NONE);
    }
    if (a->aim_active && !a->aim_reached) {
        if (time_ge(a->aim_deadline_ms, a->aim_reach_ms)) {  // tới nơi kịp ttl
            if (time_ge(now_ms, a->aim_reach_ms)) {
                a->aim_reached = true;
                send(a, a->aim_id, ACT_ST_REACHED, ACT_ERR_NONE);
            }
        } else if (time_ge(now_ms, a->aim_deadline_ms)) {
            a->aim_active = false;
            send(a, a->aim_id, ACT_ST_FAULT, ACT_ERR_TTL);
        }
    }
}

// Mỗi hạn dưới đây dùng đúng điều kiện act_tick dùng, nên "chưa tới hạn" ở đây nghĩa là act_tick
// chưa làm gì: không bao giờ trả 0 cho một việc act_tick sẽ bỏ qua.
uint32_t act_next_ms(const act_t *a, uint32_t now_ms)
{
    uint32_t w = ACT_IDLE;
    if (a->fire_active) {
        w = until(now_ms, a->fire_end_ms);
        if (a->hb_seen && !a->hb_lost) {
            // hb_ok còn đúng tới hết hb_last + timeout, nên hiệu dưới đây >= 1
            uint32_t h = hb_ok(a, now_ms) ? a->hb_last_ms + a->cfg.hb_timeout_ms + 1u - now_ms : 0u;
            if (h < w) {
                w = h;
            }
        }
    }
    if (a->aim_active && !a->aim_reached) {
        uint32_t t = time_ge(a->aim_deadline_ms, a->aim_reach_ms) ? a->aim_reach_ms : a->aim_deadline_ms;
        uint32_t x = until(now_ms, t);
        if (x < w) {
            w = x;
        }
    }
    return w;
}
