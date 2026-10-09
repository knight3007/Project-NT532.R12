// Node H2: cảm biến + vòi. Thứ tự khởi động có chủ đích: bơm và laser về mức thấp trước mọi thứ khác.
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_vfs_eventfd.h"
#include "nvs_flash.h"

#include "act_task.h"
#include "coap_node.h"
#include "hal_esp.h"
#include "net_ot.h"
#include "node_cfg.h"
#include "sensors.h"

static const char *TAG = "main";

void app_main(void)
{
    hal_safe_gpio_init();  // 1. bơm GPIO12, laser GPIO14, còi GPIO13 = thấp

    esp_err_t e = nvs_flash_init();
    if (e == ESP_ERR_NVS_NO_FREE_PAGES || e == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        e = nvs_flash_init();
    }
    ESP_ERROR_CHECK(e);

    node_cfg_t cfg;
    node_cfg_load(&cfg);
    ESP_LOGI(TAG, "NT532 node %s khởi động", cfg.node_id);

    hal_init(&cfg);

    ESP_ERROR_CHECK(esp_event_loop_create_default());
    ESP_ERROR_CHECK(esp_netif_init());
    // eventfd dùng bởi: netif, hàng đợi tác vụ OT, driver radio (như ví dụ ot_cli) và đánh thức task CoAP
    esp_vfs_eventfd_config_t eventfd_config = {.max_fds = 4};
    ESP_ERROR_CHECK(esp_vfs_eventfd_register(&eventfd_config));

    coap_node_init(&cfg);  // hàng đợi ra có trước để các task khác đẩy được
    act_task_start(&cfg);
    sensors_start(&cfg);
    net_ot_start(cfg.node_id);
    coap_node_start();
}
