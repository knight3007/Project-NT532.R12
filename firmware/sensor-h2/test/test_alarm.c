#include "alarm.h"
#include "check.h"

static alarm_t mk(void)
{
    alarm_config_t c;
    alarm_t al;
    alarm_default_config(&c);
    alarm_init(&al, &c);
    return al;
}

#define LATE 200000u

void test_alarm(void)
{
    alarm_config_t c;
    alarm_default_config(&c);
    CHECK(c.temp_c == 45.0f && c.gas == 600.0f && c.n_on == 3 && c.n_off == 5 && c.warmup_ms == 120000u);

    // 3 mẫu liên tiếp vượt -> RAISE đúng một lần
    alarm_t al = mk();
    CHECK_EQ(alarm_feed(&al, LATE, 50, 0), ALARM_NONE);
    CHECK_EQ(alarm_feed(&al, LATE, 50, 0), ALARM_NONE);
    CHECK_EQ(alarm_feed(&al, LATE, 50, 0), ALARM_RAISE);
    CHECK_EQ(alarm_feed(&al, LATE, 50, 0), ALARM_NONE);
    CHECK(al.active);
    // 4 mẫu dưới chưa hủy, mẫu 5 thì hủy
    for (int i = 0; i < 4; i++) CHECK_EQ(alarm_feed(&al, LATE, 20, 100), ALARM_NONE);
    CHECK_EQ(alarm_feed(&al, LATE, 20, 100), ALARM_CLEAR);
    CHECK_EQ(alarm_feed(&al, LATE, 20, 100), ALARM_NONE);
    CHECK(!al.active);

    // một mẫu vượt chen vào làm đếm hủy làm lại
    al = mk();
    for (int i = 0; i < 3; i++) alarm_feed(&al, LATE, 50, 0);
    for (int i = 0; i < 4; i++) CHECK_EQ(alarm_feed(&al, LATE, 20, 0), ALARM_NONE);
    CHECK_EQ(alarm_feed(&al, LATE, 50, 0), ALARM_NONE);
    for (int i = 0; i < 4; i++) CHECK_EQ(alarm_feed(&al, LATE, 20, 0), ALARM_NONE);
    CHECK(al.active);
    CHECK_EQ(alarm_feed(&al, LATE, 20, 0), ALARM_CLEAR);

    // xen kẽ vượt/dưới không bao giờ báo
    al = mk();
    for (int i = 0; i < 100; i++) CHECK_EQ(alarm_feed(&al, LATE, (i & 1) ? 20.0f : 50.0f, 0), ALARM_NONE);
    CHECK(!al.active);

    // 2 vượt rồi 1 dưới rồi 2 vượt: không báo
    al = mk();
    alarm_feed(&al, LATE, 50, 0); alarm_feed(&al, LATE, 50, 0);
    alarm_feed(&al, LATE, 20, 0);
    alarm_feed(&al, LATE, 50, 0);
    CHECK_EQ(alarm_feed(&al, LATE, 50, 0), ALARM_NONE);

    // gas vượt báo động; ngưỡng là >=
    al = mk();
    alarm_feed(&al, LATE, 20, 600); alarm_feed(&al, LATE, 20, 700);
    CHECK_EQ(alarm_feed(&al, LATE, 20, 900), ALARM_RAISE);
    al = mk();
    for (int i = 0; i < 2; i++) alarm_feed(&al, LATE, 45.0f, 0);
    CHECK_EQ(alarm_feed(&al, LATE, 45.0f, 0), ALARM_RAISE);
    al = mk();
    for (int i = 0; i < 10; i++) CHECK_EQ(alarm_feed(&al, LATE, 44.9f, 599.9f), ALARM_NONE);

    // warmup: bỏ gas, vẫn xét nhiệt
    al = mk();
    for (int i = 0; i < 10; i++) CHECK_EQ(alarm_feed(&al, 1000u, 20, 5000), ALARM_NONE);
    CHECK(!al.active);
    alarm_feed(&al, 119999u, 50, 0); alarm_feed(&al, 119999u, 50, 0);
    CHECK_EQ(alarm_feed(&al, 119999u, 50, 0), ALARM_RAISE);
    // trong warmup gas cao không giữ báo động: coi là dưới
    for (int i = 0; i < 4; i++) CHECK_EQ(alarm_feed(&al, 5000u, 20, 5000), ALARM_NONE);
    CHECK_EQ(alarm_feed(&al, 5000u, 20, 5000), ALARM_CLEAR);
    // hết warmup đúng 120000: gas được tính
    al = mk();
    alarm_feed(&al, 120000u, 20, 700); alarm_feed(&al, 120000u, 20, 700);
    CHECK_EQ(alarm_feed(&al, 120000u, 20, 700), ALARM_RAISE);

    // báo lại sau khi hủy
    for (int i = 0; i < 5; i++) alarm_feed(&al, LATE, 20, 0);
    CHECK(!al.active);
    alarm_feed(&al, LATE, 50, 0); alarm_feed(&al, LATE, 50, 0);
    CHECK_EQ(alarm_feed(&al, LATE, 50, 0), ALARM_RAISE);

    // mẫu NaN bị bỏ qua, không làm đổi trạng thái
    al = mk();
    alarm_feed(&al, LATE, 50, 0); alarm_feed(&al, LATE, 50, 0);
    CHECK_EQ(alarm_feed(&al, LATE, (float)__builtin_nan(""), 0), ALARM_NONE);
    CHECK_EQ(alarm_feed(&al, LATE, 50, 0), ALARM_RAISE);

    // cấu hình tùy chỉnh
    c.n_on = 1; c.n_off = 1;
    alarm_init(&al, &c);
    CHECK_EQ(alarm_feed(&al, LATE, 50, 0), ALARM_RAISE);
    CHECK_EQ(alarm_feed(&al, LATE, 10, 0), ALARM_CLEAR);
}
