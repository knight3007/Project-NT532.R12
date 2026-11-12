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

#include "probe_state.h"

static const char *TAG = "node_b_probe";
static probe_state_t s_state = {.stopped = true};
static const int s_resource_ids[] = {PROBE_INFO, PROBE_HB, PROBE_STATUS, PROBE_STOP};
enum { PROVISION_LINE_CAPACITY = 520 };
_Static_assert(PROVISION_LINE_CAPACITY >= 4 + 2 * OT_OPERATIONAL_DATASET_MAX_LENGTH + 3,
               "Provisioning line buffer is too small for a full dataset TLV");

static void on_request(void *context, otMessage *request, const otMessageInfo *peer)
{
    const int resource = *(const int *)context;
    const uint64_t now_ms = (uint64_t)esp_timer_get_time() / 1000;
    const uint8_t result = probe_apply(&s_state, resource, otCoapMessageGetCode(request), now_ms);
    char body[192] = {0};

    if (result == PROBE_CONTENT && resource == PROBE_INFO) {
        snprintf(body, sizeof(body),
                 "{\"n\":\"B\",\"fw\":\"%s\",\"mode\":\"probe\",\"outputs\":\"disabled\"}",
                 esp_app_get_description()->version);
    } else if (result == PROBE_CONTENT && resource == PROBE_STATUS) {
        int64_t age = probe_hb_age_ms(&s_state, now_ms);
        snprintf(body, sizeof(body),
                 "{\"n\":\"B\",\"role\":\"%s\",\"stopped\":true,\"hb_age_ms\":%lld,\"hb_fresh\":%s,\"stop_count\":%" PRIu32 "}",
                 otThreadDeviceRoleToString(otThreadGetDeviceRole(esp_openthread_get_instance())),
                 (long long)age, age >= 0 && age <= 1500 ? "true" : "false", s_state.stop_count);
    }

    otInstance *instance = esp_openthread_get_instance();
    otMessage *response = otCoapNewMessage(instance, NULL);
    if (response == NULL) return;
    otCoapType type = otCoapMessageGetType(request) == OT_COAP_TYPE_CONFIRMABLE
                          ? OT_COAP_TYPE_ACKNOWLEDGMENT : OT_COAP_TYPE_NON_CONFIRMABLE;
    otError error = otCoapMessageInitResponse(response, request, type, (otCoapCode)result);
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
};

static void on_thread_state(otChangedFlags flags, void *context)
{
    otInstance *instance = context;
    if (!(flags & OT_CHANGED_THREAD_ROLE)) return;
    otDeviceRole role = otThreadGetDeviceRole(instance);
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
    if (!otDatasetIsCommissioned(instance)) {
        ESP_LOGW(TAG, "No active dataset; Thread remains disabled");
        return;
    }
    otError error = otIp6SetEnabled(instance, true);
    if (error == OT_ERROR_NONE) error = otThreadSetEnabled(instance, true);
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
    // No GPIO, ADC, LEDC, relay, or servo driver is initialized by this probe.
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
    esp_openthread_lock_release();
    xTaskCreate(provision_task, "provision", 4096, NULL, 4, NULL);
    ESP_LOGI(TAG, "Node B probe ready; all external loads must remain unpowered");
}
