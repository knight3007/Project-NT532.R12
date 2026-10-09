#include <math.h>
#include <stdint.h>

#include "actuator.h"
#include "check.h"

#define MAXS 64

typedef struct {
    uint32_t id;
    char st[16], err[16];
    float pan, tilt;
} rec_t;

typedef struct {
    int n_angles;
    float pan, tilt;
    bool dev[2];
    int on_calls[2], off_calls[2];
    int n;
    rec_t s[MAXS];
} fake_t;

static void f_angles(void *c, float pan, float tilt)
{
    fake_t *f = c;
    f->n_angles++;
    f->pan = pan;
    f->tilt = tilt;
}

static void f_dev(void *c, act_dev_t d, bool on)
{
    fake_t *f = c;
    f->dev[d] = on;
    if (on) f->on_calls[d]++; else f->off_calls[d]++;
}

static void f_status(void *c, uint32_t id, const char *st, float pan, float tilt, const char *err)
{
    fake_t *f = c;
    CHECK(f->n < MAXS);
    if (f->n >= MAXS) return;
    rec_t *r = &f->s[f->n++];
    r->id = id;
    strncpy(r->st, st, sizeof r->st - 1);
    r->err[0] = 0;
    if (err) strncpy(r->err, err, sizeof r->err - 1);
    r->pan = pan;
    r->tilt = tilt;
}

typedef struct { fake_t f; act_t a; } rig_t;

static void rig_init(rig_t *r)
{
    act_config_t c;
    memset(r, 0, sizeof *r);
    act_default_config(&c);
    act_io_t io = {f_angles, f_dev, f_status, &r->f};
    act_init(&r->a, &c, &io);
}

// Kiểm trạng thái thứ i từ cuối (0 = cuối cùng).
static void last(rig_t *r, int back, uint32_t id, const char *st, const char *err)
{
    CHECK(r->f.n > back);
    if (r->f.n <= back) return;
    rec_t *s = &r->f.s[r->f.n - 1 - back];
    CHECK_EQ(s->id, id);
    CHECK_STR(s->st, st);
    CHECK_STR(s->err, err ? err : "");
}

// Aim + tick tới reached, có hb. Trả thời điểm đã tới.
static uint32_t aim_reach(rig_t *r, uint32_t t, uint32_t id, float pan, float tilt)
{
    act_hb(&r->a, t);
    act_aim(&r->a, t, id, pan, tilt, 3000);
    t += 1000;
    act_hb(&r->a, t);
    act_tick(&r->a, t);
    return t;
}

static void t_init(void)
{
    rig_t r;
    memset(&r, 0xAA, sizeof r);  // rác ban đầu
    act_config_t c;
    act_default_config(&c);
    CHECK(c.pan_min == -50 && c.pan_max == 50 && c.tilt_min == -35 && c.tilt_max == 45);
    CHECK(c.deg_per_s == 150 && c.settle_ms == 150 && c.hb_timeout_ms == 1500 && c.max_fire_ms == 5000);
    fake_t f;
    memset(&f, 0, sizeof f);
    f.dev[0] = f.dev[1] = true;
    act_io_t io = {f_angles, f_dev, f_status, &f};
    act_init(&r.a, &c, &io);
    CHECK(!f.dev[0] && !f.dev[1]);
    CHECK_EQ(f.off_calls[0], 1);
    CHECK_EQ(f.off_calls[1], 1);
    CHECK_EQ(f.n_angles, 1);
    CHECK(f.pan == 0 && f.tilt == 0);
    CHECK_EQ(f.n, 0);
}

static void t_aim_basic(void)
{
    rig_t r;
    rig_init(&r);
    act_aim(&r.a, 1000, 1, 30, -10, 2000);
    CHECK_EQ(r.f.n, 1);
    last(&r, 0, 1, "accepted", NULL);
    CHECK(r.f.pan == 30 && r.f.tilt == -10);
    CHECK(r.f.s[0].pan == 30 && r.f.s[0].tilt == -10);
    // 30 độ / 150 = 200 ms + settle 150 = 350
    act_tick(&r.a, 1349);
    CHECK_EQ(r.f.n, 1);
    act_tick(&r.a, 1350);
    CHECK_EQ(r.f.n, 2);
    last(&r, 0, 1, "reached", NULL);
    act_tick(&r.a, 1400);
    act_tick(&r.a, 9000);
    CHECK_EQ(r.f.n, 2);  // chỉ một lần, không ttl sau reached
    // aim kế tiếp tính theo góc hiện tại: từ (30,-10) tới (30,-40)? ngoài limit; tới (-20,-10) = 50 độ
    act_aim(&r.a, 10000, 2, -20, -10, 2000);
    act_tick(&r.a, 10000 + 150 + 333);
    CHECK_EQ(r.f.n, 3);
    act_tick(&r.a, 10000 + 150 + 334);
    last(&r, 0, 2, "reached", NULL);
}

static void t_limits(void)
{
    rig_t r;
    rig_init(&r);
    int na = r.f.n_angles;
    act_aim(&r.a, 0, 1, 50.1f, 0, 1000);
    last(&r, 0, 1, "rejected", "limits");
    act_aim(&r.a, 0, 2, -50.1f, 0, 1000);
    last(&r, 0, 2, "rejected", "limits");
    act_aim(&r.a, 0, 3, 0, 45.1f, 1000);
    last(&r, 0, 3, "rejected", "limits");
    act_aim(&r.a, 0, 4, 0, -35.1f, 1000);
    last(&r, 0, 4, "rejected", "limits");
    act_aim(&r.a, 0, 5, (float)NAN, 0, 1000);
    last(&r, 0, 5, "rejected", "limits");
    act_aim(&r.a, 0, 6, 0, (float)INFINITY, 1000);
    last(&r, 0, 6, "rejected", "limits");
    CHECK_EQ(r.f.n_angles, na);  // không đặt góc khi từ chối
    act_tick(&r.a, 5000);
    CHECK_EQ(r.f.n, 6);
    // biên chấp nhận
    act_aim(&r.a, 0, 7, 50, 45, 1000);
    last(&r, 0, 7, "accepted", NULL);
    act_aim(&r.a, 0, 8, -50, -35, 1000);
    last(&r, 0, 8, "accepted", NULL);
}

static void t_ttl(void)
{
    rig_t r;
    rig_init(&r);
    act_hb(&r.a, 1000);
    act_aim(&r.a, 1000, 1, 30, 0, 100);  // cần 350 ms nhưng ttl 100
    act_tick(&r.a, 1099);
    CHECK_EQ(r.f.n, 1);
    act_tick(&r.a, 1100);
    CHECK_EQ(r.f.n, 2);
    last(&r, 0, 1, "fault", "ttl");
    act_tick(&r.a, 1200);
    act_tick(&r.a, 1500);
    CHECK_EQ(r.f.n, 2);  // không báo reached muộn
    act_fire(&r.a, 1000, 1, ACT_DEV_LASER, 100);
    last(&r, 0, 1, "rejected", "not reached");
    CHECK_EQ(r.f.on_calls[1], 0);
    // ttl vừa đủ: reach == deadline thì vẫn reached
    rig_init(&r);
    act_aim(&r.a, 0, 1, 30, 0, 350);
    act_tick(&r.a, 350);
    last(&r, 0, 1, "reached", NULL);
    CHECK_EQ(r.f.n, 2);
}

static void t_dup(void)
{
    rig_t r;
    rig_init(&r);
    act_hb(&r.a, 0);
    act_aim(&r.a, 0, 1, 30, 0, 2000);
    // trùng khi mới accepted
    act_aim(&r.a, 10, 1, 40, 0, 2000);
    CHECK_EQ(r.f.n, 2);
    last(&r, 0, 1, "accepted", NULL);
    CHECK_EQ(r.f.n_angles, 2);  // init + một lần
    CHECK(r.f.pan == 30);
    act_tick(&r.a, 400);
    last(&r, 0, 1, "reached", NULL);
    int n = r.f.n;
    act_aim(&r.a, 410, 1, -40, 0, 2000);
    CHECK_EQ(r.f.n, n + 1);
    last(&r, 0, 1, "reached", NULL);
    CHECK_EQ(r.f.n_angles, 2);
    // fire thật
    act_hb(&r.a, 420);
    act_fire(&r.a, 420, 1, ACT_DEV_PUMP, 1000);
    last(&r, 0, 1, "accepted", NULL);
    CHECK_EQ(r.f.on_calls[0], 1);
    n = r.f.n;
    act_fire(&r.a, 430, 1, ACT_DEV_PUMP, 1000);  // gửi lại
    CHECK_EQ(r.f.n, n + 1);
    last(&r, 0, 1, "accepted", NULL);
    CHECK_EQ(r.f.on_calls[0], 1);
    act_hb(&r.a, 1400);
    act_tick(&r.a, 1420);
    last(&r, 0, 1, "done", NULL);
    CHECK(!r.f.dev[0]);
    n = r.f.n;
    act_fire(&r.a, 1500, 1, ACT_DEV_PUMP, 1000);  // gửi lại sau done: trả done, không bật lại
    CHECK_EQ(r.f.n, n + 1);
    last(&r, 0, 1, "done", NULL);
    CHECK_EQ(r.f.on_calls[0], 1);
    CHECK(!r.f.dev[0]);
    act_aim(&r.a, 1500, 1, 0, 0, 100);  // aim trùng id sau fire
    last(&r, 0, 1, "done", NULL);
    CHECK_EQ(r.f.n_angles, 2);
    // dup stop: gửi lại done và vẫn tắt hết
    act_stop(&r.a, 1600, 5);
    last(&r, 0, 5, "done", NULL);
    int offs = r.f.off_calls[0] + r.f.off_calls[1];
    r.f.dev[1] = true;  // giả lập thiết bị bị bật ngoài ý muốn
    act_stop(&r.a, 1610, 5);
    last(&r, 0, 5, "done", NULL);
    CHECK(!r.f.dev[1]);
    CHECK(r.f.off_calls[0] + r.f.off_calls[1] >= offs + 2);
}

static void t_fire_rules(void)
{
    rig_t r;
    rig_init(&r);
    // chưa có hb
    act_aim(&r.a, 0, 1, 10, 0, 2000);
    act_tick(&r.a, 500);
    act_fire(&r.a, 500, 1, ACT_DEV_LASER, 100);
    last(&r, 0, 1, "rejected", "hb");
    CHECK_EQ(r.f.on_calls[1], 0);
    // có hb nhưng aim chưa reached
    rig_init(&r);
    act_hb(&r.a, 0);
    act_aim(&r.a, 0, 1, 30, 0, 2000);
    act_fire(&r.a, 100, 1, ACT_DEV_LASER, 100);
    last(&r, 0, 1, "rejected", "not reached");
    CHECK_EQ(r.f.on_calls[1], 0);
    act_tick(&r.a, 400);
    last(&r, 0, 1, "reached", NULL);
    // sai id
    act_hb(&r.a, 400);
    act_fire(&r.a, 400, 2, ACT_DEV_LASER, 100);
    last(&r, 0, 2, "rejected", "not reached");
    CHECK_EQ(r.f.on_calls[1], 0);
    // fire không có aim nào (id 0)
    rig_init(&r);
    act_hb(&r.a, 0);
    act_fire(&r.a, 0, 0, ACT_DEV_PUMP, 100);
    last(&r, 0, 0, "rejected", "not reached");
    // aim bị thay bởi aim khác: fire id cũ bị từ chối
    rig_init(&r);
    uint32_t t = aim_reach(&r, 0, 1, 10, 0);
    act_aim(&r.a, t, 2, 20, 0, 3000);
    act_fire(&r.a, t, 1, ACT_DEV_PUMP, 100);
    last(&r, 0, 1, "rejected", "not reached");
    act_fire(&r.a, t, 2, ACT_DEV_PUMP, 100);  // aim 2 chưa reached
    last(&r, 0, 2, "rejected", "not reached");
    CHECK_EQ(r.f.on_calls[0], 0);
}

static void t_fire_done(void)
{
    rig_t r;
    rig_init(&r);
    uint32_t t = aim_reach(&r, 0, 7, 10, 5);
    act_fire(&r.a, t, 7, ACT_DEV_LASER, 800);
    last(&r, 0, 7, "accepted", NULL);
    CHECK(r.f.dev[1] && !r.f.dev[0]);
    CHECK(r.a.fire_active);
    act_hb(&r.a, t + 500);
    act_tick(&r.a, t + 799);
    CHECK(r.f.dev[1]);
    CHECK_EQ(r.f.n, 3);
    act_tick(&r.a, t + 800);
    CHECK(!r.f.dev[1]);
    last(&r, 0, 7, "done", NULL);
    CHECK_EQ(r.f.n, 4);
    act_tick(&r.a, t + 900);
    CHECK_EQ(r.f.n, 4);
    CHECK(r.f.s[3].pan == 10 && r.f.s[3].tilt == 5);
    // bơm
    rig_init(&r);
    t = aim_reach(&r, 0, 8, 0, 0);
    act_fire(&r.a, t, 8, ACT_DEV_PUMP, 300);
    CHECK(r.f.dev[0] && !r.f.dev[1]);
    act_tick(&r.a, t + 300);
    CHECK(!r.f.dev[0]);
}

static void t_stop(void)
{
    rig_t r;
    rig_init(&r);
    uint32_t t = aim_reach(&r, 0, 5, 10, 0);
    act_fire(&r.a, t, 5, ACT_DEV_PUMP, 3000);
    CHECK(r.f.dev[0]);
    act_stop(&r.a, t + 10, 6);
    CHECK(!r.f.dev[0] && !r.f.dev[1]);
    CHECK(!r.a.fire_active);
    last(&r, 1, 5, "fault", "stopped");
    last(&r, 0, 6, "done", NULL);
    act_tick(&r.a, t + 3000);  // không có done muộn
    CHECK_EQ(r.f.n, 5);
    // đua: aim 5 đã reached, stop 6, rồi fire 5 tới muộn
    rig_init(&r);
    t = aim_reach(&r, 0, 5, 10, 0);
    act_stop(&r.a, t, 6);
    last(&r, 0, 6, "done", NULL);
    act_hb(&r.a, t + 1);
    act_fire(&r.a, t + 1, 5, ACT_DEV_LASER, 1000);
    last(&r, 0, 5, "rejected", "stopped");
    act_fire(&r.a, t + 1, 5, ACT_DEV_PUMP, 1000);
    last(&r, 0, 5, "rejected", "stopped");
    CHECK_EQ(r.f.on_calls[0], 0);
    CHECK_EQ(r.f.on_calls[1], 0);
    // aim id nhỏ hơn stop
    act_aim(&r.a, t + 2, 4, 0, 0, 1000);
    last(&r, 0, 4, "rejected", "stopped");
    int na = r.f.n_angles;
    act_aim(&r.a, t + 2, 3, 20, 0, 1000);
    last(&r, 0, 3, "rejected", "stopped");
    CHECK_EQ(r.f.n_angles, na);
    // id bằng stop id: chỉ id < last_stop bị từ chối; 6 đã có trong recent nên thành trùng
    act_aim(&r.a, t + 2, 7, 20, 0, 1000);
    last(&r, 0, 7, "accepted", NULL);
    CHECK_EQ(r.f.n_angles, na + 1);
    // stop cũ hơn không hạ last_stop_id
    act_stop(&r.a, t + 3, 2);
    CHECK_EQ(r.a.last_stop_id, 6);
    act_aim(&r.a, t + 4, 5, 0, 0, 1000);  // 5 < 6: trùng recent (đã rejected) -> gửi lại rejected
    last(&r, 0, 5, "rejected", "stopped");
    // aim mới sau stop chạy được tới fire
    t = t + 5000;
    act_hb(&r.a, t);
    act_tick(&r.a, t);
    act_aim(&r.a, t, 9, 5, 5, 3000);
    act_tick(&r.a, t + 500);
    last(&r, 0, 9, "reached", NULL);
    act_hb(&r.a, t + 500);
    act_fire(&r.a, t + 500, 9, ACT_DEV_LASER, 100);
    last(&r, 0, 9, "accepted", NULL);
    CHECK(r.f.dev[1]);
    // stop không có fire chỉ gửi done
    rig_init(&r);
    act_stop(&r.a, 0, 3);
    CHECK_EQ(r.f.n, 1);
    last(&r, 0, 3, "done", NULL);
}

static void t_hb(void)
{
    rig_t r;
    rig_init(&r);
    uint32_t t = aim_reach(&r, 0, 1, 10, 0);  // hb tại 1000
    act_fire(&r.a, t + 1500, 1, ACT_DEV_LASER, 3000);  // đúng 1500 ms: còn hạn
    last(&r, 0, 1, "accepted", NULL);
    // mất hb khi đang bắn
    act_tick(&r.a, t + 1500);
    CHECK(r.f.dev[1]);  // đúng ngưỡng chưa quá
    CHECK_EQ(r.f.n, 3);
    act_tick(&r.a, t + 1501);
    CHECK(!r.f.dev[1] && !r.f.dev[0]);
    last(&r, 0, 1, "fault", "hb");
    CHECK_EQ(r.f.n, 4);
    for (uint32_t i = 1; i < 50; i++) act_tick(&r.a, t + 1501 + i * 20);
    act_tick(&r.a, t + 5000);  // kể cả khi tới hạn fire
    CHECK_EQ(r.f.n, 4);  // chỉ một fault hb
    // hb lại thì fire mới ok sau aim mới
    act_hb(&r.a, t + 6000);
    act_aim(&r.a, t + 6000, 2, 0, 0, 2000);
    act_tick(&r.a, t + 6500);
    act_fire(&r.a, t + 6500, 2, ACT_DEV_PUMP, 100);
    last(&r, 0, 2, "accepted", NULL);
    // hb hết hạn trước fire
    rig_init(&r);
    t = aim_reach(&r, 0, 1, 10, 0);
    act_fire(&r.a, t + 1501, 1, ACT_DEV_PUMP, 100);
    last(&r, 0, 1, "rejected", "hb");
    CHECK_EQ(r.f.on_calls[0], 0);
    // hb vẫn đang giữ
    rig_init(&r);
    t = aim_reach(&r, 0, 1, 10, 0);
    for (int i = 1; i <= 10; i++) { act_hb(&r.a, t + (uint32_t)i * 500); act_tick(&r.a, t + (uint32_t)i * 500); }
    act_fire(&r.a, t + 5000, 1, ACT_DEV_PUMP, 100);
    last(&r, 0, 1, "accepted", NULL);
    // chưa từng có hb thì tick không làm gì
    rig_init(&r);
    act_tick(&r.a, 100000);
    CHECK_EQ(r.f.n, 0);
}

static void t_clamp(void)
{
    rig_t r;
    rig_init(&r);
    uint32_t t = aim_reach(&r, 0, 1, 0, 0);
    act_fire(&r.a, t, 1, ACT_DEV_LASER, 0);  // ms=0 -> 1
    CHECK_EQ(r.a.fire_end_ms, t + 1);
    act_tick(&r.a, t + 1);
    last(&r, 0, 1, "done", NULL);
    rig_init(&r);
    t = aim_reach(&r, 0, 1, 0, 0);
    act_fire(&r.a, t, 1, ACT_DEV_PUMP, 99999);
    CHECK_EQ(r.a.fire_end_ms, t + 5000);
    act_hb(&r.a, t + 4000);
    act_tick(&r.a, t + 4999);
    CHECK(r.f.dev[0]);
    act_tick(&r.a, t + 5000);
    CHECK(!r.f.dev[0]);
    last(&r, 0, 1, "done", NULL);
    rig_init(&r);
    t = aim_reach(&r, 0, 1, 0, 0);
    act_fire(&r.a, t, 1, ACT_DEV_PUMP, UINT32_MAX);
    CHECK_EQ(r.a.fire_end_ms, t + 5000);
}

static void t_replace(void)
{
    rig_t r;
    rig_init(&r);
    // aim mới trong lúc đang bắn: tắt và fault stopped cho fire
    uint32_t t = aim_reach(&r, 0, 1, 10, 0);
    act_fire(&r.a, t, 1, ACT_DEV_PUMP, 3000);
    act_aim(&r.a, t + 50, 2, 20, 0, 3000);
    CHECK(!r.f.dev[0]);
    CHECK(!r.a.fire_active);
    last(&r, 1, 1, "fault", "stopped");
    last(&r, 0, 2, "accepted", NULL);
    CHECK_EQ(r.f.n_angles, 3);
    act_tick(&r.a, t + 3000);
    CHECK(r.f.s[r.f.n - 1].err[0] == 0 || strcmp(r.f.s[r.f.n - 1].st, "reached") == 0);
    // fire thứ hai thay fire thứ nhất (nhánh phòng thủ: ép trạng thái để id khác vẫn "reached")
    rig_init(&r);
    t = aim_reach(&r, 0, 1, 0, 0);
    act_fire(&r.a, t, 1, ACT_DEV_PUMP, 3000);
    r.a.aim_id = 2;  // giả lập aim 2 đã reached mà không đi qua act_aim
    act_fire(&r.a, t + 10, 2, ACT_DEV_LASER, 100);
    CHECK(!r.f.dev[0] && r.f.dev[1]);
    last(&r, 1, 1, "fault", "stopped");
    last(&r, 0, 2, "accepted", NULL);
    CHECK_EQ(r.a.fire_id, 2);
    act_tick(&r.a, t + 110);
    last(&r, 0, 2, "done", NULL);
    CHECK(!r.f.dev[1]);
}

static void t_wrap(void)
{
    rig_t r;
    rig_init(&r);
    uint32_t t0 = UINT32_MAX - 200;
    act_hb(&r.a, t0);
    act_aim(&r.a, t0, 1, 30, 0, 2000);  // reach = t0+350 vượt vòng
    act_tick(&r.a, t0 + 349);
    CHECK_EQ(r.f.n, 1);
    act_hb(&r.a, t0 + 300);
    act_tick(&r.a, t0 + 350);
    last(&r, 0, 1, "reached", NULL);
    act_hb(&r.a, t0 + 400);
    act_fire(&r.a, t0 + 400, 1, ACT_DEV_LASER, 500);  // end = t0+900 đã qua vòng
    CHECK(r.f.dev[1]);
    act_hb(&r.a, t0 + 700);
    act_tick(&r.a, t0 + 899);
    CHECK(r.f.dev[1]);
    act_tick(&r.a, t0 + 900);
    CHECK(!r.f.dev[1]);
    last(&r, 0, 1, "done", NULL);
    // hb hết hạn qua vòng
    act_aim(&r.a, t0 + 1000, 2, 30, 0, 2000);
    act_tick(&r.a, t0 + 1400);
    last(&r, 0, 2, "reached", NULL);
    act_fire(&r.a, t0 + 700 + 1501, 2, ACT_DEV_PUMP, 100);
    last(&r, 0, 2, "rejected", "hb");
    // ttl qua vòng
    rig_init(&r);
    act_aim(&r.a, UINT32_MAX - 10, 1, 40, 0, 100);
    act_tick(&r.a, 80);
    CHECK_EQ(r.f.n, 1);
    act_tick(&r.a, 89);
    last(&r, 0, 1, "fault", "ttl");
}

static void t_alloff(void)
{
    rig_t r;
    rig_init(&r);
    uint32_t t = aim_reach(&r, 0, 1, 0, 0);
    act_fire(&r.a, t, 1, ACT_DEV_LASER, 1000);
    int n = r.f.n;
    act_all_off(&r.a);
    CHECK(!r.f.dev[0] && !r.f.dev[1]);
    CHECK(!r.a.fire_active);
    CHECK_EQ(r.f.n, n);
    act_tick(&r.a, t + 1000);
    CHECK_EQ(r.f.n, n);
}

static void t_ring(void)
{
    // ring chỉ nhớ ACT_RECENT lệnh; id cũ hơn bị đẩy ra thì được xử lý như lệnh mới
    rig_t r;
    rig_init(&r);
    for (uint32_t i = 1; i <= 20; i++) act_stop(&r.a, 0, i);
    int n = r.f.n;
    act_stop(&r.a, 0, 20);
    CHECK_EQ(r.f.n, n + 1);
    last(&r, 0, 20, "done", NULL);
    act_aim(&r.a, 0, 19, 0, 0, 100);  // còn trong ring: trùng, gửi lại done
    last(&r, 0, 19, "done", NULL);
    act_aim(&r.a, 0, 3, 0, 0, 100);  // đã bị đẩy ra: xử lý như lệnh mới, nhưng < last_stop
    last(&r, 0, 3, "rejected", "stopped");
}

void test_actuator(void)
{
    t_init();
    t_aim_basic();
    t_limits();
    t_ttl();
    t_dup();
    t_fire_rules();
    t_fire_done();
    t_stop();
    t_hb();
    t_clamp();
    t_replace();
    t_wrap();
    t_alloff();
    t_ring();
}
