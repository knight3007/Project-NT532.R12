#include "net_ot.h"

#include <stdio.h>
#include <string.h>

#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_netif_types.h"
#include "esp_openthread.h"
#include "esp_openthread_lock.h"
#include "esp_openthread_netif_glue.h"
#include "esp_openthread_types.h"
#include "esp_vfs_eventfd.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "openthread/dataset.h"
#include "openthread/instance.h"
#include "openthread/ip6.h"
#include "openthread/thread.h"
#include "sdkconfig.h"

static const char *TAG = "ot";

#define ATTACHED_BIT BIT0

static EventGroupHandle_t s_ev;
static otDeviceRole s_last_role = OT_DEVICE_ROLE_DISABLED;

// "00ff..." -> byte; trả số byte, 0 nếu sai
static size_t hex_to_bin(const char *hex, uint8_t *out, size_t cap)
{
    size_t n = strlen(hex);
    if (n % 2 || n / 2 > cap) {
        return 0;
    }
    for (size_t i = 0; i < n / 2; i++) {
        unsigned v;
        if (sscanf(hex + 2 * i, "%2x", &v) != 1) {
            return 0;
        }
        out[i] = (uint8_t)v;
    }
    return n / 2;
}

// Ghi địa chỉ của node để dán vào config/site.yaml (network.nodes).
static void log_addresses(otInstance *inst)
{
    const otIp6Address *eid = otThreadGetMeshLocalEid(inst);
    for (const otNetifAddress *a = otIp6GetUnicastAddresses(inst); a; a = a->mNext) {
        char s[OT_IP6_ADDRESS_STRING_SIZE];
        otIp6AddressToString(&a->mAddress, s, sizeof(s));
        const char *kind = otIp6IsAddressEqual(&a->mAddress, eid) ? "mesh-local EID  <- dùng cho site.yaml"
                           : a->mRloc                           ? "RLOC"
                           : a->mMeshLocal                      ? "mesh-local khác"
                           : (a->mAddress.mFields.m8[0] == 0xfe && (a->mAddress.mFields.m8[1] & 0xc0) == 0x80)
                               ? "link-local"
                               : "OMR/GUA (qua border router)";
        ESP_LOGI(TAG, "địa chỉ %s  [%s]", s, kind);
    }
}

static void on_state_changed(otChangedFlags flags, void *ctx)
{
    otInstance *inst = ctx;  // gọi trong task OT, đã giữ khóa OT
    if (flags & OT_CHANGED_THREAD_ROLE) {
        otDeviceRole role = otThreadGetDeviceRole(inst);
        ESP_LOGW(TAG, "vai trò Thread: %s -> %s", otThreadDeviceRoleToString(s_last_role),
                 otThreadDeviceRoleToString(role));
        bool was = s_last_role >= OT_DEVICE_ROLE_CHILD;
        bool now = role >= OT_DEVICE_ROLE_CHILD;
        if (was && !now) {
            // Mất mạng: watchdog heartbeat của actuator sẽ tự tắt bơm và laser, không cần xử lý thêm.
            ESP_LOGE(TAG, "MẤT liên kết Thread; bơm/laser sẽ tắt khi hb quá hạn");
        }
        s_last_role = role;
        if (now) {
            xEventGroupSetBits(s_ev, ATTACHED_BIT);
        } else {
            xEventGroupClearBits(s_ev, ATTACHED_BIT);
        }
    }
    if (flags & (OT_CHANGED_IP6_ADDRESS_ADDED | OT_CHANGED_THREAD_ROLE)) {
        log_addresses(inst);
    }
}

static bool apply_dataset(otInstance *inst)
{
    otOperationalDataset ds;
    memset(&ds, 0, sizeof(ds));

    ds.mActiveTimestamp.mSeconds = 1;
    ds.mComponents.mIsActiveTimestampPresent = true;

    ds.mChannel = CONFIG_NT532_THREAD_CHANNEL;
    ds.mComponents.mIsChannelPresent = true;
    ds.mPanId = CONFIG_NT532_THREAD_PANID;
    ds.mComponents.mIsPanIdPresent = true;

    size_t nl = strlen(CONFIG_NT532_THREAD_NETWORK_NAME);
    if (nl == 0 || nl > OT_NETWORK_NAME_MAX_SIZE) {
        ESP_LOGE(TAG, "network name phải dài 1..%d ký tự", OT_NETWORK_NAME_MAX_SIZE);
        return false;
    }
    memcpy(ds.mNetworkName.m8, CONFIG_NT532_THREAD_NETWORK_NAME, nl + 1);
    ds.mComponents.mIsNetworkNamePresent = true;

    if (hex_to_bin(CONFIG_NT532_THREAD_EXTPANID, ds.mExtendedPanId.m8, sizeof(ds.mExtendedPanId.m8)) !=
        sizeof(ds.mExtendedPanId.m8)) {
        ESP_LOGE(TAG, "extended PAN ID phải là 16 chữ số hex");
        return false;
    }
    ds.mComponents.mIsExtendedPanIdPresent = true;

    if (hex_to_bin(CONFIG_NT532_THREAD_NETWORK_KEY, ds.mNetworkKey.m8, sizeof(ds.mNetworkKey.m8)) !=
        sizeof(ds.mNetworkKey.m8)) {
        ESP_LOGE(TAG, "network key phải là 32 chữ số hex");
        return false;
    }
    ds.mComponents.mIsNetworkKeyPresent = true;

    otIp6Prefix prefix;
    memset(&prefix, 0, sizeof(prefix));
    if (otIp6PrefixFromString(CONFIG_NT532_THREAD_MESH_LOCAL_PREFIX, &prefix) != OT_ERROR_NONE) {
        ESP_LOGE(TAG, "mesh-local prefix sai: %s", CONFIG_NT532_THREAD_MESH_LOCAL_PREFIX);
        return false;
    }
    memcpy(ds.mMeshLocalPrefix.m8, prefix.mPrefix.mFields.m8, sizeof(ds.mMeshLocalPrefix.m8));
    ds.mComponents.mIsMeshLocalPrefixPresent = true;

    // Không đặt PSKc: node không làm commissioner. CHƯA KIỂM: OT bản trong IDF có đòi đủ trường (kênh mask,
    // security policy) khi otDatasetSetActive hay không; ví dụ ot_cli auto_start cũng chỉ đặt các trường trên.
    if (otDatasetSetActive(inst, &ds) != OT_ERROR_NONE) {
        ESP_LOGE(TAG, "otDatasetSetActive lỗi");
        return false;
    }
    return true;
}

static void ot_task(void *arg)
{
    (void)arg;
    esp_openthread_platform_config_t config = {
        .radio_config = {.radio_mode = RADIO_MODE_NATIVE},
        .host_config = {.host_connection_mode = HOST_CONNECTION_MODE_NONE},
        .port_config = {.storage_partition_name = "nvs", .netif_queue_size = 10, .task_queue_size = 10},
    };
    ESP_ERROR_CHECK(esp_openthread_init(&config));
    otInstance *inst = esp_openthread_get_instance();

    esp_netif_config_t cfg = ESP_NETIF_DEFAULT_OPENTHREAD();
    esp_netif_t *netif = esp_netif_new(&cfg);
    assert(netif != NULL);
    ESP_ERROR_CHECK(esp_netif_attach(netif, esp_openthread_netif_glue_init(&config)));
    esp_netif_set_default_netif(netif);

    otSetStateChangedCallback(inst, on_state_changed, inst);
    if (apply_dataset(inst) && otIp6SetEnabled(inst, true) == OT_ERROR_NONE &&
        otThreadSetEnabled(inst, true) == OT_ERROR_NONE) {
        ESP_LOGI(TAG, "Thread bật (FTD), kênh %d, tên mạng %s; đang gắn vào mạng", CONFIG_NT532_THREAD_CHANNEL,
                 CONFIG_NT532_THREAD_NETWORK_NAME);
    } else {
        ESP_LOGE(TAG, "không bật được Thread; sửa dataset trong menuconfig");
    }
    // TODO tuần 4: đăng ký dịch vụ qua SRP client (otSrpClient*) để Pi tìm node bằng DNS-SD thay vì site.yaml.

    esp_openthread_launch_mainloop();  // không trở về trừ khi lỗi

    esp_openthread_netif_glue_deinit();
    esp_netif_destroy(netif);
    esp_vfs_eventfd_unregister();
    vTaskDelete(NULL);
}

void net_ot_start(void)
{
    s_ev = xEventGroupCreate();
    configASSERT(s_ev);
    xTaskCreate(ot_task, "ot_main", 10240, NULL, 5, NULL);
}

void net_ot_wait_attached(void)
{
    xEventGroupWaitBits(s_ev, ATTACHED_BIT, pdFALSE, pdTRUE, portMAX_DELAY);
}

bool net_ot_is_attached(void)
{
    return (xEventGroupGetBits(s_ev) & ATTACHED_BIT) != 0;
}
