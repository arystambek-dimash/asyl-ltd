#include "api_client.h"

#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

#include "cJSON.h"
#include "command_guard.h"
#include "conveyor_control.h"
#include "device_config.h"
#include "esp_crt_bundle.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_random.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "sdkconfig.h"
#include "wifi_provisioning.h"

#define RESPONSE_CAPACITY 2048
#define API_URL_CAPACITY (ASYL_BASE_URL_MAX + 96)
#define AUTHORIZATION_CAPACITY 96

typedef struct {
    char bytes[RESPONSE_CAPACITY + 1];
    size_t length;
    bool overflow;
} response_buffer_t;

typedef struct {
    esp_http_client_handle_t client;
    response_buffer_t response;
} sync_http_client_t;

static const char *TAG = "asyl_api";
static char s_boot_id[37];
static uint64_t s_sequence;

static void make_boot_id(void) {
    uint8_t value[16];
    esp_fill_random(value, sizeof(value));
    value[6] = (value[6] & 0x0f) | 0x40;
    value[8] = (value[8] & 0x3f) | 0x80;
    snprintf(
        s_boot_id,
        sizeof(s_boot_id),
        "%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-"
        "%02x%02x%02x%02x%02x%02x",
        value[0], value[1], value[2], value[3],
        value[4], value[5], value[6], value[7],
        value[8], value[9], value[10], value[11],
        value[12], value[13], value[14], value[15]
    );
}

static bool json_u64(const cJSON *item, uint64_t *destination) {
    if (!cJSON_IsNumber(item) || destination == NULL ||
        !isfinite(item->valuedouble) || item->valuedouble < 0 ||
        item->valuedouble > 9007199254740991.0 ||
        floor(item->valuedouble) != item->valuedouble) {
        return false;
    }
    *destination = (uint64_t)item->valuedouble;
    return true;
}

static bool json_u32(const cJSON *item, uint32_t *destination) {
    uint64_t value;
    if (!json_u64(item, &value) || value > UINT32_MAX || destination == NULL) {
        return false;
    }
    *destination = (uint32_t)value;
    return true;
}

static bool object_has_only(const cJSON *object, const char *const *names) {
    if (!cJSON_IsObject(object)) {
        return false;
    }
    for (const cJSON *item = object->child; item != NULL; item = item->next) {
        bool known = false;
        for (size_t i = 0; names[i] != NULL; ++i) {
            if (item->string != NULL && strcmp(item->string, names[i]) == 0) {
                known = true;
                break;
            }
        }
        if (!known) {
            return false;
        }
    }
    return true;
}

static bool field_occurs_once(const cJSON *object, const char *name) {
    size_t occurrences = 0;
    for (const cJSON *item = object->child; item != NULL; item = item->next) {
        if (item->string != NULL && strcmp(item->string, name) == 0) {
            ++occurrences;
        }
    }
    return occurrences == 1;
}

static bool parse_response(
    const char *body,
    asyl_command_t *command,
    uint32_t *next_sync_ms
) {
    static const char *const ROOT_FIELDS[] = {
        "protocol_version", "server_time", "next_sync_ms", "command", NULL,
    };
    static const char *const COMMAND_FIELDS[] = {
        "revision", "state", "lease_ms", "session_id", "target_total",
        "reason", NULL,
    };
    const char *parse_end = NULL;
    cJSON *root = cJSON_ParseWithOpts(body, &parse_end, true);
    if (!object_has_only(root, ROOT_FIELDS)) {
        cJSON_Delete(root);
        return false;
    }
    for (size_t i = 0; ROOT_FIELDS[i] != NULL; ++i) {
        if (!field_occurs_once(root, ROOT_FIELDS[i])) {
            cJSON_Delete(root);
            return false;
        }
    }

    const cJSON *protocol = cJSON_GetObjectItemCaseSensitive(
        root, "protocol_version"
    );
    const cJSON *server_time = cJSON_GetObjectItemCaseSensitive(root, "server_time");
    const cJSON *sync = cJSON_GetObjectItemCaseSensitive(root, "next_sync_ms");
    const cJSON *command_json = cJSON_GetObjectItemCaseSensitive(root, "command");
    uint64_t protocol_value;
    uint64_t server_time_value;
    uint32_t sync_value;
    bool valid = json_u64(protocol, &protocol_value) && protocol_value == 1 &&
                 json_u64(server_time, &server_time_value) &&
                 server_time_value <= INT64_MAX &&
                 json_u32(sync, &sync_value) && sync_value >= 100 &&
                 sync_value <= CONFIG_ASYL_DEFAULT_POLL_MS &&
                 object_has_only(command_json, COMMAND_FIELDS);
    if (valid) {
        for (size_t i = 0; COMMAND_FIELDS[i] != NULL; ++i) {
            if (!field_occurs_once(command_json, COMMAND_FIELDS[i])) {
                valid = false;
                break;
            }
        }
    }

    const cJSON *revision = cJSON_GetObjectItemCaseSensitive(
        command_json, "revision"
    );
    const cJSON *state = cJSON_GetObjectItemCaseSensitive(command_json, "state");
    const cJSON *lease = cJSON_GetObjectItemCaseSensitive(
        command_json, "lease_ms"
    );
    const cJSON *session = cJSON_GetObjectItemCaseSensitive(
        command_json, "session_id"
    );
    const cJSON *target = cJSON_GetObjectItemCaseSensitive(
        command_json, "target_total"
    );
    const cJSON *reason = cJSON_GetObjectItemCaseSensitive(command_json, "reason");
    uint64_t revision_value;
    uint64_t state_value;
    uint32_t lease_value;
    uint64_t session_value = 0;
    uint32_t target_value = 0;
    valid = valid && json_u64(revision, &revision_value) &&
            json_u64(state, &state_value) && state_value <= 1 &&
            json_u32(lease, &lease_value) && cJSON_IsString(reason) &&
            reason->valuestring != NULL && strlen(reason->valuestring) <= 128;

    if (valid && state_value == 1) {
        valid = revision_value >= 1 && lease_value >= 1 &&
                lease_value <= CONFIG_ASYL_MAX_COMMAND_LEASE_MS &&
                json_u64(session, &session_value) && session_value >= 1 &&
                json_u32(target, &target_value) && target_value >= 1;
    } else if (valid) {
        valid = lease_value == 0 && cJSON_IsNull(session) && cJSON_IsNull(target);
    }

    if (valid) {
        memset(command, 0, sizeof(*command));
        command->revision = revision_value;
        command->state = (int)state_value;
        command->session_id = session_value;
        command->target_total = target_value;
        command->lease_ms = lease_value;
        command->server_time = (int64_t)server_time_value;
        *next_sync_ms = sync_value;
    }
    cJSON_Delete(root);
    return valid;
}

static char *build_request(
    const asyl_conveyor_snapshot_t *snapshot,
    uint64_t acknowledged_revision
) {
    cJSON *root = cJSON_CreateObject();
    if (root == NULL) {
        return NULL;
    }
    cJSON_AddNumberToObject(root, "protocol_version", 1);
    cJSON_AddStringToObject(root, "boot_id", s_boot_id);
    cJSON_AddNumberToObject(root, "seq", (double)s_sequence);
    if (acknowledged_revision == 0) {
        cJSON_AddNullToObject(root, "ack_revision");
    } else {
        cJSON_AddNumberToObject(root, "ack_revision", (double)acknowledged_revision);
    }
    cJSON_AddNumberToObject(root, "output_state", snapshot->output_state);
    cJSON_AddNumberToObject(root, "feedback_state", snapshot->feedback_state);
    if (snapshot->fault_latched) {
        cJSON_AddStringToObject(root, "fault", snapshot->fault);
    } else {
        cJSON_AddNullToObject(root, "fault");
    }
    cJSON_AddNumberToObject(
        root, "uptime_ms", (double)(esp_timer_get_time() / 1000)
    );
    cJSON_AddNumberToObject(root, "wifi_rssi", asyl_wifi_rssi());
    cJSON_AddStringToObject(root, "firmware", CONFIG_ASYL_FIRMWARE_VERSION);
    char *encoded = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    return encoded;
}

static esp_err_t http_event(esp_http_client_event_t *event) {
    response_buffer_t *response = event->user_data;
    if (event->event_id != HTTP_EVENT_ON_DATA || response == NULL ||
        event->data_len <= 0) {
        return ESP_OK;
    }
    if (response->length + (size_t)event->data_len > RESPONSE_CAPACITY) {
        response->overflow = true;
        return ESP_OK;
    }
    memcpy(response->bytes + response->length, event->data, event->data_len);
    response->length += (size_t)event->data_len;
    response->bytes[response->length] = '\0';
    return ESP_OK;
}

static esp_err_t sync_http_client_init(
    const asyl_device_config_t *config,
    sync_http_client_t *http
) {
    memset(http, 0, sizeof(*http));
    char url[API_URL_CAPACITY];
    const int url_length = snprintf(
        url, sizeof(url), "%s%s", config->base_url, CONFIG_ASYL_API_SYNC_PATH
    );
    char authorization[AUTHORIZATION_CAPACITY];
    const int authorization_length = snprintf(
        authorization,
        sizeof(authorization),
        "Device %s.%s",
        config->device_id,
        config->token
    );
    if (url_length <= 0 || (size_t)url_length >= sizeof(url) ||
        authorization_length <= 0 ||
        (size_t)authorization_length >= sizeof(authorization)) {
        memset(authorization, 0, sizeof(authorization));
        return ESP_ERR_INVALID_SIZE;
    }

    const esp_http_client_config_t http_config = {
        .url = url,
        .event_handler = http_event,
        .user_data = &http->response,
        .timeout_ms = CONFIG_ASYL_HTTP_TIMEOUT_MS,
        .crt_bundle_attach = esp_crt_bundle_attach,
        .skip_cert_common_name_check = false,
        .disable_auto_redirect = true,
        .keep_alive_enable = true,
    };
    http->client = esp_http_client_init(&http_config);
    if (http->client == NULL) {
        memset(authorization, 0, sizeof(authorization));
        return ESP_ERR_NO_MEM;
    }

    esp_err_t error = esp_http_client_set_method(
        http->client, HTTP_METHOD_POST
    );
    if (error == ESP_OK) {
        error = esp_http_client_set_header(
            http->client, "Content-Type", "application/json"
        );
    }
    if (error == ESP_OK) {
        error = esp_http_client_set_header(
            http->client, "Accept", "application/json"
        );
    }
    if (error == ESP_OK) {
        error = esp_http_client_set_header(
            http->client, "Cache-Control", "no-store"
        );
    }
    if (error == ESP_OK) {
        error = esp_http_client_set_header(
            http->client, "Authorization", authorization
        );
    }
    /* esp_http_client_set_header copies the value into the client context. */
    memset(authorization, 0, sizeof(authorization));
    if (error != ESP_OK) {
        esp_http_client_cleanup(http->client);
        http->client = NULL;
    }
    return error;
}

static esp_err_t perform_sync(
    sync_http_client_t *http,
    const asyl_guard_state_t *guard,
    asyl_command_t *command,
    uint32_t *next_sync_ms
) {
    memset(&http->response, 0, sizeof(http->response));
    asyl_conveyor_snapshot_t snapshot;
    asyl_conveyor_snapshot(&snapshot);
    char *request = build_request(&snapshot, guard->persisted_revision);
    if (request == NULL) {
        return ESP_ERR_NO_MEM;
    }

    esp_err_t error = esp_http_client_set_post_field(
        http->client, request, strlen(request)
    );
    if (error == ESP_OK) {
        error = esp_http_client_perform(http->client);
    }
    const int status = error == ESP_OK
        ? esp_http_client_get_status_code(http->client)
        : 0;
    if (error == ESP_OK && status != 200) {
        ESP_LOGW(TAG, "sync returned HTTP %d", status);
        error = ESP_FAIL;
    }
    if (error == ESP_OK &&
        !esp_http_client_is_complete_data_received(http->client)) {
        error = ESP_ERR_INVALID_RESPONSE;
    }
    if (error == ESP_OK &&
        (http->response.overflow || http->response.length == 0)) {
        error = ESP_ERR_INVALID_SIZE;
    }
    if (error == ESP_OK &&
        !parse_response(http->response.bytes, command, next_sync_ms)) {
        error = ESP_ERR_INVALID_RESPONSE;
    }
    if (error != ESP_OK) {
        /* A failed or incomplete perform can leave the IDF client parser in
         * the middle of an exchange. Close only the transport so the next
         * request reconnects with the same pinned URL and headers. */
        esp_http_client_close(http->client);
    }
    cJSON_free(request);
    return error;
}

static bool clock_synchronized(void) {
    /* 2025-01-01. TLS certificate validation and ON both remain unavailable
     * until SNTP has supplied a plausible wall clock. */
    return time(NULL) >= 1735689600;
}

static void reject_and_fail_off(
    asyl_guard_state_t *guard,
    asyl_guard_reason_t reason,
    uint64_t rejected_revision
) {
    asyl_conveyor_fail_off(asyl_guard_reason_name(reason));
    asyl_conveyor_snapshot_t snapshot;
    asyl_conveyor_snapshot(&snapshot);
    asyl_guard_block_revision(guard, snapshot.blocked_revision);
    if (rejected_revision > 0) {
        asyl_guard_block_revision(guard, rejected_revision);
        const esp_err_t error = asyl_safety_state_store_blocked(
            rejected_revision
        );
        if (error != ESP_OK) {
            ESP_LOGE(TAG, "cannot persist rejected revision fence: %s",
                     esp_err_to_name(error));
        }
    }
}

static void apply_command(
    asyl_guard_state_t *guard,
    const asyl_command_t *command
) {
    asyl_conveyor_snapshot_t before;
    asyl_conveyor_snapshot(&before);
    asyl_guard_block_revision(guard, before.blocked_revision);
    const asyl_guard_policy_t policy = {
        .max_lease_ms = CONFIG_ASYL_MAX_COMMAND_LEASE_MS,
        .max_clock_skew_seconds = CONFIG_ASYL_SERVER_CLOCK_SKEW_SECONDS,
    };
    const asyl_guard_result_t checked = asyl_guard_evaluate(
        guard, &policy, command, (int64_t)time(NULL), clock_synchronized()
    );
    if (checked.decision == ASYL_GUARD_REJECT_FAIL_OFF) {
        reject_and_fail_off(guard, checked.reason, command->revision);
        ESP_LOGE(TAG, "command rejected after forcing OFF: %s",
                 asyl_guard_reason_name(checked.reason));
        return;
    }

    if (checked.decision == ASYL_GUARD_APPLY_OFF) {
        /* De-energize before any NVS write or log can stall. */
        asyl_conveyor_apply_off(command->revision, "server_off");
        if (checked.persist_revision &&
            asyl_safety_state_store_revision(command->revision) != ESP_OK) {
            reject_and_fail_off(
                guard, ASYL_GUARD_REVISION_CONFLICT, command->revision
            );
            ESP_LOGE(TAG, "OFF revision persistence failed; relay remains OFF");
            return;
        }
        asyl_guard_commit(guard, command, &checked);
        return;
    }

    if (checked.persist_revision &&
        asyl_safety_state_store_revision(command->revision) != ESP_OK) {
        reject_and_fail_off(
            guard, ASYL_GUARD_REVISION_CONFLICT, command->revision
        );
        ESP_LOGE(TAG, "ON revision persistence failed; relay remains OFF");
        return;
    }
    if (asyl_conveyor_apply_on(command->revision, command->lease_ms) != ESP_OK) {
        ESP_LOGE(TAG, "physical ON command rejected");
        asyl_guard_block_revision(guard, command->revision);
        asyl_safety_state_store_blocked(command->revision);
        return;
    }
    asyl_guard_commit(guard, command, &checked);
}

static void api_task(void *argument) {
    (void)argument;
    asyl_device_config_t config;
    while (asyl_device_config_load(&config) != ESP_OK) {
        asyl_conveyor_apply_off(0, "awaiting_provisioning");
        vTaskDelay(pdMS_TO_TICKS(500));
    }

    asyl_guard_state_t guard = {0};
    esp_err_t load_error = asyl_safety_state_load(
        &guard.persisted_revision, &guard.blocked_revision
    );
    if (load_error != ESP_OK) {
        ESP_LOGE(TAG, "cannot load replay state; refusing ON");
        asyl_conveyor_fail_off("replay_state_unavailable");
        vTaskDelete(NULL);
        return;
    }
    sync_http_client_t http;
    const esp_err_t http_error = sync_http_client_init(&config, &http);
    if (http_error != ESP_OK) {
        asyl_conveyor_fail_off("http_client_init_failed");
        ESP_LOGE(TAG, "HTTP client init failed; refusing ON: %s",
                 esp_err_to_name(http_error));
        vTaskDelete(NULL);
        return;
    }
    uint32_t next_sync_ms = CONFIG_ASYL_DEFAULT_POLL_MS;
    while (true) {
        if (!asyl_wifi_wait_ready(1000)) {
            /* This task exclusively owns the handle, so closing here cannot
             * race an in-flight perform. Keep the headers/context for retry. */
            esp_http_client_close(http.client);
            continue;
        }
        if (s_sequence == INT64_MAX) {
            asyl_conveyor_fail_off("request_sequence_exhausted");
            esp_restart();
        }
        ++s_sequence;
        asyl_command_t command;
        uint32_t server_next_sync = CONFIG_ASYL_DEFAULT_POLL_MS;
        const esp_err_t error = perform_sync(
            &http, &guard, &command, &server_next_sync
        );
        if (error == ESP_OK) {
            apply_command(&guard, &command);
            next_sync_ms = server_next_sync;
        } else {
            /* Do not extend the lease. The independent safety task drops the
             * output no later than the last accepted lease (<= 1500 ms). */
            ESP_LOGW(TAG, "sync failed: %s", esp_err_to_name(error));
            next_sync_ms = CONFIG_ASYL_DEFAULT_POLL_MS;
        }
        vTaskDelay(pdMS_TO_TICKS(next_sync_ms));
    }
}

esp_err_t asyl_api_client_start(void) {
    make_boot_id();
    s_sequence = 0;
    if (xTaskCreate(api_task, "asyl_api", 9216, NULL, 8, NULL) != pdPASS) {
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}
