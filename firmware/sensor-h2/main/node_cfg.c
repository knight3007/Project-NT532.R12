#include "node_cfg.h"

#include <stdlib.h>
#include <string.h>

#include "esp_log.h"
#include "nvs.h"
#include "sdkconfig.h"

static const char *TAG = "cfg";
static node_cfg_t s_cfg;

static void get_str(nvs_handle_t h, const char *key, char *dst, size_t cap)
{
    char tmp[32];
    size_t len = sizeof(tmp);
    if (nvs_get_str(h, key, tmp, &len) == ESP_OK && tmp[0] != '\0' && strlen(tmp) < cap) {
        strcpy(dst, tmp);
        ESP_LOGI(TAG, "NVS %s=%s", key, dst);
    }
}

static void get_f(nvs_handle_t h, const char *key, float *dst)
{
    char tmp[24];
    size_t len = sizeof(tmp);
    if (nvs_get_str(h, key, tmp, &len) != ESP_OK) {
        return;
    }
    char *end = NULL;
    float v = strtof(tmp, &end);
    if (end == tmp || *end != '\0') {
        ESP_LOGW(TAG, "NVS %s=\"%s\" không phải số, bỏ qua", key, tmp);
        return;
    }
    *dst = v;
    ESP_LOGI(TAG, "NVS %s=%.2f", key, v);
}

static void get_i(nvs_handle_t h, const char *key, int *dst)
{
    int32_t v;
    if (nvs_get_i32(h, key, &v) == ESP_OK) {
        *dst = (int)v;
        ESP_LOGI(TAG, "NVS %s=%d", key, (int)v);
    }
}

static void get_b(nvs_handle_t h, const char *key, bool *dst)
{
    int v = *dst;
    int before = v;
    get_i(h, key, &v);
    if (v != before) {
        *dst = v != 0;
    }
}

void node_cfg_load(node_cfg_t *c)
{
    memset(c, 0, sizeof(*c));
    strncpy(c->node_id, CONFIG_NT532_NODE_ID, sizeof(c->node_id) - 1);
    c->temp_c = CONFIG_NT532_TEMP_ALARM_DECI_C / 10.0f;
    c->gas = CONFIG_NT532_GAS_ALARM;
    c->telemetry_s = CONFIG_NT532_TELEMETRY_S;
    c->warmup_s = CONFIG_NT532_GAS_WARMUP_S;
    c->remote_alarm_s = CONFIG_NT532_REMOTE_ALARM_S;
    c->pulse_min_us = CONFIG_NT532_SERVO_PULSE_MIN_US;
    c->pulse_max_us = CONFIG_NT532_SERVO_PULSE_MAX_US;
    c->pan_off = CONFIG_NT532_PAN_OFFSET_DECI / 10.0f;
    c->tilt_off = CONFIG_NT532_TILT_OFFSET_DECI / 10.0f;
#ifdef CONFIG_NT532_PAN_INVERT
    c->pan_inv = true;
#endif
#ifdef CONFIG_NT532_TILT_INVERT
    c->tilt_inv = true;
#endif
    c->pan_min = CONFIG_NT532_PAN_MIN;
    c->pan_max = CONFIG_NT532_PAN_MAX;
    c->tilt_min = CONFIG_NT532_TILT_MIN;
    c->tilt_max = CONFIG_NT532_TILT_MAX;
    c->hb_timeout_ms = CONFIG_NT532_HB_TIMEOUT_MS;
    c->max_fire_ms = CONFIG_NT532_MAX_FIRE_MS;

    nvs_handle_t h;
    if (nvs_open("nt532", NVS_READONLY, &h) == ESP_OK) {
        int tmp;
        get_str(h, "node_id", c->node_id, sizeof(c->node_id));
        get_f(h, "temp_c", &c->temp_c);
        get_f(h, "gas", &c->gas);
        tmp = (int)c->telemetry_s;
        get_i(h, "telem_s", &tmp);
        if (tmp > 0) c->telemetry_s = (uint32_t)tmp;
        get_i(h, "pmin_us", &c->pulse_min_us);
        get_i(h, "pmax_us", &c->pulse_max_us);
        get_f(h, "pan_off", &c->pan_off);
        get_f(h, "tilt_off", &c->tilt_off);
        get_b(h, "pan_inv", &c->pan_inv);
        get_b(h, "tilt_inv", &c->tilt_inv);
        get_f(h, "pan_lo", &c->pan_min);
        get_f(h, "pan_hi", &c->pan_max);
        get_f(h, "tilt_lo", &c->tilt_min);
        get_f(h, "tilt_hi", &c->tilt_max);
        nvs_close(h);
    } else {
        ESP_LOGI(TAG, "không có namespace nt532 trong NVS, dùng mặc định Kconfig");
    }
    if (c->pulse_min_us >= c->pulse_max_us) {
        ESP_LOGE(TAG, "pulse_min >= pulse_max, quay về mặc định 500..2500");
        c->pulse_min_us = 500;
        c->pulse_max_us = 2500;
    }
    s_cfg = *c;
    ESP_LOGI(TAG, "node %s: ngưỡng %.1f C / gas %.0f, pan %.0f..%.0f tilt %.0f..%.0f", c->node_id, c->temp_c,
             c->gas, c->pan_min, c->pan_max, c->tilt_min, c->tilt_max);
}

const node_cfg_t *node_cfg(void)
{
    return &s_cfg;
}
