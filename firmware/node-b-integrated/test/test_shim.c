// Test lớp shim (core_shim.c) qua đúng API phẳng mà Python sẽ gọi.
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "probe_state.h"

void *ic_new(void);
void ic_free(void *h);
void ic_set_attached(void *h, int attached);
int ic_probe(void *h, int resource, int method, uint64_t now_ms);
int ic_pantilt_post(void *h, const char *body, size_t len, uint64_t now_ms);
int ic_laser_post(void *h, const char *body, size_t len, uint64_t now_ms);
int ic_watchdog(void *h, uint64_t now_ms);
int ic_pwm_started(void *h);
int ic_next_step(void *h, uint64_t now_ms, int *pan_us, int *tilt_us);
void ic_snapshot(void *h, int *flags, int *pan, int *tilt, int *tpan, int *ttilt);
size_t ic_pantilt_json(void *h, char *buf, size_t cap);
int ic_hb_timeout_ms(void);
int ic_limits(int *a, int *b, int *c, int *d, int *e, int *f, int *g);

#define POST(h, s, t) ic_pantilt_post(h, s, strlen(s), t)

int main(void)
{
    void *h = ic_new();
    int fl, pan, tilt, tp, tt;
    assert(h);
    ic_set_attached(h, 1);
    assert(POST(h, "ARM", 900) == 2);
    assert(ic_probe(h, PROBE_HB, PROBE_POST, 1000) == 68);
    assert(POST(h, "ARM", 1000) == 0);
    assert(POST(h, "HOME", 1000) == 0);
    assert(ic_pwm_started(h) == 1);
    ic_snapshot(h, &fl, &pan, &tilt, &tp, &tt);
    assert(fl & 8);
    assert(pan == 1500 && tilt == 1500);
    assert(POST(h, "1600,1500", 1000) == 0);
    assert(ic_next_step(h, 1020, &pan, &tilt) == 1);
    assert(pan == 1520);
    assert(ic_next_step(h, 1025, &pan, &tilt) == 0);
    assert(ic_laser_post(h, "ON", 2, 1030) == 0);
    ic_snapshot(h, &fl, &pan, &tilt, &tp, &tt);
    assert(fl & 16);
    assert(ic_watchdog(h, 1000 + 1500 + 1) == 1);
    ic_snapshot(h, &fl, &pan, &tilt, &tp, &tt);
    assert(!(fl & 8) && !(fl & 16));
    char buf[256];
    assert(ic_pantilt_json(h, buf, sizeof buf) > 0 && strstr(buf, "pan_us"));
    int l[7];
    assert(ic_limits(&l[0], &l[1], &l[2], &l[3], &l[4], &l[5], &l[6]) == 0);
    assert(l[0] == 500 && l[1] == 2500 && l[2] == 600 && l[3] == 2500 && l[4] == 1500 &&
           l[5] == 20 && l[6] == 20);
    assert(ic_hb_timeout_ms() == 1500);
    ic_free(h);
    puts("test_shim PASS");
    return 0;
}
