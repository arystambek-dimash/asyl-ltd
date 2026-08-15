#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

#define ASYL_FAULT_MAX 47

typedef struct {
    int output_state;
    int feedback_state;
    bool fault_latched;
    char fault[ASYL_FAULT_MAX + 1];
    uint64_t active_revision;
    uint64_t blocked_revision;
    int64_t lease_remaining_ms;
} asyl_conveyor_snapshot_t;

void asyl_conveyor_bootstrap_off(void);
esp_err_t asyl_conveyor_init(uint64_t persisted_blocked_revision);
esp_err_t asyl_conveyor_apply_on(uint64_t revision, uint32_t lease_ms);
void asyl_conveyor_apply_off(uint64_t revision, const char *reason);
void asyl_conveyor_fail_off(const char *reason);
void asyl_conveyor_snapshot(asyl_conveyor_snapshot_t *snapshot);
