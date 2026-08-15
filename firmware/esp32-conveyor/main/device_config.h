#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

#define ASYL_BASE_URL_MAX 191
#define ASYL_PRODUCTION_API_BASE_URL "https://asyl-ltd.kz/api"
#define ASYL_DEVICE_ID_LEN 36
#define ASYL_DEVICE_TOKEN_LEN 43
#define ASYL_DEVICE_TOKEN_MAX ASYL_DEVICE_TOKEN_LEN

typedef struct {
    char base_url[ASYL_BASE_URL_MAX + 1];
    char device_id[ASYL_DEVICE_ID_LEN + 1];
    char token[ASYL_DEVICE_TOKEN_MAX + 1];
} asyl_device_config_t;

bool asyl_device_config_validate(const asyl_device_config_t *config);
esp_err_t asyl_device_config_load(asyl_device_config_t *config);
esp_err_t asyl_device_config_save(const asyl_device_config_t *config);
esp_err_t asyl_device_config_erase(void);

esp_err_t asyl_safety_state_load(
    uint64_t *last_revision,
    uint64_t *blocked_revision
);
esp_err_t asyl_safety_state_store_revision(uint64_t revision);
esp_err_t asyl_safety_state_store_blocked(uint64_t revision);
