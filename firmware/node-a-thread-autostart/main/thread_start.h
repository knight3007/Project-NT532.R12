#pragma once

#include <stdbool.h>
#include "openthread/error.h"
#include "openthread/instance.h"

// Returns false when no valid active dataset exists; no network is created.
bool thread_start_existing(otInstance *instance, otError *error);
