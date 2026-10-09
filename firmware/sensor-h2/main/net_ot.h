// Thread (OpenThread FTD, radio gốc của H2) và netif lwIP để dùng socket UDP/IPv6 cho libcoap.
#pragma once

#include <stdbool.h>

// Cần esp_event_loop, esp_netif_init và esp_vfs_eventfd_register đã gọi (app_main lo).
void net_ot_start(void);
// Chặn tới khi node gắn vào mạng Thread (vai trò child, router hoặc leader). Nhiều task gọi được.
void net_ot_wait_attached(void);
bool net_ot_is_attached(void);
