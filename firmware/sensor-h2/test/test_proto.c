#include <math.h>
#include <stdint.h>
#include <string.h>

#include "check.h"
#include "proto.h"

#define S(x) x, strlen(x)

static bool aim(const char *j, proto_aim_t *o) { return proto_parse_aim(S(j), o); }
static bool fire(const char *j, proto_fire_t *o) { return proto_parse_fire(S(j), o); }
static bool stop(const char *j, proto_stop_t *o) { return proto_parse_stop(S(j), o); }
static bool alarm_(const char *j, proto_alarm_t *o) { return proto_parse_alarm(S(j), o); }

static void p_aim(void)
{
    proto_aim_t a;
    CHECK(aim("{\"id\":42,\"pan\":31.5,\"tilt\":-7.25,\"ttl\":1500}", &a));
    CHECK(a.id == 42 && a.pan == 31.5f && a.tilt == -7.25f && a.ttl == 1500);
    // khoảng trắng, đổi thứ tự key, số nguyên cho float, số mũ, key lạ
    CHECK(aim(" \t\r\n{ \"ttl\" : 1500.0 , \"tilt\":-7 ,\"pan\" : 3e1, \"id\":0, \"x\":true,\"y\":null,\"z\":\"s\" } \n", &a));
    CHECK(a.id == 0 && a.pan == 30 && a.tilt == -7 && a.ttl == 1500);
    CHECK(aim("{\"id\":1,\"pan\":-0.0,\"tilt\":1E+1,\"ttl\":1.5e3}", &a));
    CHECK(a.tilt == 10 && a.ttl == 1500);
    CHECK(aim("{\"id\":4294967295,\"pan\":0,\"tilt\":0,\"ttl\":4294967295}", &a));
    CHECK(a.id == UINT32_MAX && a.ttl == UINT32_MAX);
    CHECK(aim("{\"id\":1,\"id\":2,\"pan\":0,\"tilt\":0,\"ttl\":5}", &a) && a.id == 2);  // lần cuối thắng
    // không có NUL kết thúc: chỉ dùng len
    const char raw[] = "{\"id\":7,\"pan\":1,\"tilt\":2,\"ttl\":3}9999";
    CHECK(proto_parse_aim(raw, sizeof raw - 1 - 4, &a) && a.id == 7);
    CHECK(!proto_parse_aim(raw, sizeof raw - 1, &a));  // rác sau object
    CHECK(!proto_parse_aim(raw, sizeof raw - 1 - 5, &a));  // cụt giữa object

    // từ chối
    CHECK(!aim("{\"id\":1,\"pan\":0,\"tilt\":0}", &a));                       // thiếu ttl
    CHECK(!aim("{\"pan\":0,\"tilt\":0,\"ttl\":5}", &a));                      // thiếu id
    CHECK(!aim("{\"id\":\"1\",\"pan\":0,\"tilt\":0,\"ttl\":5}", &a));         // chuỗi thay số
    CHECK(!aim("{\"id\":1,\"pan\":\"0\",\"tilt\":0,\"ttl\":5}", &a));
    CHECK(!aim("{\"id\":true,\"pan\":0,\"tilt\":0,\"ttl\":5}", &a));
    CHECK(!aim("{\"id\":null,\"pan\":0,\"tilt\":0,\"ttl\":5}", &a));
    CHECK(!aim("{\"id\":-1,\"pan\":0,\"tilt\":0,\"ttl\":5}", &a));
    CHECK(!aim("{\"id\":1,\"pan\":0,\"tilt\":0,\"ttl\":-5}", &a));
    CHECK(!aim("{\"id\":4294967296,\"pan\":0,\"tilt\":0,\"ttl\":5}", &a));
    CHECK(!aim("{\"id\":1,\"pan\":0,\"tilt\":0,\"ttl\":4294967296}", &a));
    CHECK(!aim("{\"id\":1.5,\"pan\":0,\"tilt\":0,\"ttl\":5}", &a));
    CHECK(!aim("{\"id\":1,\"pan\":0,\"tilt\":0,\"ttl\":5.2}", &a));
    CHECK(!aim("{\"id\":1,\"pan\":1e999,\"tilt\":0,\"ttl\":5}", &a));         // inf
    CHECK(!aim("{\"id\":1,\"pan\":1e39,\"tilt\":0,\"ttl\":5}", &a));          // vượt float
    CHECK(!aim("{\"id\":1,\"pan\":NaN,\"tilt\":0,\"ttl\":5}", &a));
    CHECK(!aim("{\"id\":1,\"pan\":0,\"tilt\":0,\"ttl\":5", &a));              // thiếu }
    CHECK(!aim("{\"id\":1,\"pan\":0,\"tilt\":0,\"ttl\":", &a));
    CHECK(!aim("{\"id\":1,\"pan\":0,\"tilt\":0,\"ttl\":5,}", &a));            // phẩy thừa
    CHECK(!aim("{\"id\":1 \"pan\":0,\"tilt\":0,\"ttl\":5}", &a));             // thiếu phẩy
    CHECK(!aim("{id:1,\"pan\":0,\"tilt\":0,\"ttl\":5}", &a));                 // key không ngoặc
    CHECK(!aim("{\"id\":1,\"pan\":0,\"tilt\":0,\"ttl\":5} x", &a));
    CHECK(!aim("{\"id\":1,\"pan\":0,\"tilt\":0,\"ttl\":5}{}", &a));
    CHECK(!aim("{\"id\":1,\"pan\":+1,\"tilt\":0,\"ttl\":5}", &a));
    CHECK(!aim("{\"id\":1,\"pan\":01,\"tilt\":0,\"ttl\":5}", &a));
    CHECK(!aim("{\"id\":1,\"pan\":1.,\"tilt\":0,\"ttl\":5}", &a));
    CHECK(!aim("{\"id\":1,\"pan\":.5,\"tilt\":0,\"ttl\":5}", &a));
    CHECK(!aim("{\"id\":1,\"pan\":1e,\"tilt\":0,\"ttl\":5}", &a));
    CHECK(!aim("{\"id\":1,\"pan\":-,\"tilt\":0,\"ttl\":5}", &a));
    CHECK(!aim("{\"id\":1,\"pan\":{\"a\":1},\"tilt\":0,\"ttl\":5}", &a));     // lồng nhau
    CHECK(!aim("{\"id\":1,\"pan\":[1],\"tilt\":0,\"ttl\":5}", &a));
    CHECK(!aim("{\"id\":1,\"pan\":0,\"tilt\":0,\"ttl\":5,\"x\":{}}", &a));    // lồng ở key lạ cũng loại
    CHECK(!aim("{\"id\":1,\"pan\":0,\"tilt\":0,\"ttl\":5,\"x\":[]}", &a));
    CHECK(!aim("[1,2]", &a));
    CHECK(!aim("42", &a));
    CHECK(!aim("", &a));
    CHECK(!aim("{", &a));
    CHECK(!aim("{}", &a));
    CHECK(!aim("{\"id\":1,\"pan\":0,\"tilt\":0,\"ttl\":5,\"s\":\"ab", &a));   // chuỗi cụt
    CHECK(!aim("{\"id\":1,\"pan\":0,\"tilt\":0,\"ttl\":5,\"s\":\"a\\n\"}", &a));  // escape không hỗ trợ
    CHECK(!proto_parse_aim(NULL, 0, &a));
    // out không bị ghi khi lỗi
    a.id = 99;
    CHECK(!aim("{\"id\":1,\"pan\":0,\"tilt\":0}", &a));
    CHECK_EQ(a.id, 99);
    // quá nhiều cặp
    char big[600] = "{";
    for (int i = 0; i < 20; i++) strcat(big, "\"k\":1,");
    strcat(big, "\"id\":1,\"pan\":0,\"tilt\":0,\"ttl\":5}");
    CHECK(!aim(big, &a));
    // số quá dài
    CHECK(!aim("{\"id\":1,\"pan\":0.0000000000000000000000000000000000000000000001,\"tilt\":0,\"ttl\":5}", &a));
}

static void p_fire(void)
{
    proto_fire_t f;
    CHECK(fire("{\"id\":9,\"dev\":\"pump\",\"ms\":800}", &f));
    CHECK(f.id == 9 && f.dev == ACT_DEV_PUMP && f.ms == 800);
    CHECK(fire("{ \"ms\" : 2e2 , \"dev\" : \"laser\" , \"id\" : 10.0 }", &f));
    CHECK(f.id == 10 && f.dev == ACT_DEV_LASER && f.ms == 200);
    CHECK(!fire("{\"id\":9,\"dev\":\"water\",\"ms\":800}", &f));
    CHECK(!fire("{\"id\":9,\"dev\":\"Pump\",\"ms\":800}", &f));
    CHECK(!fire("{\"id\":9,\"dev\":\"pumps\",\"ms\":800}", &f));
    CHECK(!fire("{\"id\":9,\"dev\":\"\",\"ms\":800}", &f));
    CHECK(!fire("{\"id\":9,\"dev\":1,\"ms\":800}", &f));
    CHECK(!fire("{\"id\":9,\"ms\":800}", &f));
    CHECK(!fire("{\"id\":9,\"dev\":\"pump\"}", &f));
    CHECK(!fire("{\"id\":9,\"dev\":\"pump\",\"ms\":-1}", &f));
    CHECK(!fire("{\"id\":9,\"dev\":\"pump\",\"ms\":4294967296}", &f));
    CHECK(!fire("{\"id\":9,\"dev\":\"pump\",\"ms\":80.5}", &f));
    CHECK(!fire("{\"id\":9,\"dev\":\"pump\",\"ms\":\"80\"}", &f));
    CHECK(!fire("{\"id\":9,\"dev\":\"pump\",\"ms\":80", &f));
    CHECK(!fire("{\"id\":9,\"dev\":[\"pump\"],\"ms\":80}", &f));
}

static void p_stop(void)
{
    proto_stop_t s;
    CHECK(stop("{\"id\":6}", &s) && s.id == 6);
    CHECK(stop(" {\"x\":1,\"id\":4294967295} ", &s) && s.id == UINT32_MAX);
    CHECK(!stop("{}", &s));
    CHECK(!stop("{\"id\":-6}", &s));
    CHECK(!stop("{\"id\":\"6\"}", &s));
    CHECK(!stop("{\"id\":6", &s));
    CHECK(!stop("{\"id\":{\"a\":6}}", &s));
}

static void p_alarm(void)
{
    proto_alarm_t a;
    CHECK(alarm_("{\"n\":\"s1\",\"s\":12}", &a));
    CHECK_STR(a.n, "s1");
    CHECK_EQ(a.s, 12);
    CHECK(alarm_("{\"s\":3,\"n\":\"1234567\"}", &a));  // 7 ký tự vừa
    CHECK_STR(a.n, "1234567");
    CHECK(!alarm_("{\"s\":3,\"n\":\"12345678\"}", &a));  // 8 ký tự không vừa (cần chỗ cho '\0')
    CHECK(!alarm_("{\"s\":3,\"n\":\"a-very-long-node-name\"}", &a));
    CHECK(alarm_("{\"s\":3,\"n\":\"\"}", &a));
    CHECK_STR(a.n, "");
    CHECK(!alarm_("{\"s\":3,\"n\":5}", &a));
    CHECK(!alarm_("{\"s\":\"3\",\"n\":\"s1\"}", &a));
    CHECK(!alarm_("{\"n\":\"s1\"}", &a));
    CHECK(!alarm_("{\"s\":3}", &a));
    CHECK(!alarm_("{\"n\":\"s1\",\"s\":-1}", &a));
}

// Mẫu đã tạo để cross-check bằng Python (in khi -v).
static void emit(int verbose, const char *tag, const char *s)
{
    if (verbose) printf("%s %s\n", tag, s);
}

static void p_gen(int verbose)
{
    char b2[PROTO_INFO_MAX];
    char b[PROTO_MAX];
    size_t n;

    n = proto_alert(b, sizeof b, "s1", 124, "alert", 52.1f, 832);
    CHECK_STR(b, "{\"n\":\"s1\",\"s\":124,\"k\":\"alert\",\"t\":52.1,\"g\":832}");
    CHECK_EQ(n, strlen(b));
    CHECK(n <= 60);
    emit(verbose, "A", b);

    n = proto_status(b, sizeof b, 42, "reached", 31.5f, -7.2f, NULL, "s1");
    CHECK_STR(b, "{\"id\":42,\"st\":\"reached\",\"pan\":31.5,\"tilt\":-7.2,\"n\":\"s1\"}");
    CHECK(n <= 60);
    emit(verbose, "S", b);

    n = proto_status(b, sizeof b, 4294967295u, "rejected", -50, 45, "not reached", "s2");
    CHECK_STR(b, "{\"id\":4294967295,\"st\":\"rejected\",\"pan\":-50,\"tilt\":45,\"err\":\"not reached\",\"n\":\"s2\"}");
    CHECK_EQ(n, strlen(b));
    emit(verbose, "S", b);
    n = proto_status(b, sizeof b, 1, "fault", 0, 0, "stopped", NULL);
    CHECK_STR(b, "{\"id\":1,\"st\":\"fault\",\"pan\":0,\"tilt\":0,\"err\":\"stopped\"}");
    emit(verbose, "S", b);

    n = proto_telemetry(b, sizeof b, "s1", 7, 3600, 27.0f, 120.0f, false, 0);
    CHECK_STR(b, "{\"n\":\"s1\",\"s\":7,\"up\":3600,\"t\":27,\"g\":120}");
    emit(verbose, "T", b);
    n = proto_telemetry(b, sizeof b, "s1", 7, 3600, 27.25f, 120.5f, true, 55.04f);
    CHECK_STR(b, "{\"n\":\"s1\",\"s\":7,\"up\":3600,\"t\":27.25,\"g\":120.5,\"h\":55.04}");
    CHECK(n <= 60);
    emit(verbose, "T", b);
    n = proto_telemetry(b, sizeof b, "s1", 0, 0, -0.001f, -12.345f, true, 100);
    CHECK_STR(b, "{\"n\":\"s1\",\"s\":0,\"up\":0,\"t\":0,\"g\":-12.35,\"h\":100}");
    emit(verbose, "T", b);
    n = proto_telemetry(b, sizeof b, "s1", 4294967295u, 4294967295u, 1234.5f, 1023, false, 0);
    CHECK(n > 0);
    emit(verbose, "T", b);

    n = proto_alarm(b, sizeof b, "s1", 124);
    CHECK_STR(b, "{\"n\":\"s1\",\"s\":124}");
    emit(verbose, "L", b);
    proto_alarm_t al;
    CHECK(proto_parse_alarm(b, n, &al) && al.s == 124);
    CHECK_STR(al.n, "s1");

    n = proto_info(b2, sizeof b2, "s1", "v1.2-3-gabc1234", -50, 50, -35, 45, 1500, 5000, true);
    CHECK_STR(b2, "{\"n\":\"s1\",\"fw\":\"v1.2-3-gabc1234\",\"pan\":[-50,50],\"tilt\":[-35,45],\"hb\":1500,\"fire\":5000,\"srp\":1}");
    CHECK_EQ(n, strlen(b2));
    emit(verbose, "I", b2);
    n = proto_info(b2, sizeof b2, "s2", "1.0", -47.5f, 50, -35, 40.25f, 1500, 3000, false);
    CHECK_STR(b2, "{\"n\":\"s2\",\"fw\":\"1.0\",\"pan\":[-47.5,50],\"tilt\":[-35,40.25],\"hb\":1500,\"fire\":3000,\"srp\":0}");
    CHECK_EQ(proto_info(b2, sizeof b2, "s1", "x\"y", 0, 1, 0, 1, 1, 1, false), 0);
    CHECK_EQ(proto_info(b2, 20, "s1", "1.0", 0, 1, 0, 1, 1, 1, false), 0);  // không vừa
    CHECK_EQ(proto_info(b2, sizeof b2, "s1", "1.0", (float)NAN, 1, 0, 1, 1, 1, false), 0);

    // không hữu hạn -> lỗi (0)
    CHECK_EQ(proto_alert(b, sizeof b, "s1", 1, "alert", (float)NAN, 1), 0);
    CHECK_EQ(proto_alert(b, sizeof b, "s1", 1, "alert", 1, (float)INFINITY), 0);
    CHECK_EQ(proto_telemetry(b, sizeof b, "s1", 1, 1, 1, 1, true, -(float)INFINITY), 0);
    CHECK_EQ(proto_status(b, sizeof b, 1, "done", (float)NAN, 0, NULL, "s1"), 0);
    CHECK_EQ(proto_status(b, sizeof b, 1, "done", 1e9f, 0, NULL, "s1"), 0);
    // chuỗi cần escape / NULL
    CHECK_EQ(proto_alert(b, sizeof b, "s\"1", 1, "alert", 1, 1), 0);
    CHECK_EQ(proto_alert(b, sizeof b, "s\\1", 1, "alert", 1, 1), 0);
    CHECK_EQ(proto_alert(b, sizeof b, NULL, 1, "alert", 1, 1), 0);
    CHECK_EQ(proto_status(b, sizeof b, 1, NULL, 0, 0, NULL, "s1"), 0);
    CHECK_EQ(proto_status(b, sizeof b, 1, "done", 0, 0, "a\nb", "s1"), 0);

    // bộ đệm vừa khít / thiếu một byte
    const char *exp = "{\"n\":\"s1\",\"s\":124}";
    size_t L = strlen(exp);
    char tight[32];
    memset(tight, 'X', sizeof tight);
    CHECK_EQ(proto_alarm(tight, L + 1, "s1", 124), L);
    CHECK_STR(tight, exp);
    CHECK_EQ(tight[L + 1], 'X');  // không ghi vượt
    memset(tight, 'X', sizeof tight);
    CHECK_EQ(proto_alarm(tight, L, "s1", 124), 0);
    CHECK_EQ(tight[L], 'X');
    CHECK_EQ(proto_alarm(tight, 0, "s1", 124), 0);
    CHECK_EQ(proto_alarm(NULL, 10, "s1", 124), 0);
    CHECK_EQ(proto_alarm(tight, 1, "s1", 124), 0);

    // quay vòng: bộ tạo -> bộ parse của chính mình
    float vals[] = {0, 1, -1, 0.5f, -0.25f, 31.5f, -7.2f, 49.99f, -34.75f, 12.34f, 0.01f, 45};
    for (size_t i = 0; i < sizeof vals / sizeof *vals; i++) {
        n = proto_status(b, sizeof b, (uint32_t)i, "accepted", vals[i], -vals[i], NULL, "s1");
        CHECK(n > 0);
        // dùng bộ parse aim trên bản status: id,pan,tilt có đủ; thiếu ttl nên chèn tay
        char j[PROTO_MAX + 24];
        snprintf(j, sizeof j, "{\"ttl\":5,%s", b + 1);
        proto_aim_t a;
        CHECK(proto_parse_aim(j, strlen(j), &a));
        CHECK(a.id == i);
        CHECK(fabsf(a.pan - vals[i]) < 0.006f);
        CHECK(fabsf(a.tilt + vals[i]) < 0.006f);
    }
}

void test_proto(int verbose)
{
    p_aim();
    p_fire();
    p_stop();
    p_alarm();
    p_gen(verbose);
}
