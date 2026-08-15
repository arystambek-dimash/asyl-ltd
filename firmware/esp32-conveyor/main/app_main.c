#include <stdbool.h>
#include <stdint.h>

#include "api_client.h"
#include "conveyor_control.h"
#include "device_config.h"
#include "driver/gpio.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_wifi_default.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs_flash.h"
#include "sdkconfig.h"
#include "wifi_provisioning.h"

static const char *TAG = "asyl_main";

#ifdef CONFIG_ASYL_PROVISION_BUTTON_ACTIVE_LOW
#define PROVISION_BUTTON_ACTIVE_LOW true
#else
#define PROVISION_BUTTON_ACTIVE_LOW false
#endif

static bool provisioning_button_held(void) {
    gpio_config_t button = {
        .pin_bit_mask = 1ULL << CONFIG_ASYL_PROVISION_BUTTON_GPIO,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = PROVISION_BUTTON_ACTIVE_LOW
            ? GPIO_PULLUP_ENABLE
            : GPIO_PULLUP_DISABLE,
        .pull_down_en = PROVISION_BUTTON_ACTIVE_LOW
            ? GPIO_PULLDOWN_DISABLE
            : GPIO_PULLDOWN_ENABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    if (gpio_config(&button) != ESP_OK) {
        return false;
    }
    const int active_level = PROVISION_BUTTON_ACTIVE_LOW ? 0 : 1;
    if (gpio_get_level(CONFIG_ASYL_PROVISION_BUTTON_GPIO) != active_level) {
        return false;
    }
    const uint32_t samples = CONFIG_ASYL_PROVISION_HOLD_MS / 50;
    for (uint32_t i = 0; i < samples; ++i) {
        if (gpio_get_level(CONFIG_ASYL_PROVISION_BUTTON_GPIO) != active_level) {
            return false;
        }
        vTaskDelay(pdMS_TO_TICKS(50));
    }
    return true;
}

void app_main(void) {
    asyl_conveyor_bootstrap_off();
    if (CONFIG_ASYL_RELAY_GPIO == CONFIG_ASYL_FEEDBACK_GPIO ||
        CONFIG_ASYL_RELAY_GPIO == CONFIG_ASYL_PROVISION_BUTTON_GPIO ||
        CONFIG_ASYL_FEEDBACK_GPIO == CONFIG_ASYL_PROVISION_BUTTON_GPIO) {
        ESP_LOGE(TAG, "relay, feedback and provisioning GPIOs must be distinct");
        return;
    }

    /* Never erase NVS automatically: losing the persisted command revision
     * could turn a replayed old ON into a seemingly new command. */
    esp_err_t error = nvs_flash_init();
    if (error != ESP_OK) {
        ESP_LOGE(TAG, "NVS unavailable; controller stays OFF: %s",
                 esp_err_to_name(error));
        return;
    }

    const bool force_provisioning = provisioning_button_held();
    if (force_provisioning) {
        ESP_LOGW(TAG, "physical provisioning reset requested");
        error = asyl_device_config_erase();
        if (error != ESP_OK && error != ESP_ERR_NVS_NOT_FOUND) {
            ESP_LOGE(TAG, "cannot reset device configuration: %s",
                     esp_err_to_name(error));
            return;
        }
    }

    uint64_t last_revision = 0;
    uint64_t blocked_revision = 0;
    error = asyl_safety_state_load(&last_revision, &blocked_revision);
    if (error != ESP_OK) {
        ESP_LOGE(TAG, "cannot load safety state: %s", esp_err_to_name(error));
        return;
    }
    (void)last_revision;
    error = asyl_conveyor_init(blocked_revision);
    if (error != ESP_OK) {
        ESP_LOGE(TAG, "safety controller init failed: %s", esp_err_to_name(error));
        return;
    }

    if ((error = esp_netif_init()) != ESP_OK ||
        (error = esp_event_loop_create_default()) != ESP_OK) {
        asyl_conveyor_fail_off("network_stack_init_failed");
        ESP_LOGE(TAG, "network stack init failed: %s", esp_err_to_name(error));
        return;
    }
    if (esp_netif_create_default_wifi_sta() == NULL) {
        asyl_conveyor_fail_off("network_interface_failed");
        ESP_LOGE(TAG, "cannot create station network interface");
        return;
    }
    error = asyl_wifi_start(force_provisioning);
    if (error != ESP_OK) {
        asyl_conveyor_fail_off("wifi_start_failed");
        ESP_LOGE(TAG, "Wi-Fi/provisioning start failed: %s", esp_err_to_name(error));
        return;
    }
    error = asyl_api_client_start();
    if (error != ESP_OK) {
        asyl_conveyor_fail_off("api_task_failed");
        ESP_LOGE(TAG, "API task start failed: %s", esp_err_to_name(error));
        return;
    }
    ESP_LOGI(TAG, "controller initialized in fail-OFF mode");
}
