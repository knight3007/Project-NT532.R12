#include "thread_start.h"

#include "openthread/dataset.h"
#include "openthread/ip6.h"
#include "openthread/thread.h"

bool thread_start_existing(otInstance *instance, otError *error)
{
    *error = OT_ERROR_NONE;
    if (!otDatasetIsCommissioned(instance)) return false;

    *error = otIp6SetEnabled(instance, true);
    if (*error == OT_ERROR_NONE) *error = otThreadSetEnabled(instance, true);
    return true;
}
