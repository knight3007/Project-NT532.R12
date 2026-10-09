#include "coap_node.h"

#include <arpa/inet.h>
#include <string.h>
#include <sys/socket.h>

#include "act_task.h"
#include "coap3/coap.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "hal_esp.h"
#include "esp_app_desc.h"
#include "net_ot.h"
#include "node_cfg.h"
#include "proto.h"
#include "sdkconfig.h"
#include "sensors.h"

static const char *TAG = "coap";

#define OUT_Q_LEN 16
#define MCAST_ADDR "ff03::1"
#define MCAST_HOPS 8

typedef struct {
    uint8_t kind;
    uint8_t len;
    char buf[PROTO_MAX];
} out_msg_t;

static QueueHandle_t s_out_q;
static char s_node[8];
static coap_context_t *s_ctx;
static coap_session_t *s_pi, *s_mc;

bool coap_node_send(coap_out_kind_t kind, const char *json, size_t len)
{
    out_msg_t m;
    if (!s_out_q || len == 0 || len > sizeof(m.buf)) {
        return false;
    }
    m.kind = (uint8_t)kind;
    m.len = (uint8_t)len;
    memcpy(m.buf, json, len);
    if (xQueueSend(s_out_q, &m, 0) != pdTRUE) {
        ESP_LOGW(TAG, "hàng đợi ra đầy, bỏ bản tin loại %d", (int)kind);
        return false;
    }
    return true;
}

void coap_node_init(const node_cfg_t *cfg)
{
    strncpy(s_node, cfg->node_id, sizeof(s_node) - 1);
    s_out_q = xQueueCreate(OUT_Q_LEN, sizeof(out_msg_t));
    configASSERT(s_out_q);
}

// --- server: handler chạy trong coap_io_process, chỉ parse rồi đẩy lệnh ----------------------------

static bool payload_of(const coap_pdu_t *req, const char **p, size_t *n)
{
    const uint8_t *d;
    size_t len;
    if (!coap_get_data(req, &len, &d) || len == 0) {
        return false;
    }
    *p = (const char *)d;
    *n = len;
    return true;
}

static void reply(coap_pdu_t *resp, bool ok)
{
    coap_pdu_set_code(resp, ok ? COAP_RESPONSE_CODE_CHANGED : COAP_RESPONSE_CODE_BAD_REQUEST);
}

static void reply_post(coap_pdu_t *resp, bool queued)
{
    coap_pdu_set_code(resp, queued ? COAP_RESPONSE_CODE_CHANGED : COAP_RESPONSE_CODE_SERVICE_UNAVAILABLE);
}

static void h_aim(coap_resource_t *r, coap_session_t *s, const coap_pdu_t *req, const coap_string_t *q,
                  coap_pdu_t *resp)
{
    const char *p;
    size_t n;
    proto_aim_t a;
    if (!payload_of(req, &p, &n) || !proto_parse_aim(p, n, &a)) {
        ESP_LOGW(TAG, "/aim sai payload");
        reply(resp, false);
        return;
    }
    act_cmd_t c = {.type = CMD_AIM, .id = a.id, .pan = a.pan, .tilt = a.tilt, .ttl_ms = a.ttl};
    reply_post(resp, act_task_post(&c));
}

static void h_fire(coap_resource_t *r, coap_session_t *s, const coap_pdu_t *req, const coap_string_t *q,
                   coap_pdu_t *resp)
{
    const char *p;
    size_t n;
    proto_fire_t f;
    if (!payload_of(req, &p, &n) || !proto_parse_fire(p, n, &f)) {
        ESP_LOGW(TAG, "/fire sai payload");
        reply(resp, false);
        return;
    }
    act_cmd_t c = {.type = CMD_FIRE, .id = f.id, .dev = f.dev, .ms = f.ms};
    reply_post(resp, act_task_post(&c));
}

static void h_stop(coap_resource_t *r, coap_session_t *s, const coap_pdu_t *req, const coap_string_t *q,
                   coap_pdu_t *resp)
{
    const char *p;
    size_t n;
    proto_stop_t st;
    if (!payload_of(req, &p, &n) || !proto_parse_stop(p, n, &st)) {
        ESP_LOGW(TAG, "/stop sai payload");
        reply(resp, false);
        return;
    }
    // Tắt phần cứng ngay, không chờ task chấp hành; lệnh vẫn vào hàng đợi để cập nhật id /stop và gửi "done".
    hal_dev_set(ACT_DEV_PUMP, false);
    hal_dev_set(ACT_DEV_LASER, false);
    act_cmd_t c = {.type = CMD_STOP, .id = st.id};
    reply_post(resp, act_task_post(&c));
}

static void h_hb(coap_resource_t *r, coap_session_t *s, const coap_pdu_t *req, const coap_string_t *q,
                 coap_pdu_t *resp)
{
    // Pi đo tuổi heartbeat bằng chính câu trả lời này: luôn trả 2.04. Libcoap trả NON cho request NON.
    act_cmd_t c = {.type = CMD_HB};
    act_task_post(&c);
    coap_pdu_set_code(resp, COAP_RESPONSE_CODE_CHANGED);
}

static void h_alarm(coap_resource_t *r, coap_session_t *s, const coap_pdu_t *req, const coap_string_t *q,
                    coap_pdu_t *resp)
{
    const char *p;
    size_t n;
    proto_alarm_t a;
    if (!payload_of(req, &p, &n) || !proto_parse_alarm(p, n, &a)) {
        reply(resp, false);
        return;
    }
    if (strcmp(a.n, s_node) != 0) {
        ESP_LOGW(TAG, "/alarm từ %s (s=%u)", a.n, (unsigned)a.s);
        sensors_remote_alarm();
    }
    coap_pdu_set_code(resp, COAP_RESPONSE_CODE_CHANGED);
}

// GET /info: Pi hỏi một lần khi khởi động để so giới hạn góc và nhịp heartbeat với site.yaml.
static void h_info(coap_resource_t *r, coap_session_t *s, const coap_pdu_t *req, const coap_string_t *q,
                   coap_pdu_t *resp)
{
    const node_cfg_t *c = node_cfg();
    char buf[PROTO_INFO_MAX];
#ifdef CONFIG_NT532_SRP
    const bool srp = true;
#else
    const bool srp = false;
#endif
    size_t n = proto_info(buf, sizeof buf, s_node, esp_app_get_description()->version, c->pan_min, c->pan_max,
                          c->tilt_min, c->tilt_max, c->hb_timeout_ms, c->max_fire_ms, srp);
    if (n == 0) {
        coap_pdu_set_code(resp, COAP_RESPONSE_CODE_INTERNAL_ERROR);
        return;
    }
    coap_pdu_set_code(resp, COAP_RESPONSE_CODE_CONTENT);
    coap_add_data(resp, n, (const uint8_t *)buf);  // libcoap sao dữ liệu vào PDU
}

static void add_post(coap_context_t *ctx, const char *path, coap_method_handler_t h)
{
    coap_resource_t *res = coap_resource_init(coap_make_str_const(path), 0);
    coap_register_handler(res, COAP_REQUEST_POST, h);
    coap_add_resource(ctx, res);
}

static void add_get(coap_context_t *ctx, const char *path, coap_method_handler_t h)
{
    coap_resource_t *res = coap_resource_init(coap_make_str_const(path), 0);
    coap_register_handler(res, COAP_REQUEST_GET, h);
    coap_add_resource(ctx, res);
}

// --- client -----------------------------------------------------------------------------------

static coap_response_t on_response(coap_session_t *s, const coap_pdu_t *sent, const coap_pdu_t *recv,
                                   const coap_mid_t mid)
{
    coap_pdu_code_t code = coap_pdu_get_code(recv);
    if (COAP_RESPONSE_CLASS(code) != 2) {
        ESP_LOGW(TAG, "Pi trả %d.%02d cho mid=%d", COAP_RESPONSE_CLASS(code), code & 0x1f, (int)mid);
    }
    return COAP_RESPONSE_OK;
}

static void on_nack(coap_session_t *s, const coap_pdu_t *sent, const coap_nack_reason_t reason,
                    const coap_mid_t mid)
{
    // Hết lần gửi lại hoặc bị RST: /status hoặc /a không tới Pi.
    ESP_LOGW(TAG, "gói CON mid=%d không tới đích (lý do %d)", (int)mid, (int)reason);
}

// CHƯA KIỂM: tên trường addr.sin6 và coap_address_init trong libcoap 4.3.5 của component espressif/coap
static bool make_addr(coap_address_t *a, const char *ip6, uint16_t port)
{
    coap_address_init(a);
    a->addr.sin6.sin6_family = AF_INET6;
    a->addr.sin6.sin6_port = htons(port);
    a->size = sizeof(struct sockaddr_in6);
    return inet_pton(AF_INET6, ip6, &a->addr.sin6.sin6_addr) == 1;
}

static coap_session_t *open_session(const char *ip6)
{
    coap_address_t dst;
    if (!ip6[0] || !make_addr(&dst, ip6, CONFIG_NT532_COAP_PORT)) {
        return NULL;
    }
    return coap_new_client_session(s_ctx, NULL, &dst, COAP_PROTO_UDP);
}

static void send_msg(const out_msg_t *m)
{
    const char *path;
    coap_pdu_type_t type;
    coap_session_t **sess = &s_pi;
    switch (m->kind) {
    case COAP_OUT_STATUS: path = "status"; type = COAP_MESSAGE_CON; break;
    case COAP_OUT_ALERT:  path = "a";      type = COAP_MESSAGE_CON; break;
    case COAP_OUT_TELEM:  path = "t";      type = COAP_MESSAGE_NON; break;
    default:              path = "alarm";  type = COAP_MESSAGE_NON; sess = &s_mc; break;
    }
    if (!*sess) {
        *sess = open_session(m->kind == COAP_OUT_ALARM ? MCAST_ADDR : CONFIG_NT532_PI_ADDR);
        if (*sess && m->kind == COAP_OUT_ALARM) {
            coap_mcast_set_hops(*sess, MCAST_HOPS);  // mặc định 1 thì không qua được router Thread
        }
    }
    if (!*sess) {
        ESP_LOGW(TAG, "chưa có session cho /%s (NT532_PI_ADDR=\"%s\"), bỏ bản tin", path, CONFIG_NT532_PI_ADDR);
        return;
    }
    coap_pdu_t *pdu = coap_pdu_init(type, COAP_REQUEST_CODE_POST, coap_new_message_id(*sess),
                                    coap_session_max_pdu_size(*sess));
    if (!pdu) {
        ESP_LOGE(TAG, "không tạo được PDU");
        return;
    }
    coap_add_option(pdu, COAP_OPTION_URI_PATH, strlen(path), (const uint8_t *)path);
    coap_add_data(pdu, m->len, (const uint8_t *)m->buf);
    if (coap_send(*sess, pdu) == COAP_INVALID_MID) {
        ESP_LOGW(TAG, "gửi /%s lỗi", path);
    }
}

// --- task ---------------------------------------------------------------------------------------

static bool server_up(void)
{
    s_ctx = coap_new_context(NULL);
    if (!s_ctx) {
        ESP_LOGE(TAG, "coap_new_context lỗi");
        return false;
    }
    coap_address_t any;
    coap_address_init(&any);  // đã zero: in6addr_any
    any.addr.sin6.sin6_family = AF_INET6;
    any.addr.sin6.sin6_port = htons(CONFIG_NT532_COAP_PORT);
    any.size = sizeof(struct sockaddr_in6);
    if (!coap_new_endpoint(s_ctx, &any, COAP_PROTO_UDP)) {
        ESP_LOGE(TAG, "không mở được endpoint UDP [::]:%d", CONFIG_NT532_COAP_PORT);
        return false;
    }
    add_post(s_ctx, "aim", h_aim);
    add_post(s_ctx, "fire", h_fire);
    add_post(s_ctx, "stop", h_stop);
    add_post(s_ctx, "hb", h_hb);
    add_post(s_ctx, "alarm", h_alarm);
    add_get(s_ctx, "info", h_info);
    coap_register_response_handler(s_ctx, on_response);
    coap_register_nack_handler(s_ctx, on_nack);
    // CHƯA KIỂM: lwIP có nhận ff03::1 trên netif OpenThread sau khi join bằng setsockopt hay không
    if (coap_join_mcast_group_intf(s_ctx, MCAST_ADDR, NULL) != 0) {
        ESP_LOGW(TAG, "không join được %s, sẽ không nhận /alarm của node kia", MCAST_ADDR);
    }
    return true;
}

static void coap_task(void *arg)
{
    (void)arg;
    coap_startup();
    net_ot_wait_attached();
    ESP_LOGI(TAG, "Thread đã gắn, mở server CoAP cổng %d", CONFIG_NT532_COAP_PORT);
    if (!server_up()) {
        ESP_LOGE(TAG, "server CoAP không chạy được; node chỉ còn watchdog an toàn");
        vTaskDelete(NULL);
    }
    out_msg_t m;
    for (;;) {
        coap_io_process(s_ctx, 10);
        while (xQueueReceive(s_out_q, &m, 0) == pdTRUE) {
            send_msg(&m);
        }
    }
}

void coap_node_start(void)
{
    xTaskCreate(coap_task, "coap", 8192, NULL, 4, NULL);
}
