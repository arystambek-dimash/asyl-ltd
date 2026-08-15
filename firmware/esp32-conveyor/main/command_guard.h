#pragma once

#include <stdbool.h>
#include <stdint.h>

typedef struct {
    uint64_t revision;
    int state;
    uint64_t session_id;
    uint32_t target_total;
    uint32_t lease_ms;
    int64_t server_time;
} asyl_command_t;

typedef struct {
    uint32_t max_lease_ms;
    uint32_t max_clock_skew_seconds;
} asyl_guard_policy_t;

typedef struct {
    uint64_t persisted_revision;
    uint64_t blocked_revision;
    bool off_seen_this_boot;
    bool runtime_valid;
    uint64_t runtime_revision;
    int runtime_state;
    uint64_t runtime_session_id;
    uint32_t runtime_target_total;
} asyl_guard_state_t;

typedef enum {
    ASYL_GUARD_APPLY_OFF = 0,
    ASYL_GUARD_APPLY_ON,
    ASYL_GUARD_RENEW_ON,
    ASYL_GUARD_REJECT_FAIL_OFF,
} asyl_guard_decision_t;

typedef enum {
    ASYL_GUARD_OK = 0,
    ASYL_GUARD_INVALID_STATE,
    ASYL_GUARD_INVALID_LEASE,
    ASYL_GUARD_INVALID_SESSION,
    ASYL_GUARD_CLOCK_UNSYNCED,
    ASYL_GUARD_BOOT_OFF_HANDSHAKE_REQUIRED,
    ASYL_GUARD_STALE_SERVER_TIME,
    ASYL_GUARD_STALE_REVISION,
    ASYL_GUARD_REBOOT_RESUME_BLOCKED,
    ASYL_GUARD_REVISION_CONFLICT,
    ASYL_GUARD_FAULT_LATCHED,
} asyl_guard_reason_t;

typedef struct {
    asyl_guard_decision_t decision;
    asyl_guard_reason_t reason;
    bool persist_revision;
} asyl_guard_result_t;

asyl_guard_result_t asyl_guard_evaluate(
    const asyl_guard_state_t *state,
    const asyl_guard_policy_t *policy,
    const asyl_command_t *command,
    int64_t local_time,
    bool clock_synchronized
);

void asyl_guard_commit(
    asyl_guard_state_t *state,
    const asyl_command_t *command,
    const asyl_guard_result_t *result
);

void asyl_guard_block_revision(asyl_guard_state_t *state, uint64_t revision);
bool asyl_guard_can_clear_fault(
    uint64_t blocked_revision,
    uint64_t off_revision,
    bool feedback_is_off
);
bool asyl_guard_can_energize(
    bool fault_latched,
    uint64_t blocked_revision,
    uint64_t revision,
    bool renewal,
    bool feedback_is_on
);
const char *asyl_guard_reason_name(asyl_guard_reason_t reason);
