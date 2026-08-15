#include "conveyor_control.h"

#include <string.h>

#include "command_guard.h"
#include "device_config.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "esp_task_wdt.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "sdkconfig.h"

static const char *TAG = "asyl_safety";
static SemaphoreHandle_t s_lock;
static bool s_output_on;
static bool s_fault_latched;
static char s_fault[ASYL_FAULT_MAX + 1] = "boot_off";
static uint64_t s_active_revision;
static uint64_t s_blocked_revision;
static int64_t s_lease_deadline_us;
static int64_t s_feedback_transition_started_us;

#ifdef CONFIG_ASYL_RELAY_ACTIVE_HIGH
#define RELAY_ACTIVE_HIGH true
#else
#define RELAY_ACTIVE_HIGH false
#endif

#ifdef CONFIG_ASYL_FEEDBACK_ACTIVE_HIGH
#define FEEDBACK_ACTIVE_HIGH true
#else
#define FEEDBACK_ACTIVE_HIGH false
#endif

static int output_level(bool on) {
    const bool high = RELAY_ACTIVE_HIGH ? on : !on;
    return high ? 1 : 0;
}

void asyl_conveyor_bootstrap_off(void) {
    /* This is intentionally the first application action after reset. */
    gpio_set_level(CONFIG_ASYL_RELAY_GPIO, output_level(false));
    gpio_set_direction(CONFIG_ASYL_RELAY_GPIO, GPIO_MODE_OUTPUT);
    gpio_set_level(CONFIG_ASYL_RELAY_GPIO, output_level(false));
}

static bool read_feedback(void) {
    const bool high = gpio_get_level(CONFIG_ASYL_FEEDBACK_GPIO) != 0;
    return FEEDBACK_ACTIVE_HIGH ? high : !high;
}

static void copy_fault(const char *reason) {
    if (reason == NULL || reason[0] == '\0') {
        reason = "unknown_fault";
    }
    strncpy(s_fault, reason, sizeof(s_fault) - 1);
    s_fault[sizeof(s_fault) - 1] = '\0';
}

static uint64_t fail_off_locked(const char *reason) {
    gpio_set_level(CONFIG_ASYL_RELAY_GPIO, output_level(false));
    const uint64_t blocked = s_active_revision;
    s_output_on = false;
    s_lease_deadline_us = 0;
    s_feedback_transition_started_us = esp_timer_get_time();
    s_fault_latched = true;
    copy_fault(reason);
    if (blocked > s_blocked_revision) {
        s_blocked_revision = blocked;
    }
    return blocked;
}

static void persist_blocked(uint64_t revision) {
    if (revision == 0) {
        return;
    }
    const esp_err_t error = asyl_safety_state_store_blocked(revision);
    if (error != ESP_OK) {
        ESP_LOGE(TAG, "cannot persist blocked revision: %s", esp_err_to_name(error));
    }
}

static void safety_task(void *argument) {
    (void)argument;
    esp_err_t task_wdt_error = esp_task_wdt_add(NULL);
    if (task_wdt_error != ESP_OK && task_wdt_error != ESP_ERR_INVALID_STATE) {
        ESP_LOGW(TAG, "task watchdog registration failed: %s",
                 esp_err_to_name(task_wdt_error));
    }

    while (true) {
        uint64_t block_revision = 0;
        const int64_t now = esp_timer_get_time();
        xSemaphoreTake(s_lock, portMAX_DELAY);
        const bool feedback = read_feedback();

        if (s_output_on &&
            (s_lease_deadline_us == 0 || now >= s_lease_deadline_us)) {
            ESP_LOGE(TAG, "lease expired; forcing relay OFF");
            block_revision = fail_off_locked("lease_expired");
        } else if (
            !s_output_on && feedback && s_fault_latched &&
            strcmp(s_fault, "feedback_stuck_on") == 0
        ) {
            /* Keep reporting the welded/stuck contact, but do not rewrite NVS
             * or flood logs on every safety-task iteration. */
        } else if (feedback != s_output_on) {
            if (s_feedback_transition_started_us == 0) {
                s_feedback_transition_started_us = now;
            } else if (
                now - s_feedback_transition_started_us >=
                (int64_t)CONFIG_ASYL_FEEDBACK_SETTLE_MS * 1000
            ) {
                const char *reason = s_output_on
                    ? "feedback_failed_on"
                    : "feedback_stuck_on";
                ESP_LOGE(TAG, "%s; forcing relay OFF", reason);
                block_revision = fail_off_locked(reason);
            }
        } else {
            s_feedback_transition_started_us = 0;
        }
        xSemaphoreGive(s_lock);

        persist_blocked(block_revision);
        if (task_wdt_error == ESP_OK) {
            esp_task_wdt_reset();
        }
        vTaskDelay(pdMS_TO_TICKS(20));
    }
}

esp_err_t asyl_conveyor_init(uint64_t persisted_blocked_revision) {
    /* Set the inactive level before enabling the output driver. A hardware
     * pull-down/pull-up is still required to keep the contactor OFF at reset. */
    asyl_conveyor_bootstrap_off();
    gpio_config_t output = {
        .pin_bit_mask = 1ULL << CONFIG_ASYL_RELAY_GPIO,
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    esp_err_t error = gpio_config(&output);
    if (error != ESP_OK) {
        return error;
    }
    gpio_set_level(CONFIG_ASYL_RELAY_GPIO, output_level(false));

    gpio_config_t feedback = {
        .pin_bit_mask = 1ULL << CONFIG_ASYL_FEEDBACK_GPIO,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = FEEDBACK_ACTIVE_HIGH
            ? GPIO_PULLUP_DISABLE
            : GPIO_PULLUP_ENABLE,
        .pull_down_en = FEEDBACK_ACTIVE_HIGH
            ? GPIO_PULLDOWN_ENABLE
            : GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    error = gpio_config(&feedback);
    if (error != ESP_OK) {
        return error;
    }
    s_lock = xSemaphoreCreateMutex();
    if (s_lock == NULL) {
        return ESP_ERR_NO_MEM;
    }
    s_output_on = false;
    s_fault_latched = false;
    s_active_revision = 0;
    s_blocked_revision = persisted_blocked_revision;
    s_lease_deadline_us = 0;
    s_feedback_transition_started_us = esp_timer_get_time();
    copy_fault("boot_off");

    if (xTaskCreate(
            safety_task, "asyl_safety", 4096, NULL,
            configMAX_PRIORITIES - 2, NULL
        ) != pdPASS) {
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

esp_err_t asyl_conveyor_apply_on(uint64_t revision, uint32_t lease_ms) {
    if (revision == 0 || lease_ms == 0 ||
        lease_ms > CONFIG_ASYL_MAX_COMMAND_LEASE_MS ||
        lease_ms > CONFIG_ASYL_LOCAL_FAIL_OFF_MS) {
        asyl_conveyor_fail_off("invalid_lease");
        return ESP_ERR_INVALID_ARG;
    }
    esp_err_t error = ESP_OK;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    const bool renewal = s_output_on && s_active_revision == revision;
    if (revision <= s_blocked_revision ||
        (s_fault_latched && revision <= s_active_revision) ||
        (!renewal && read_feedback())) {
        error = ESP_ERR_INVALID_STATE;
    } else {
        if (!renewal) {
            s_fault_latched = false;
            copy_fault("none");
            s_active_revision = revision;
            s_feedback_transition_started_us = esp_timer_get_time();
            gpio_set_level(CONFIG_ASYL_RELAY_GPIO, output_level(true));
            s_output_on = true;
        }
        s_lease_deadline_us = esp_timer_get_time() + (int64_t)lease_ms * 1000;
    }
    xSemaphoreGive(s_lock);
    if (error != ESP_OK) {
        asyl_conveyor_fail_off("on_rejected");
    }
    return error;
}

void asyl_conveyor_apply_off(uint64_t revision, const char *reason) {
    xSemaphoreTake(s_lock, portMAX_DELAY);
    gpio_set_level(CONFIG_ASYL_RELAY_GPIO, output_level(false));
    s_output_on = false;
    s_lease_deadline_us = 0;
    s_feedback_transition_started_us = esp_timer_get_time();
    const bool clear_fault = s_fault_latched && asyl_guard_can_clear_fault(
        s_blocked_revision, revision, !read_feedback()
    );
    if (revision > s_active_revision) {
        s_active_revision = revision;
    }
    if (clear_fault) {
        s_fault_latched = false;
        copy_fault(reason == NULL ? "server_off" : reason);
        ESP_LOGI(TAG, "newer server OFF acknowledged and cleared local fault");
    } else if (!s_fault_latched) {
        copy_fault(reason == NULL ? "server_off" : reason);
    }
    xSemaphoreGive(s_lock);
}

void asyl_conveyor_fail_off(const char *reason) {
    xSemaphoreTake(s_lock, portMAX_DELAY);
    const uint64_t blocked = fail_off_locked(reason);
    xSemaphoreGive(s_lock);
    persist_blocked(blocked);
}

void asyl_conveyor_snapshot(asyl_conveyor_snapshot_t *snapshot) {
    if (snapshot == NULL) {
        return;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    snapshot->output_state = s_output_on ? 1 : 0;
    snapshot->feedback_state = read_feedback() ? 1 : 0;
    snapshot->fault_latched = s_fault_latched;
    strncpy(snapshot->fault, s_fault, sizeof(snapshot->fault) - 1);
    snapshot->fault[sizeof(snapshot->fault) - 1] = '\0';
    snapshot->active_revision = s_active_revision;
    snapshot->blocked_revision = s_blocked_revision;
    if (s_output_on && s_lease_deadline_us > 0) {
        snapshot->lease_remaining_ms =
            (s_lease_deadline_us - esp_timer_get_time()) / 1000;
        if (snapshot->lease_remaining_ms < 0) {
            snapshot->lease_remaining_ms = 0;
        }
    } else {
        snapshot->lease_remaining_ms = 0;
    }
    xSemaphoreGive(s_lock);
}
