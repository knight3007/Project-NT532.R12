#pragma once
#include <stdbool.h>
typedef struct otInstance {
    bool commissioned;
    bool ip6_enabled;
    bool thread_enabled;
    bool fail_ip6;
    bool fail_thread;
    unsigned ip6_calls;
    unsigned thread_calls;
} otInstance;
