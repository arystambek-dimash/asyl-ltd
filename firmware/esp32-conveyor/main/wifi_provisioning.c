#include "wifi_provisioning.h"

#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "device_config.h"
#include "esp_check.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_sntp.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "sdkconfig.h"
#include "wifi_provisioning/manager.h"
#include "wifi_provisioning/scheme_ble.h"

#define WIFI_CONNECTED_BIT BIT0
#define DEVICE_CONFIG_BIT BIT1
/* Keep the standard ESP-IDF endpoint name so the official esp_prov client can
 * send our encrypted JSON through --custom_data without a proprietary app. */
#define PROVISIONING_ENDPOINT "custom-data"

static const char *TAG = "asyl_wifi";
static EventGroupHandle_t s_events;
static esp_timer_handle_t s_reconnect_timer;
static uint32_t s_backoff_ms = 500;
static bool s_reconnect_enabled;
static bool s_sntp_started;
static uint8_t s_sec2_salt[64];
static uint8_t s_sec2_verifier[512];
static wifi_prov_security2_params_t s_security2_params;

static void schedule_reconnect(void);

static bool decode_hex(
    const char *source,
    uint8_t *destination,
    size_t capacity,
    size_t *decoded_length
) {
    if (source == NULL || destination == NULL || decoded_length == NULL) {
        return false;
    }
    const size_t length = strlen(source);
    if (length == 0 || (length % 2) != 0 || length / 2 > capacity) {
        return false;
    }
    for (size_t i = 0; i < length / 2; ++i) {
        char pair[3] = {source[i * 2], source[i * 2 + 1], '\0'};
        char *end = NULL;
        const unsigned long byte = strtoul(pair, &end, 16);
        if (end == NULL || *end != '\0' || byte > 0xff) {
            return false;
        }
        destination[i] = (uint8_t)byte;
    }
    *decoded_length = length / 2;
    return true;
}

static void start_sntp_once(void) {
    if (s_sntp_started) {
        return;
    }
    esp_sntp_setoperatingmode(SNTP_OPMODE_POLL);
    esp_sntp_setservername(0, "time.google.com");
    esp_sntp_init();
    s_sntp_started = true;
}

static void reconnect_timer_callback(void *argument) {
    (void)argument;
    if (s_reconnect_enabled && !asyl_wifi_is_connected()) {
        const esp_err_t error = esp_wifi_connect();
        if (error != ESP_OK) {
            ESP_LOGW(TAG, "Wi-Fi reconnect failed to start: %s",
                     esp_err_to_name(error));
            schedule_reconnect();
        }
    }
}

static void schedule_reconnect(void) {
    if (!s_reconnect_enabled || esp_timer_is_active(s_reconnect_timer)) {
        return;
    }
    ESP_LOGW(TAG, "Wi-Fi disconnected; retry in %" PRIu32 " ms", s_backoff_ms);
    esp_timer_start_once(s_reconnect_timer, (uint64_t)s_backoff_ms * 1000);
    if (s_backoff_ms < CONFIG_ASYL_WIFI_MAX_BACKOFF_MS) {
        s_backoff_ms *= 2;
        if (s_backoff_ms > CONFIG_ASYL_WIFI_MAX_BACKOFF_MS) {
            s_backoff_ms = CONFIG_ASYL_WIFI_MAX_BACKOFF_MS;
        }
    }
}

static void network_event_handler(
    void *argument,
    esp_event_base_t event_base,
    int32_t event_id,
    void *event_data
) {
    (void)argument;
    (void)event_data;
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        xEventGroupClearBits(s_events, WIFI_CONNECTED_BIT);
        schedule_reconnect();
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        if (esp_timer_is_active(s_reconnect_timer)) {
            esp_timer_stop(s_reconnect_timer);
        }
        s_backoff_ms = 500;
        xEventGroupSetBits(s_events, WIFI_CONNECTED_BIT);
        esp_wifi_set_ps(WIFI_PS_NONE);
        start_sntp_once();
        ESP_LOGI(TAG, "Wi-Fi connected and IP acquired");
    } else if (event_base == WIFI_PROV_EVENT) {
        if (event_id == WIFI_PROV_CRED_SUCCESS) {
            s_reconnect_enabled = true;
            ESP_LOGI(TAG, "Wi-Fi credentials accepted");
        } else if (event_id == WIFI_PROV_CRED_FAIL) {
            ESP_LOGE(TAG, "Wi-Fi provisioning credentials failed");
        } else if (event_id == WIFI_PROV_END) {
            s_reconnect_enabled = true;
            wifi_prov_mgr_deinit();
            ESP_LOGI(TAG, "BLE provisioning stopped");
        }
    }
}

static bool only_config_fields(cJSON *root) {
    for (cJSON *item = root == NULL ? NULL : root->child;
         item != NULL;
         item = item->next) {
        if (item->string == NULL ||
            (strcmp(item->string, "base_url") != 0 &&
            strcmp(item->string, "device_id") != 0 &&
            strcmp(item->string, "token") != 0)) {
            return false;
        }
    }
    return true;
}

static bool config_field_once(cJSON *root, const char *name, bool optional) {
    size_t count = 0;
    for (cJSON *item = root == NULL ? NULL : root->child;
         item != NULL;
         item = item->next) {
        if (item->string != NULL && strcmp(item->string, name) == 0) {
            ++count;
        }
    }
    return count == 1 || (optional && count == 0);
}

static esp_err_t provisioning_config_handler(
    uint32_t session_id,
    const uint8_t *input,
    ssize_t input_length,
    uint8_t **output,
    ssize_t *output_length,
    void *private_data
) {
    (void)session_id;
    (void)private_data;
    if (input == NULL || input_length <= 0 || input_length > 1024 ||
        output == NULL || output_length == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    cJSON *root = cJSON_ParseWithLength((const char *)input, (size_t)input_length);
    const cJSON *base_url = cJSON_GetObjectItemCaseSensitive(root, "base_url");
    const cJSON *device_id = cJSON_GetObjectItemCaseSensitive(root, "device_id");
    const cJSON *token = cJSON_GetObjectItemCaseSensitive(root, "token");
    asyl_device_config_t config = {0};
    bool valid = cJSON_IsObject(root) && only_config_fields(root) &&
                 config_field_once(root, "base_url", true) &&
                 config_field_once(root, "device_id", false) &&
                 config_field_once(root, "token", false) &&
                 (base_url == NULL || cJSON_IsString(base_url)) &&
                 cJSON_IsString(device_id) && cJSON_IsString(token);
    if (valid) {
        valid = strlen(device_id->valuestring) == ASYL_DEVICE_ID_LEN &&
                strlen(token->valuestring) == ASYL_DEVICE_TOKEN_LEN &&
                (base_url == NULL ||
                 strlen(base_url->valuestring) <= ASYL_BASE_URL_MAX);
    }
    if (valid) {
        snprintf(
            config.base_url,
            sizeof(config.base_url),
            "%s",
            base_url == NULL ? ASYL_PRODUCTION_API_BASE_URL
                             : base_url->valuestring
        );
        snprintf(config.device_id, sizeof(config.device_id), "%s",
                 device_id->valuestring);
        snprintf(config.token, sizeof(config.token), "%s", token->valuestring);
        valid = asyl_device_config_validate(&config);
    }

    esp_err_t save_error = ESP_ERR_INVALID_ARG;
    if (valid) {
        save_error = asyl_device_config_save(&config);
        if (save_error == ESP_OK) {
            xEventGroupSetBits(s_events, DEVICE_CONFIG_BIT);
        }
    }
    memset(&config, 0, sizeof(config));
    if (cJSON_IsString(token) && token->valuestring != NULL) {
        memset(token->valuestring, 0, strlen(token->valuestring));
    }
    cJSON_Delete(root);

    const char *response = save_error == ESP_OK
        ? "{\"ok\":true}"
        : "{\"ok\":false,\"error\":\"invalid_device_config\"}";
    *output_length = (ssize_t)strlen(response) + 1;
    *output = (uint8_t *)strdup(response);
    if (*output == NULL) {
        *output_length = 0;
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

static esp_err_t start_secure_ble_provisioning(void) {
    size_t salt_length = 0;
    size_t verifier_length = 0;
    if (!decode_hex(
            CONFIG_ASYL_PROV_SEC2_SALT_HEX,
            s_sec2_salt,
            sizeof(s_sec2_salt),
            &salt_length
        ) ||
        !decode_hex(
            CONFIG_ASYL_PROV_SEC2_VERIFIER_HEX,
            s_sec2_verifier,
            sizeof(s_sec2_verifier),
            &verifier_length
        ) || salt_length != 16 || verifier_length != 384) {
        ESP_LOGE(
            TAG,
            "Security2 salt/verifier missing or invalid; relay remains OFF"
        );
        return ESP_ERR_INVALID_STATE;
    }

    uint8_t mac[6];
    ESP_RETURN_ON_ERROR(
        esp_read_mac(mac, ESP_MAC_WIFI_STA), TAG, "cannot read station MAC"
    );
    char service_name[32];
    snprintf(
        service_name,
        sizeof(service_name),
        "%s-%02X%02X%02X",
        CONFIG_ASYL_PROV_SERVICE_PREFIX,
        mac[3], mac[4], mac[5]
    );

    s_security2_params = (wifi_prov_security2_params_t){
        .salt = (const char *)s_sec2_salt,
        .salt_len = (uint16_t)salt_length,
        .verifier = (const char *)s_sec2_verifier,
        .verifier_len = (uint16_t)verifier_length,
    };
    ESP_RETURN_ON_ERROR(
        wifi_prov_mgr_endpoint_create(PROVISIONING_ENDPOINT),
        TAG,
        "cannot create provisioning endpoint"
    );
    ESP_RETURN_ON_ERROR(
        wifi_prov_mgr_start_provisioning(
            WIFI_PROV_SECURITY_2, &s_security2_params, service_name, NULL
        ),
        TAG,
        "cannot start BLE provisioning"
    );
    ESP_RETURN_ON_ERROR(
        wifi_prov_mgr_endpoint_register(
            PROVISIONING_ENDPOINT, provisioning_config_handler, NULL
        ),
        TAG,
        "cannot register provisioning endpoint"
    );
    ESP_LOGI(TAG, "secure BLE provisioning ready as %s", service_name);
    return ESP_OK;
}

esp_err_t asyl_wifi_start(bool force_provisioning) {
    s_events = xEventGroupCreate();
    if (s_events == NULL) {
        return ESP_ERR_NO_MEM;
    }
    const esp_timer_create_args_t timer_args = {
        .callback = reconnect_timer_callback,
        .name = "wifi_retry",
    };
    ESP_RETURN_ON_ERROR(
        esp_timer_create(&timer_args, &s_reconnect_timer),
        TAG,
        "cannot create reconnect timer"
    );
    ESP_RETURN_ON_ERROR(
        esp_event_handler_register(
            WIFI_EVENT, ESP_EVENT_ANY_ID, network_event_handler, NULL
        ),
        TAG,
        "cannot register Wi-Fi handler"
    );
    ESP_RETURN_ON_ERROR(
        esp_event_handler_register(
            IP_EVENT, IP_EVENT_STA_GOT_IP, network_event_handler, NULL
        ),
        TAG,
        "cannot register IP handler"
    );
    ESP_RETURN_ON_ERROR(
        esp_event_handler_register(
            WIFI_PROV_EVENT, ESP_EVENT_ANY_ID, network_event_handler, NULL
        ),
        TAG,
        "cannot register provisioning handler"
    );

    wifi_init_config_t wifi_config = WIFI_INIT_CONFIG_DEFAULT();
    ESP_RETURN_ON_ERROR(
        esp_wifi_init(&wifi_config), TAG, "Wi-Fi driver init failed"
    );
    ESP_RETURN_ON_ERROR(
        esp_wifi_set_storage(WIFI_STORAGE_FLASH),
        TAG,
        "cannot select persistent Wi-Fi storage"
    );

    wifi_prov_mgr_config_t manager_config = {
        .scheme = wifi_prov_scheme_ble,
        .scheme_event_handler = WIFI_PROV_SCHEME_BLE_EVENT_HANDLER_FREE_BTDM,
    };
    ESP_RETURN_ON_ERROR(
        wifi_prov_mgr_init(manager_config), TAG, "provisioning manager init failed"
    );

    bool wifi_provisioned = false;
    ESP_RETURN_ON_ERROR(
        wifi_prov_mgr_is_provisioned(&wifi_provisioned),
        TAG,
        "cannot inspect provisioning state"
    );
    asyl_device_config_t device_config;
    bool config_ready = asyl_device_config_load(&device_config) == ESP_OK;
    if (config_ready) {
        xEventGroupSetBits(s_events, DEVICE_CONFIG_BIT);
    }

    if (force_provisioning || (wifi_provisioned && !config_ready)) {
        ESP_RETURN_ON_ERROR(
            wifi_prov_mgr_reset_provisioning(),
            TAG,
            "cannot reset Wi-Fi provisioning"
        );
        wifi_provisioned = false;
    }
    if (!wifi_provisioned || !config_ready) {
        s_reconnect_enabled = false;
        return start_secure_ble_provisioning();
    }

    wifi_prov_mgr_deinit();
    s_reconnect_enabled = true;
    ESP_RETURN_ON_ERROR(
        esp_wifi_set_mode(WIFI_MODE_STA), TAG, "cannot select station mode"
    );
    ESP_RETURN_ON_ERROR(esp_wifi_start(), TAG, "cannot start Wi-Fi");
    ESP_RETURN_ON_ERROR(
        esp_wifi_set_ps(WIFI_PS_NONE), TAG, "cannot disable Wi-Fi power save"
    );
    const esp_err_t connect_error = esp_wifi_connect();
    if (connect_error != ESP_OK) {
        schedule_reconnect();
    }
    return ESP_OK;
}

bool asyl_wifi_wait_ready(uint32_t timeout_ms) {
    if (s_events == NULL) {
        return false;
    }
    const EventBits_t bits = xEventGroupWaitBits(
        s_events,
        WIFI_CONNECTED_BIT | DEVICE_CONFIG_BIT,
        pdFALSE,
        pdTRUE,
        pdMS_TO_TICKS(timeout_ms)
    );
    return (bits & (WIFI_CONNECTED_BIT | DEVICE_CONFIG_BIT)) ==
           (WIFI_CONNECTED_BIT | DEVICE_CONFIG_BIT);
}

bool asyl_wifi_is_connected(void) {
    return s_events != NULL &&
           (xEventGroupGetBits(s_events) & WIFI_CONNECTED_BIT) != 0;
}

int asyl_wifi_rssi(void) {
    wifi_ap_record_t access_point;
    if (!asyl_wifi_is_connected() ||
        esp_wifi_sta_get_ap_info(&access_point) != ESP_OK) {
        return -127;
    }
    return access_point.rssi;
}
