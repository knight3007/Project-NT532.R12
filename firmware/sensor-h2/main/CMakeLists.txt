# core/ là C thuần, test trên máy bằng `make -C firmware/sensor-h2/test`; ở đây biên dịch lại cho H2.
idf_component_register(
    SRCS
        app_main.c
        node_cfg.c
        hal_esp.c
        sensors.c
        act_task.c
        net_ot.c
        coap_node.c
        core/actuator.c
        core/alarm.c
        core/proto.c
    INCLUDE_DIRS
        "."
        "core"
    PRIV_REQUIRES
        esp_app_format nvs_flash esp_event esp_netif esp_timer vfs openthread
        esp_driver_gpio esp_driver_ledc esp_driver_i2c esp_adc
        espressif__coap
)
