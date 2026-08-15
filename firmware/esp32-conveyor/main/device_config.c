#include "device_config.h"

#include <ctype.h>
#include <stddef.h>
#include <string.h>

#include "nvs.h"
#include "sdkconfig.h"

#define NVS_NAMESPACE "asyl_ctrl"
#define KEY_BASE_URL "base_url"
#define KEY_DEVICE_ID "device_id"
#define KEY_TOKEN "token"
#define KEY_LAST_REV "last_rev"
#define KEY_BLOCKED_REV "blocked_rev"

static bool canonical_uuid(const char *value) {
    if (value == NULL || strlen(value) != ASYL_DEVICE_ID_LEN) {
        return false;
    }
    for (size_t i = 0; i < ASYL_DEVICE_ID_LEN; ++i) {
        const bool hyphen = i == 8 || i == 13 || i == 18 || i == 23;
        const bool lowercase_hex =
            (value[i] >= '0' && value[i] <= '9') ||
            (value[i] >= 'a' && value[i] <= 'f');
        if (hyphen ? value[i] != '-' : !lowercase_hex) {
            return false;
        }
    }
    const bool rfc4122_variant = value[19] == '8' || value[19] == '9' ||
                                 value[19] == 'a' || value[19] == 'b';
    return value[14] == '4' && rfc4122_variant;
}

static bool valid_base_url(const char *value) {
    if (value == NULL) {
        return false;
    }
    const size_t length = strlen(value);
    if (length < strlen("https://a.b") || length > ASYL_BASE_URL_MAX ||
        strncmp(value, "https://", strlen("https://")) != 0 ||
        value[length - 1] == '/') {
        return false;
    }
    for (size_t i = strlen("https://"); i < length; ++i) {
        const unsigned char c = (unsigned char)value[i];
        if (isspace(c) || c < 0x21 || c > 0x7e || c == '@' || c == '?' ||
            c == '#') {
            return false;
        }
    }
#ifndef CONFIG_ASYL_ALLOW_CUSTOM_API_BASE_URL
    if (strcmp(value, ASYL_PRODUCTION_API_BASE_URL) != 0) {
        return false;
    }
#endif
    return true;
}

static bool valid_token(const char *value) {
    if (value == NULL) {
        return false;
    }
    const size_t length = strlen(value);
    if (length != ASYL_DEVICE_TOKEN_LEN) {
        return false;
    }
    for (size_t i = 0; i < length; ++i) {
        const unsigned char c = (unsigned char)value[i];
        if (!(isalnum(c) || c == '-' || c == '_')) {
            return false;
        }
    }
    return true;
}

bool asyl_device_config_validate(const asyl_device_config_t *config) {
    return config != NULL && valid_base_url(config->base_url) &&
           canonical_uuid(config->device_id) && valid_token(config->token);
}

static esp_err_t get_string(
    nvs_handle_t handle,
    const char *key,
    char *destination,
    size_t capacity
) {
    size_t required = capacity;
    const esp_err_t error = nvs_get_str(handle, key, destination, &required);
    if (error != ESP_OK) {
        return error;
    }
    if (required == 0 || required > capacity ||
        destination[capacity - 1] != '\0') {
        return ESP_ERR_INVALID_SIZE;
    }
    return ESP_OK;
}

esp_err_t asyl_device_config_load(asyl_device_config_t *config) {
    if (config == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(config, 0, sizeof(*config));
    nvs_handle_t handle;
    esp_err_t error = nvs_open(NVS_NAMESPACE, NVS_READONLY, &handle);
    if (error != ESP_OK) {
        return error;
    }
    error = get_string(
        handle, KEY_BASE_URL, config->base_url, sizeof(config->base_url)
    );
    if (error == ESP_OK) {
        error = get_string(
            handle, KEY_DEVICE_ID, config->device_id, sizeof(config->device_id)
        );
    }
    if (error == ESP_OK) {
        error = get_string(handle, KEY_TOKEN, config->token, sizeof(config->token));
    }
    nvs_close(handle);
    if (error == ESP_OK && !asyl_device_config_validate(config)) {
        memset(config, 0, sizeof(*config));
        return ESP_ERR_INVALID_STATE;
    }
    return error;
}

esp_err_t asyl_device_config_save(const asyl_device_config_t *config) {
    if (!asyl_device_config_validate(config)) {
        return ESP_ERR_INVALID_ARG;
    }
    nvs_handle_t handle;
    esp_err_t error = nvs_open(NVS_NAMESPACE, NVS_READWRITE, &handle);
    if (error != ESP_OK) {
        return error;
    }
    error = nvs_set_str(handle, KEY_BASE_URL, config->base_url);
    if (error == ESP_OK) {
        error = nvs_set_str(handle, KEY_DEVICE_ID, config->device_id);
    }
    if (error == ESP_OK) {
        error = nvs_set_str(handle, KEY_TOKEN, config->token);
    }
    if (error == ESP_OK) {
        error = nvs_commit(handle);
    }
    nvs_close(handle);
    return error;
}

esp_err_t asyl_device_config_erase(void) {
    nvs_handle_t handle;
    esp_err_t error = nvs_open(NVS_NAMESPACE, NVS_READWRITE, &handle);
    if (error != ESP_OK) {
        return error;
    }
    const char *keys[] = {KEY_BASE_URL, KEY_DEVICE_ID, KEY_TOKEN};
    for (size_t i = 0; i < sizeof(keys) / sizeof(keys[0]); ++i) {
        const esp_err_t erase_error = nvs_erase_key(handle, keys[i]);
        if (erase_error != ESP_OK && erase_error != ESP_ERR_NVS_NOT_FOUND) {
            error = erase_error;
            break;
        }
    }
    if (error == ESP_OK) {
        error = nvs_commit(handle);
    }
    nvs_close(handle);
    return error;
}

esp_err_t asyl_safety_state_load(
    uint64_t *last_revision,
    uint64_t *blocked_revision
) {
    if (last_revision == NULL || blocked_revision == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    *last_revision = 0;
    *blocked_revision = 0;
    nvs_handle_t handle;
    esp_err_t error = nvs_open(NVS_NAMESPACE, NVS_READONLY, &handle);
    if (error == ESP_ERR_NVS_NOT_FOUND) {
        return ESP_OK;
    }
    if (error != ESP_OK) {
        return error;
    }
    error = nvs_get_u64(handle, KEY_LAST_REV, last_revision);
    if (error == ESP_ERR_NVS_NOT_FOUND) {
        error = ESP_OK;
    }
    if (error == ESP_OK) {
        esp_err_t blocked_error = nvs_get_u64(
            handle, KEY_BLOCKED_REV, blocked_revision
        );
        if (blocked_error != ESP_OK && blocked_error != ESP_ERR_NVS_NOT_FOUND) {
            error = blocked_error;
        }
    }
    nvs_close(handle);
    return error;
}

static esp_err_t store_monotonic(const char *key, uint64_t revision) {
    nvs_handle_t handle;
    esp_err_t error = nvs_open(NVS_NAMESPACE, NVS_READWRITE, &handle);
    if (error != ESP_OK) {
        return error;
    }
    uint64_t current = 0;
    error = nvs_get_u64(handle, key, &current);
    if (error == ESP_ERR_NVS_NOT_FOUND) {
        error = ESP_OK;
    }
    if (error == ESP_OK && revision > current) {
        error = nvs_set_u64(handle, key, revision);
        if (error == ESP_OK) {
            error = nvs_commit(handle);
        }
    }
    nvs_close(handle);
    return error;
}

esp_err_t asyl_safety_state_store_revision(uint64_t revision) {
    return store_monotonic(KEY_LAST_REV, revision);
}

esp_err_t asyl_safety_state_store_blocked(uint64_t revision) {
    return store_monotonic(KEY_BLOCKED_REV, revision);
}
