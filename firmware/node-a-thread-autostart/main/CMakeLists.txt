idf_component_register(SRCS "app_main.c" "probe_state.c" "thread_start.c"
                       INCLUDE_DIRS "."
                       PRIV_REQUIRES esp_app_format esp_event esp_netif esp_timer nvs_flash openthread vfs)
