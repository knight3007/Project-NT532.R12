#include <inttypes.h>
#include <stdio.h>
#include <string.h>

#include "esp_app_desc.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_netif_types.h"
#include "esp_openthread.h"
#include "esp_openthread_lock.h"
#include "esp_openthread_netif_glue.h"
#include "esp_openthread_types.h"
#include "esp_timer.h"
#include "esp_vfs_eventfd.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs_flash.h"
#include "openthread/coap.h"
#include "openthread/dataset.h"
#include "openthread/ip6.h"
#include "openthread/thread.h"

#include "integrated_control.h"
#include "mq2_sensor.h"
#include "node_a_response.h"
#include "probe_state.h"
#include "servo_output.h"
#include "thread_start.h"

static const char *TAG = "node_a_integrated";
static probe_state_t s_state = {.stopped = true};
enum { RESOURCE_PANTILT = 4, RESOURCE_LASER = 5, RESOURCE_MQ2 = 6 };
static const int s_resource_ids[] = {PROBE_INFO, PROBE_HB, PROBE_STATUS, PROBE_STOP,
                                     RESOURCE_PANTILT, RESOURCE_LASER, RESOURCE_MQ2};
static portMUX_TYPE s_control_lock = portMUX_INITIALIZER_UNLOCKED;
static control_state_t s_control;
static TaskHandle_t s_servo_task;
enum { PROVISION_LINE_CAPACITY = 520 };
_Static_assert(PROVISION_LINE_CAPACITY >= 4 + 2 * OT_OPERATIONAL_DATASET_MAX_LENGTH + 3,
               "Provisioning line buffer is too small for a full dataset TLV");

static void wake_servo(void)
{
    if (s_servo_task != NULL) xTaskNotifyGive(s_servo_task);
}

static control_state_t control_snapshot(void)
{
    portENTER_CRITICAL(&s_control_lock);
    control_state_t snapshot = s_control;
    portEXIT_CRITICAL(&s_control_lock);
    return snapshot;
}

// Caller holds s_control_lock so relay GPIO and authorization cannot race STOP.
static esp_err_t relay_apply_locked(void)
{
    esp_err_t error = servo_output_set_relay(s_control.laser_on);
    if (error != ESP_OK) {
        control_stop(&s_control);
        (void)servo_output_set_relay(false);
    }
    return error;
}

static otCoapCode pantilt_post(otMessage *request, uint64_t now_ms)
{
    uint16_t offset = otMessageGetOffset(request);
    uint16_t length = otMessageGetLength(request);
    if (offset > length || length - offset > 16) return OT_COAP_CODE_REQUEST_TOO_LARGE;
    char command[17];
    uint16_t count = length - offset;
    if (otMessageRead(request, offset, command, count) != count) return OT_COAP_CODE_BAD_REQUEST;
    command[count] = '\0';
    portENTER_CRITICAL(&s_control_lock);
    control_result_t result = control_pantilt(&s_control, command, count, now_ms);
    portEXIT_CRITICAL(&s_control_lock);
    if (result == CONTROL_OK) {
        wake_servo();
        return OT_COAP_CODE_CHANGED;
    }
    return result == CONTROL_BAD_REQUEST ? OT_COAP_CODE_BAD_REQUEST : OT_COAP_CODE_FORBIDDEN;
}

static otCoapCode laser_post(otMessage *request, uint64_t now_ms)
{
    uint16_t offset = otMessageGetOffset(request);
    uint16_t length = otMessageGetLength(request);
    if (offset > length || length - offset > 16) return OT_COAP_CODE_REQUEST_TOO_LARGE;
    char command[17];
    uint16_t count = length - offset;
    if (otMessageRead(request, offset, command, count) != count) return OT_COAP_CODE_BAD_REQUEST;
    portENTER_CRITICAL(&s_control_lock);
    control_result_t result = control_laser_request(&s_control, command, count, now_ms);
    esp_err_t relay_error = result == CONTROL_OK ? relay_apply_locked() : ESP_OK;
    portEXIT_CRITICAL(&s_control_lock);
    if (relay_error != ESP_OK) {
        ESP_LOGE(TAG, "Relay update failed: %s", esp_err_to_name(relay_error));
        wake_servo();
        return OT_COAP_CODE_INTERNAL_ERROR;
    }
    return result == CONTROL_OK ? OT_COAP_CODE_CHANGED
           : result == CONTROL_BAD_REQUEST ? OT_COAP_CODE_BAD_REQUEST : OT_COAP_CODE_FORBIDDEN;
}

static void servo_task(void *unused)
{
    (void)unused;
    bool hardware_active = false;
    for (;;) {
        ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(20));
        portENTER_CRITICAL(&s_control_lock);
        // Sample the clock inside the lock so a heartbeat handled meanwhile is never newer than now_ms.
        uint64_t now_ms = (uint64_t)esp_timer_get_time() / 1000;
        bool timed_out = control_watchdog(&s_control, now_ms);
        esp_err_t relay_error = relay_apply_locked();
        bool start_home = s_control.home_requested;
        bool requested = start_home || s_control.pwm_active;
        portEXIT_CRITICAL(&s_control_lock);
        if (timed_out) ESP_LOGW(TAG, "Actuator heartbeat expired; servo PWM stopped");
        if (relay_error != ESP_OK) ESP_LOGE(TAG, "Relay update failed: %s", esp_err_to_name(relay_error));
        if (!requested) {
            if (hardware_active) servo_output_stop();
            hardware_active = false;
            continue;
        }
        if (!hardware_active) {
            if (!start_home) continue;
            esp_err_t error = servo_output_start_home();
            if (error != ESP_OK) {
                ESP_LOGE(TAG, "Servo start failed: %s", esp_err_to_name(error));
                portENTER_CRITICAL(&s_control_lock);
                control_stop(&s_control);
                (void)relay_apply_locked();
                portEXIT_CRITICAL(&s_control_lock);
                servo_output_stop();
                continue;
            }
            hardware_active = true;
            portENTER_CRITICAL(&s_control_lock);
            (void)control_watchdog(&s_control, (uint64_t)esp_timer_get_time() / 1000);
            (void)relay_apply_locked();
            bool keep = control_pwm_started(&s_control);
            portEXIT_CRITICAL(&s_control_lock);
            if (!keep) {
                servo_output_stop();
                hardware_active = false;
            }
            continue;
        }
        int pan_us = 0, tilt_us = 0;
        portENTER_CRITICAL(&s_control_lock);
        bool active = s_control.pwm_active;
        bool changed = active && control_next_step(&s_control, now_ms, &pan_us, &tilt_us);
        portEXIT_CRITICAL(&s_control_lock);
        if (!active) {
            servo_output_stop();
            hardware_active = false;
            continue;
        }
        esp_err_t update_error = changed ? servo_output_set_us(pan_us, tilt_us) : ESP_OK;
        if (update_error != ESP_OK) {
            ESP_LOGE(TAG, "Servo update failed: %s; output stopped", esp_err_to_name(update_error));
            portENTER_CRITICAL(&s_control_lock);
            control_stop(&s_control);
            (void)relay_apply_locked();
            portEXIT_CRITICAL(&s_control_lock);
            servo_output_stop();
            hardware_active = false;
            continue;
        }
        // STOP or Thread loss may arrive while LEDC is updating.
        if (!control_snapshot().pwm_active) {
            servo_output_stop();
            hardware_active = false;
        }
    }
}

static void on_request(void *context, otMessage *request, const otMessageInfo *peer)
{
    const int resource = *(const int *)context;
    const uint64_t now_ms = (uint64_t)esp_timer_get_time() / 1000;
    const otCoapCode method = otCoapMessageGetCode(request);
    otCoapCode result;
    char body[512] = {0};

    if (resource <= PROBE_STOP) {
        result = (otCoapCode)probe_apply(&s_state, resource, method, now_ms);
        if (result == OT_COAP_CODE_CHANGED && resource == PROBE_HB) {
            portENTER_CRITICAL(&s_control_lock);
            control_heartbeat(&s_control, now_ms);
            portEXIT_CRITICAL(&s_control_lock);
        } else if (result == OT_COAP_CODE_CHANGED && resource == PROBE_STOP) {
            portENTER_CRITICAL(&s_control_lock);
            control_stop(&s_control);
            esp_err_t relay_error = relay_apply_locked();
            portEXIT_CRITICAL(&s_control_lock);
            wake_servo();
            if (relay_error != ESP_OK) {
                ESP_LOGE(TAG, "Relay STOP failed: %s", esp_err_to_name(relay_error));
                result = OT_COAP_CODE_INTERNAL_ERROR;
            }
        }
    } else if (resource == RESOURCE_PANTILT) {
        if (method == OT_COAP_CODE_GET) result = OT_COAP_CODE_CONTENT;
        else if (method == OT_COAP_CODE_POST) result = pantilt_post(request, now_ms);
        else result = OT_COAP_CODE_METHOD_NOT_ALLOWED;
    } else if (resource == RESOURCE_LASER) {
        if (method == OT_COAP_CODE_GET) result = OT_COAP_CODE_CONTENT;
        else if (method == OT_COAP_CODE_POST) result = laser_post(request, now_ms);
        else result = OT_COAP_CODE_METHOD_NOT_ALLOWED;
    } else if (resource == RESOURCE_MQ2) {
        result = method == OT_COAP_CODE_GET ? OT_COAP_CODE_CONTENT : OT_COAP_CODE_METHOD_NOT_ALLOWED;
    } else {
        result = OT_COAP_CODE_NOT_FOUND;
    }

    if (result == OT_COAP_CODE_CONTENT && resource == PROBE_INFO) {
        if (!node_a_info_json(esp_app_get_description()->version, body, sizeof(body))) {
            result = OT_COAP_CODE_INTERNAL_ERROR;
            body[0] = '\0';
        }
    } else if (result == OT_COAP_CODE_CONTENT && resource == PROBE_STATUS) {
        int64_t age = probe_hb_age_ms(&s_state, now_ms);
        control_state_t control = control_snapshot();
        mq2_state_t sensor = mq2_sensor_snapshot();
        if (!node_a_status_json(
                otThreadDeviceRoleToString(otThreadGetDeviceRole(esp_openthread_get_instance())),
                age, s_state.stop_count, &control, &sensor, now_ms, body, sizeof(body))) {
            result = OT_COAP_CODE_INTERNAL_ERROR;
            body[0] = '\0';
        }
    } else if (result == OT_COAP_CODE_CONTENT && resource == RESOURCE_PANTILT) {
        control_state_t control = control_snapshot();
        if (!control_pantilt_status_json(&control, body, sizeof(body))) {
            result = OT_COAP_CODE_INTERNAL_ERROR;
            body[0] = '\0';
        }
    } else if (result == OT_COAP_CODE_CONTENT && resource == RESOURCE_LASER) {
        control_state_t control = control_snapshot();
        snprintf(body, sizeof(body),
                 "{\"available\":true,\"mode\":\"relay_led_simulation\",\"on\":%s}",
                 control.laser_on ? "true" : "false");
    } else if (result == OT_COAP_CODE_CONTENT && resource == RESOURCE_MQ2) {
        mq2_state_t sensor = mq2_sensor_snapshot();
        if (!mq2_status_json(&sensor, now_ms, body, sizeof(body))) {
            result = OT_COAP_CODE_INTERNAL_ERROR;
            body[0] = '\0';
        }
    }

    otInstance *instance = esp_openthread_get_instance();
    otMessage *response = otCoapNewMessage(instance, NULL);
    if (response == NULL) return;
    otCoapType type = otCoapMessageGetType(request) == OT_COAP_TYPE_CONFIRMABLE
                          ? OT_COAP_TYPE_ACKNOWLEDGMENT : OT_COAP_TYPE_NON_CONFIRMABLE;
    otError error = otCoapMessageInitResponse(response, request, type, result);
    if (error == OT_ERROR_NONE && body[0] != '\0') {
        error = otCoapMessageAppendContentFormatOption(response, OT_COAP_OPTION_CONTENT_FORMAT_JSON);
        if (error == OT_ERROR_NONE) error = otCoapMessageSetPayloadMarker(response);
        if (error == OT_ERROR_NONE) error = otMessageAppend(response, body, (uint16_t)strlen(body));
    }
    if (error == OT_ERROR_NONE) error = otCoapSendResponse(instance, response, peer);
    if (error != OT_ERROR_NONE) {
        otMessageFree(response);
        ESP_LOGW(TAG, "CoAP response failed: %d", error);
    }
}

static otCoapResource s_resources[] = {
    {.mUriPath = "info", .mHandler = on_request, .mContext = (void *)&s_resource_ids[0]},
    {.mUriPath = "hb", .mHandler = on_request, .mContext = (void *)&s_resource_ids[1]},
    {.mUriPath = "status", .mHandler = on_request, .mContext = (void *)&s_resource_ids[2]},
    {.mUriPath = "stop", .mHandler = on_request, .mContext = (void *)&s_resource_ids[3]},
    {.mUriPath = "pantilt", .mHandler = on_request, .mContext = (void *)&s_resource_ids[4]},
    {.mUriPath = "laser", .mHandler = on_request, .mContext = (void *)&s_resource_ids[5]},
    {.mUriPath = "mq2", .mHandler = on_request, .mContext = (void *)&s_resource_ids[6]},
};

static void on_thread_state(otChangedFlags flags, void *context)
{
    otInstance *instance = context;
    if (!(flags & OT_CHANGED_THREAD_ROLE)) return;
    otDeviceRole role = otThreadGetDeviceRole(instance);
    portENTER_CRITICAL(&s_control_lock);
    control_set_attached(&s_control, role >= OT_DEVICE_ROLE_CHILD);
    esp_err_t relay_error = relay_apply_locked();
    portEXIT_CRITICAL(&s_control_lock);
    wake_servo();
    if (relay_error != ESP_OK) ESP_LOGE(TAG, "Relay role-change failed: %s", esp_err_to_name(relay_error));
    ESP_LOGI(TAG, "Thread role: %s", otThreadDeviceRoleToString(role));
    if (role >= OT_DEVICE_ROLE_CHILD) {
        char address[OT_IP6_ADDRESS_STRING_SIZE];
        otIp6AddressToString(otThreadGetMeshLocalEid(instance), address, sizeof(address));
        ESP_LOGI(TAG, "mesh-local EID: %s", address);
    }
}

static void clear_secret(void *memory, size_t length)
{
    volatile uint8_t *bytes = memory;
    while (length--) *bytes++ = 0;
}

static void start_thread(otInstance *instance)
{
    otError error;
    if (!thread_start_existing(instance, &error)) {
        ESP_LOGW(TAG, "No active dataset; Thread remains disabled");
        return;
    }
    ESP_LOGI(TAG, "Thread start: %s", otThreadErrorToString(error));
}

static void process_provision_line(char *line)
{
    bool is_start = probe_is_start(line);
    otOperationalDatasetTlvs dataset = {0};
    bool is_tlv = probe_parse_tlv(line, dataset.mTlvs, sizeof(dataset.mTlvs), &dataset.mLength);
    if (!is_start && !is_tlv) {
        ESP_LOGW(TAG, "Invalid provisioning command");
    } else if (esp_openthread_lock_acquire(pdMS_TO_TICKS(1000))) {
        otInstance *instance = esp_openthread_get_instance();
        if (is_tlv) {
            otError error = otDatasetSetActiveTlvs(instance, &dataset);
            ESP_LOGI(TAG, "Active dataset accepted: %s", otThreadErrorToString(error));
            if (error == OT_ERROR_NONE) start_thread(instance);
        } else {
            start_thread(instance);
        }
        esp_openthread_lock_release();
    } else {
        ESP_LOGW(TAG, "OpenThread busy; provisioning not applied");
    }
    clear_secret(&dataset, sizeof(dataset));
}

static void provision_task(void *unused)
{
    (void)unused;
    char line[PROVISION_LINE_CAPACITY] = {0};
    size_t used = 0;
    bool overflow = false;
    ESP_LOGI(TAG, "Ready for START or TLV:<active dataset hex> on USB serial; input is not logged");
    for (;;) {
        int ch = fgetc(stdin); // USB Serial/JTAG stdin is nonblocking and may return partial input.
        if (ch == EOF) {
            clearerr(stdin);
            vTaskDelay(pdMS_TO_TICKS(10));
            continue;
        }
        if (ch == '\n' || ch == '\r') {
            if (!overflow && used > 0) {
                line[used++] = '\n';
                line[used] = '\0';
                process_provision_line(line);
            } else if (overflow) {
                ESP_LOGW(TAG, "Provisioning line too long");
            }
            clear_secret(line, sizeof(line));
            used = 0;
            overflow = false;
        } else if (used < sizeof(line) - 2) {
            line[used++] = (char)ch;
        } else {
            overflow = true;
        }
    }
}

void app_main(void)
{
    // Hold active-HIGH relay and both servo signals LOW before NVS/radio startup.
    ESP_ERROR_CHECK(servo_output_init_safe());
    control_init(&s_control);
    ESP_ERROR_CHECK(xTaskCreate(servo_task, "servo", 4096, NULL, 3, &s_servo_task) == pdPASS
                        ? ESP_OK : ESP_ERR_NO_MEM);
    ESP_ERROR_CHECK(nvs_flash_init()); // Fail closed; never erase an existing dataset.
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    ESP_ERROR_CHECK(esp_netif_init());
    esp_vfs_eventfd_config_t eventfd = {.max_fds = 3};
    ESP_ERROR_CHECK(esp_vfs_eventfd_register(&eventfd));

    static esp_openthread_config_t config = {
        .netif_config = ESP_NETIF_DEFAULT_OPENTHREAD(),
        .platform_config = {
            .radio_config = {.radio_mode = RADIO_MODE_NATIVE},
            .host_config = {.host_connection_mode = HOST_CONNECTION_MODE_NONE},
            .port_config = {.storage_partition_name = "nvs", .netif_queue_size = 10, .task_queue_size = 10},
        },
    };
    ESP_ERROR_CHECK(esp_openthread_start(&config));
    ESP_ERROR_CHECK(esp_openthread_lock_acquire(portMAX_DELAY) ? ESP_OK : ESP_FAIL);
    otInstance *instance = esp_openthread_get_instance();
    otSetStateChangedCallback(instance, on_thread_state, instance);
    ESP_ERROR_CHECK(otCoapStart(instance, 5683) == OT_ERROR_NONE ? ESP_OK : ESP_FAIL);
    for (size_t i = 0; i < sizeof(s_resources) / sizeof(s_resources[0]); ++i) {
        otCoapAddResource(instance, &s_resources[i]);
    }
    start_thread(instance); // Reuse the stored active dataset after cold boot.
    esp_openthread_lock_release();
    xTaskCreate(provision_task, "provision", 4096, NULL, 4, NULL);
    esp_err_t sensor_error = mq2_sensor_start();
    if (sensor_error != ESP_OK) ESP_LOGW(TAG, "MQ-2 start failed: %s", esp_err_to_name(sensor_error));
    ESP_LOGI(TAG, "Node A integrated ready; relay OFF, servo PWM OFF, laser unavailable");
}
