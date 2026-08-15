#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

esp_err_t asyl_wifi_start(bool force_provisioning);
bool asyl_wifi_wait_ready(uint32_t timeout_ms);
bool asyl_wifi_is_connected(void);
int asyl_wifi_rssi(void);
