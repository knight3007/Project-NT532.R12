#include <assert.h>

#include "thread_start.h"
#include "openthread/dataset.h"
#include "openthread/ip6.h"
#include "openthread/thread.h"

bool otDatasetIsCommissioned(otInstance *instance) { return instance->commissioned; }

otError otIp6SetEnabled(otInstance *instance, bool enabled)
{
    ++instance->ip6_calls;
    if (instance->fail_ip6) return OT_ERROR_FAILED;
    instance->ip6_enabled = enabled;
    return OT_ERROR_NONE;
}

otError otThreadSetEnabled(otInstance *instance, bool enabled)
{
    ++instance->thread_calls;
    if (instance->fail_thread) return OT_ERROR_FAILED;
    instance->thread_enabled = enabled;
    return OT_ERROR_NONE;
}

int main(void)
{
    otError error = OT_ERROR_FAILED;

    // Cold boot with the previously stored active dataset joins without Serial.
    otInstance commissioned = {.commissioned = true};
    assert(thread_start_existing(&commissioned, &error));
    assert(error == OT_ERROR_NONE && commissioned.ip6_enabled && commissioned.thread_enabled);
    assert(commissioned.ip6_calls == 1 && commissioned.thread_calls == 1);

    // A later cold boot uses the same dataset and enables the stack again.
    commissioned.ip6_enabled = false;
    commissioned.thread_enabled = false;
    assert(thread_start_existing(&commissioned, &error));
    assert(error == OT_ERROR_NONE && commissioned.ip6_enabled && commissioned.thread_enabled);

    // Empty NVS/dataset leaves radio disabled until provisioning or START.
    otInstance fresh = {0};
    assert(!thread_start_existing(&fresh, &error));
    assert(error == OT_ERROR_NONE && fresh.ip6_calls == 0 && fresh.thread_calls == 0);

    // Manual START after a dataset is later provisioned still works.
    fresh.commissioned = true;
    assert(thread_start_existing(&fresh, &error));
    assert(error == OT_ERROR_NONE && fresh.ip6_enabled && fresh.thread_enabled);
    assert(thread_start_existing(&fresh, &error)); // Repeated START is harmless.
    assert(error == OT_ERROR_NONE && fresh.ip6_enabled && fresh.thread_enabled);

    // An IP6 error must not attempt to enable Thread.
    otInstance failed = {.commissioned = true, .fail_ip6 = true};
    assert(thread_start_existing(&failed, &error));
    assert(error == OT_ERROR_FAILED && failed.thread_calls == 0);
    return 0;
}
