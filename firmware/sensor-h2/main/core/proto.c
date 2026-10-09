// Parse và tạo payload JSON của hợp đồng CoAP: xem proto.h.

#include "proto.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

// ---------------------------------------------------------------- parse

typedef enum { V_NUM, V_STR, V_OTHER } vtype_t;

typedef struct {
    const char *key;
    size_t key_len;
    vtype_t type;
    double num;
    const char *str;
    size_t str_len;
} pair_t;

#define MAX_PAIRS 16
#define NUM_MAX 40  // số dài hơn thế không phải thứ Pi gửi

typedef struct {
    pair_t p[MAX_PAIRS];
    size_t n;
} obj_t;

static bool is_ws(char c)
{
    return c == ' ' || c == '\t' || c == '\r' || c == '\n';
}

static bool is_digit(char c)
{
    return c >= '0' && c <= '9';
}

static void skip_ws(const char **s, const char *end)
{
    while (*s < end && is_ws(**s)) {
        (*s)++;
    }
}

// Chuỗi không escape, không ký tự điều khiển. *s trỏ vào dấu " mở.
static bool parse_string(const char **s, const char *end, const char **out, size_t *out_len)
{
    const char *p = *s;
    if (p >= end || *p != '"') {
        return false;
    }
    p++;
    const char *start = p;
    while (p < end && *p != '"') {
        unsigned char c = (unsigned char)*p;
        if (c == '\\' || c < 0x20) {
            return false;
        }
        p++;
    }
    if (p >= end) {
        return false;  // cụt
    }
    *out = start;
    *out_len = (size_t)(p - start);
    *s = p + 1;
    return true;
}

// Văn phạm số của JSON.
static bool parse_number(const char **s, const char *end, double *out)
{
    const char *p = *s;
    const char *start = p;
    if (p < end && *p == '-') {
        p++;
    }
    if (p >= end || !is_digit(*p)) {
        return false;
    }
    if (*p == '0') {
        p++;
    } else {
        while (p < end && is_digit(*p)) {
            p++;
        }
    }
    if (p < end && *p == '.') {
        p++;
        if (p >= end || !is_digit(*p)) {
            return false;
        }
        while (p < end && is_digit(*p)) {
            p++;
        }
    }
    if (p < end && (*p == 'e' || *p == 'E')) {
        p++;
        if (p < end && (*p == '+' || *p == '-')) {
            p++;
        }
        if (p >= end || !is_digit(*p)) {
            return false;
        }
        while (p < end && is_digit(*p)) {
            p++;
        }
    }
    size_t n = (size_t)(p - start);
    if (n >= NUM_MAX) {
        return false;
    }
    char tmp[NUM_MAX];
    memcpy(tmp, start, n);
    tmp[n] = '\0';
    double v = strtod(tmp, NULL);
    if (!isfinite(v)) {
        return false;
    }
    *out = v;
    *s = p;
    return true;
}

static bool match_lit(const char **s, const char *end, const char *lit)
{
    size_t n = strlen(lit);
    if ((size_t)(end - *s) < n || memcmp(*s, lit, n) != 0) {
        return false;
    }
    *s += n;
    return true;
}

static bool parse_obj(const char *buf, size_t len, obj_t *o)
{
    if (buf == NULL) {
        return false;
    }
    const char *s = buf, *end = buf + len;
    o->n = 0;
    skip_ws(&s, end);
    if (s >= end || *s != '{') {
        return false;
    }
    s++;
    skip_ws(&s, end);
    if (s < end && *s == '}') {
        s++;
    } else {
        for (;;) {
            if (o->n >= MAX_PAIRS) {
                return false;
            }
            pair_t *p = &o->p[o->n];
            skip_ws(&s, end);
            if (!parse_string(&s, end, &p->key, &p->key_len)) {
                return false;
            }
            skip_ws(&s, end);
            if (s >= end || *s != ':') {
                return false;
            }
            s++;
            skip_ws(&s, end);
            if (s >= end) {
                return false;
            }
            if (*s == '"') {
                p->type = V_STR;
                if (!parse_string(&s, end, &p->str, &p->str_len)) {
                    return false;
                }
            } else if (*s == '-' || is_digit(*s)) {
                p->type = V_NUM;
                if (!parse_number(&s, end, &p->num)) {
                    return false;
                }
            } else if (match_lit(&s, end, "true") || match_lit(&s, end, "false") ||
                       match_lit(&s, end, "null")) {
                p->type = V_OTHER;
            } else {
                return false;  // object/array lồng nhau hoặc rác
            }
            o->n++;
            skip_ws(&s, end);
            if (s >= end) {
                return false;
            }
            if (*s == ',') {
                s++;
                continue;
            }
            if (*s == '}') {
                s++;
                break;
            }
            return false;
        }
    }
    skip_ws(&s, end);
    return s == end;
}

// Key trùng: lấy lần cuối như json.loads của Python.
static const pair_t *find_key(const obj_t *o, const char *key)
{
    size_t kl = strlen(key);
    const pair_t *found = NULL;
    for (size_t i = 0; i < o->n; i++) {
        if (o->p[i].key_len == kl && memcmp(o->p[i].key, key, kl) == 0) {
            found = &o->p[i];
        }
    }
    return found;
}

static bool get_u32(const obj_t *o, const char *key, uint32_t *out)
{
    const pair_t *p = find_key(o, key);
    if (p == NULL || p->type != V_NUM) {
        return false;
    }
    double v = p->num;
    if (v != floor(v) || v < 0.0 || v > 4294967295.0) {
        return false;
    }
    *out = (uint32_t)v;
    return true;
}

static bool get_f32(const obj_t *o, const char *key, float *out)
{
    const pair_t *p = find_key(o, key);
    if (p == NULL || p->type != V_NUM) {
        return false;
    }
    float f = (float)p->num;
    if (!isfinite(f)) {
        return false;
    }
    *out = f;
    return true;
}

static bool get_str(const obj_t *o, const char *key, const char **s, size_t *n)
{
    const pair_t *p = find_key(o, key);
    if (p == NULL || p->type != V_STR) {
        return false;
    }
    *s = p->str;
    *n = p->str_len;
    return true;
}

bool proto_parse_aim(const char *buf, size_t len, proto_aim_t *out)
{
    obj_t o;
    proto_aim_t r;
    if (!parse_obj(buf, len, &o) || !get_u32(&o, "id", &r.id) || !get_f32(&o, "pan", &r.pan) ||
        !get_f32(&o, "tilt", &r.tilt) || !get_u32(&o, "ttl", &r.ttl)) {
        return false;
    }
    *out = r;
    return true;
}

bool proto_parse_fire(const char *buf, size_t len, proto_fire_t *out)
{
    obj_t o;
    proto_fire_t r;
    const char *d;
    size_t dn;
    if (!parse_obj(buf, len, &o) || !get_u32(&o, "id", &r.id) || !get_str(&o, "dev", &d, &dn) ||
        !get_u32(&o, "ms", &r.ms)) {
        return false;
    }
    if (dn == 4 && memcmp(d, "pump", 4) == 0) {
        r.dev = ACT_DEV_PUMP;
    } else if (dn == 5 && memcmp(d, "laser", 5) == 0) {
        r.dev = ACT_DEV_LASER;
    } else {
        return false;
    }
    *out = r;
    return true;
}

bool proto_parse_stop(const char *buf, size_t len, proto_stop_t *out)
{
    obj_t o;
    proto_stop_t r;
    if (!parse_obj(buf, len, &o) || !get_u32(&o, "id", &r.id)) {
        return false;
    }
    *out = r;
    return true;
}

bool proto_parse_alarm(const char *buf, size_t len, proto_alarm_t *out)
{
    obj_t o;
    proto_alarm_t r;
    const char *n;
    size_t nl;
    if (!parse_obj(buf, len, &o) || !get_str(&o, "n", &n, &nl) || !get_u32(&o, "s", &r.s)) {
        return false;
    }
    if (nl >= sizeof r.n) {
        return false;
    }
    memset(r.n, 0, sizeof r.n);
    memcpy(r.n, n, nl);
    *out = r;
    return true;
}

// ---------------------------------------------------------------- tạo

typedef struct {
    char *b;
    size_t cap, len;
    bool ok;
} out_t;

static void put_raw(out_t *o, const char *s, size_t n)
{
    if (!o->ok) {
        return;
    }
    if (o->len + n + 1 > o->cap) {  // chừa chỗ cho '\0'
        o->ok = false;
        return;
    }
    memcpy(o->b + o->len, s, n);
    o->len += n;
}

static void put(out_t *o, const char *s)
{
    put_raw(o, s, strlen(s));
}

// Chuỗi trong JSON: từ chối ký tự cần escape thay vì tạo JSON sai.
static void put_qstr(out_t *o, const char *s)
{
    if (s == NULL) {
        o->ok = false;
        return;
    }
    for (const char *p = s; *p; p++) {
        unsigned char c = (unsigned char)*p;
        if (c == '"' || c == '\\' || c < 0x20) {
            o->ok = false;
            return;
        }
    }
    put(o, "\"");
    put(o, s);
    put(o, "\"");
}

static void put_u32(out_t *o, uint32_t v)
{
    char t[11];
    size_t i = sizeof t;
    do {
        t[--i] = (char)('0' + v % 10u);
        v /= 10u;
    } while (v != 0u);
    put_raw(o, t + i, sizeof t - i);
}

// Tối đa 2 chữ số thập phân, bỏ số 0 thừa; không nan/inf.
static void put_f(out_t *o, float v)
{
    if (!isfinite(v) || fabsf(v) >= 1.0e7f) {
        o->ok = false;
        return;
    }
    long long sc = llround((double)v * 100.0);
    if (sc < 0) {
        put(o, "-");
        sc = -sc;
    }
    put_u32(o, (uint32_t)(sc / 100));
    unsigned fr = (unsigned)(sc % 100);
    if (fr != 0u) {
        char t[3] = {'.', (char)('0' + fr / 10u), (char)('0' + fr % 10u)};
        put_raw(o, t, fr % 10u == 0u ? 2u : 3u);
    }
}

static void key(out_t *o, const char *k, bool first)
{
    if (!first) {
        put(o, ",");
    }
    put(o, "\"");
    put(o, k);
    put(o, "\":");
}

static size_t finish(out_t *o)
{
    if (!o->ok) {
        return 0;
    }
    o->b[o->len] = '\0';
    return o->len;
}

static bool begin(out_t *o, char *buf, size_t cap)
{
    o->b = buf;
    o->cap = cap;
    o->len = 0;
    o->ok = buf != NULL && cap > 0;
    put(o, "{");
    return o->ok;
}

size_t proto_telemetry(char *buf, size_t cap, const char *n, uint32_t s, uint32_t up_s, float t, float g,
                       bool has_h, float h)
{
    out_t o;
    if (!begin(&o, buf, cap)) {
        return 0;
    }
    key(&o, "n", true);
    put_qstr(&o, n);
    key(&o, "s", false);
    put_u32(&o, s);
    key(&o, "up", false);
    put_u32(&o, up_s);
    key(&o, "t", false);
    put_f(&o, t);
    key(&o, "g", false);
    put_f(&o, g);
    if (has_h) {
        key(&o, "h", false);
        put_f(&o, h);
    }
    put(&o, "}");
    return finish(&o);
}

size_t proto_alert(char *buf, size_t cap, const char *n, uint32_t s, const char *k, float t, float g)
{
    out_t o;
    if (!begin(&o, buf, cap)) {
        return 0;
    }
    key(&o, "n", true);
    put_qstr(&o, n);
    key(&o, "s", false);
    put_u32(&o, s);
    key(&o, "k", false);
    put_qstr(&o, k);
    key(&o, "t", false);
    put_f(&o, t);
    key(&o, "g", false);
    put_f(&o, g);
    put(&o, "}");
    return finish(&o);
}

size_t proto_status(char *buf, size_t cap, uint32_t id, const char *st, float pan, float tilt, const char *err,
                    const char *n)
{
    out_t o;
    if (!begin(&o, buf, cap)) {
        return 0;
    }
    key(&o, "id", true);
    put_u32(&o, id);
    key(&o, "st", false);
    put_qstr(&o, st);
    key(&o, "pan", false);
    put_f(&o, pan);
    key(&o, "tilt", false);
    put_f(&o, tilt);
    if (err != NULL) {
        key(&o, "err", false);
        put_qstr(&o, err);
    }
    if (n != NULL) {
        key(&o, "n", false);
        put_qstr(&o, n);
    }
    put(&o, "}");
    return finish(&o);
}

size_t proto_alarm(char *buf, size_t cap, const char *n, uint32_t s)
{
    out_t o;
    if (!begin(&o, buf, cap)) {
        return 0;
    }
    key(&o, "n", true);
    put_qstr(&o, n);
    key(&o, "s", false);
    put_u32(&o, s);
    put(&o, "}");
    return finish(&o);
}

size_t proto_info(char *buf, size_t cap, const char *n, const char *fw, float pan_lo, float pan_hi, float tilt_lo,
                  float tilt_hi, uint32_t hb_ms, uint32_t fire_ms, bool srp)
{
    out_t o;
    if (!begin(&o, buf, cap)) {
        return 0;
    }
    key(&o, "n", true);
    put_qstr(&o, n);
    key(&o, "fw", false);
    put_qstr(&o, fw);
    key(&o, "pan", false);
    put(&o, "[");
    put_f(&o, pan_lo);
    put(&o, ",");
    put_f(&o, pan_hi);
    put(&o, "]");
    key(&o, "tilt", false);
    put(&o, "[");
    put_f(&o, tilt_lo);
    put(&o, ",");
    put_f(&o, tilt_hi);
    put(&o, "]");
    key(&o, "hb", false);
    put_u32(&o, hb_ms);
    key(&o, "fire", false);
    put_u32(&o, fire_ms);
    key(&o, "srp", false);
    put(&o, srp ? "1" : "0");
    put(&o, "}");
    return finish(&o);
}
