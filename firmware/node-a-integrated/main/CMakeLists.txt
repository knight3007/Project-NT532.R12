idf_component_register(SRCS "app_main.c" "probe_state.c" "thread_start.c" "mq2_logic.c" "mq2_adc.c"
                            "mq2_sensor.c" "node_a_response.c" "integrated_control.c" "servo_output.c"
                       INCLUDE_DIRS "."
                       PRIV_REQUIRES esp_adc esp_app_format esp_driver_gpio esp_driver_ledc esp_event esp_netif esp_timer nvs_flash openthread vfs)
