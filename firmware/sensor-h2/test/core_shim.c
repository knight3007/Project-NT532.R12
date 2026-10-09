// Lớp mỏng phẳng quanh lõi C (actuator, alarm, proto) để Python (ctypes) dùng mà không phải khai
// lại bố cục act_t. Chỉ phục vụ node giả trên máy (pi/src/nt532/net/fakenode.py); không vào firmware.
//
// Mọi hàm gọi lại (callback) chạy đồng bộ trong shim_act_*, ở luồng đang gọi.

#include <stdlib.h>
#include <string.h>

#include "actuator.h"
#include "alarm.h"
#include "proto.h"

typedef void (*shim_angles_fn)(void *ctx, float pan, float tilt);
typedef void (*shim_dev_fn)(void *ctx, int dev, int on);  // dev: 0 bơm, 1 laser
typedef void (*shim_status_fn)(void *ctx, uint32_t id, const char *st, float pan, float tilt, const char *err);

typedef struct {
    act_t act;
    shim_angles_fn angles;
    shim_dev_fn dev;
    shim_status_fn status;
    void *ctx;
} shim_act_t;

static void io_angles(void *p, float pan, float tilt)
{
    shim_act_t *s = p;
    s->angles(s->ctx, pan, tilt);
}

static void io_dev(void *p, act_dev_t dev, bool on)
{
    shim_act_t *s = p;
    s->dev(s->ctx, (int)dev, on ? 1 : 0);
}

static void io_status(void *p, uint32_t id, const char *st, float pan, float tilt, const char *err)
{
    shim_act_t *s = p;
    s->status(s->ctx, id, st, pan, tilt, err);
}

// lim: pan_min, pan_max, tilt_min, tilt_max; NULL thì dùng mặc định
void *shim_act_new(const float *lim, uint32_t hb_timeout_ms, uint32_t max_fire_ms, shim_angles_fn angles,
                   shim_dev_fn dev, shim_status_fn status, void *ctx)
{
    shim_act_t *s = calloc(1, sizeof *s);
    if (s == NULL) {
        return NULL;
    }
    s->angles = angles;
    s->dev = dev;
    s->status = status;
    s->ctx = ctx;
    act_config_t cfg;
    act_default_config(&cfg);
    if (lim != NULL) {
        cfg.pan_min = lim[0];
        cfg.pan_max = lim[1];
        cfg.tilt_min = lim[2];
        cfg.tilt_max = lim[3];
    }
    if (hb_timeout_ms != 0) {
        cfg.hb_timeout_ms = hb_timeout_ms;
    }
    if (max_fire_ms != 0) {
        cfg.max_fire_ms = max_fire_ms;
    }
    act_io_t io = {.set_angles = io_angles, .set_dev = io_dev, .send_status = io_status, .ctx = s};
    act_init(&s->act, &cfg, &io);
    act_all_off(&s->act);  // như act_task_start
    return s;
}

void shim_act_free(void *p) { free(p); }
void shim_act_aim(void *p, uint32_t now, uint32_t id, float pan, float tilt, uint32_t ttl)
{
    act_aim(&((shim_act_t *)p)->act, now, id, pan, tilt, ttl);
}
void shim_act_fire(void *p, uint32_t now, uint32_t id, int dev, uint32_t ms)
{
    act_fire(&((shim_act_t *)p)->act, now, id, dev == ACT_DEV_LASER ? ACT_DEV_LASER : ACT_DEV_PUMP, ms);
}
void shim_act_stop(void *p, uint32_t now, uint32_t id) { act_stop(&((shim_act_t *)p)->act, now, id); }
void shim_act_hb(void *p, uint32_t now) { act_hb(&((shim_act_t *)p)->act, now); }
void shim_act_tick(void *p, uint32_t now) { act_tick(&((shim_act_t *)p)->act, now); }
void shim_act_all_off(void *p) { act_all_off(&((shim_act_t *)p)->act); }

// --- alarm

void *shim_alarm_new(float temp_c, float gas, uint32_t warmup_ms)
{
    alarm_t *al = calloc(1, sizeof *al);
    if (al == NULL) {
        return NULL;
    }
    alarm_config_t cfg;
    alarm_default_config(&cfg);
    cfg.temp_c = temp_c;
    cfg.gas = gas;
    cfg.warmup_ms = warmup_ms;
    alarm_init(al, &cfg);
    return al;
}

void shim_alarm_free(void *p) { free(p); }
int shim_alarm_feed(void *p, uint32_t up_ms, float t, float g) { return (int)alarm_feed(p, up_ms, t, g); }

// --- proto: parse trả 1/0, kết quả qua con trỏ

int shim_parse_aim(const char *buf, size_t len, uint32_t *id, float *pan, float *tilt, uint32_t *ttl)
{
    proto_aim_t a;
    if (!proto_parse_aim(buf, len, &a)) {
        return 0;
    }
    *id = a.id;
    *pan = a.pan;
    *tilt = a.tilt;
    *ttl = a.ttl;
    return 1;
}

int shim_parse_fire(const char *buf, size_t len, uint32_t *id, int *dev, uint32_t *ms)
{
    proto_fire_t f;
    if (!proto_parse_fire(buf, len, &f)) {
        return 0;
    }
    *id = f.id;
    *dev = (int)f.dev;
    *ms = f.ms;
    return 1;
}

int shim_parse_stop(const char *buf, size_t len, uint32_t *id)
{
    proto_stop_t s;
    if (!proto_parse_stop(buf, len, &s)) {
        return 0;
    }
    *id = s.id;
    return 1;
}

// n: bộ đệm tối thiểu 8 byte
int shim_parse_alarm(const char *buf, size_t len, char *n, uint32_t *s)
{
    proto_alarm_t a;
    if (!proto_parse_alarm(buf, len, &a)) {
        return 0;
    }
    memcpy(n, a.n, sizeof a.n);
    *s = a.s;
    return 1;
}

// --- proto: tạo bản tin (bool thành int cho ctypes)

size_t shim_telemetry(char *buf, size_t cap, const char *n, uint32_t s, uint32_t up_s, float t, float g,
                      int has_h, float h)
{
    return proto_telemetry(buf, cap, n, s, up_s, t, g, has_h != 0, h);
}
size_t shim_alert(char *buf, size_t cap, const char *n, uint32_t s, const char *k, float t, float g)
{
    return proto_alert(buf, cap, n, s, k, t, g);
}
size_t shim_status(char *buf, size_t cap, uint32_t id, const char *st, float pan, float tilt, const char *err,
                   const char *n)
{
    return proto_status(buf, cap, id, st, pan, tilt, err, n);
}
size_t shim_alarm_msg(char *buf, size_t cap, const char *n, uint32_t s) { return proto_alarm(buf, cap, n, s); }
