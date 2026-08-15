#include "command_guard.h"

#include <stddef.h>

static asyl_guard_result_t result(
    asyl_guard_decision_t decision,
    asyl_guard_reason_t reason,
    bool persist_revision
) {
    asyl_guard_result_t value = {
        .decision = decision,
        .reason = reason,
        .persist_revision = persist_revision,
    };
    return value;
}

asyl_guard_result_t asyl_guard_evaluate(
    const asyl_guard_state_t *state,
    const asyl_guard_policy_t *policy,
    const asyl_command_t *command,
    int64_t local_time,
    bool clock_synchronized
) {
    if (state == NULL || policy == NULL || command == NULL ||
        (command->state != 0 && command->state != 1)) {
        return result(
            ASYL_GUARD_REJECT_FAIL_OFF, ASYL_GUARD_INVALID_STATE, false
        );
    }

    /* A valid OFF is always applied immediately. It cannot energize anything,
     * even if the clock is not synchronized or its revision is stale. */
    if (command->state == 0) {
        if (command->lease_ms != 0) {
            return result(
                ASYL_GUARD_REJECT_FAIL_OFF, ASYL_GUARD_INVALID_LEASE, false
            );
        }
        return result(
            ASYL_GUARD_APPLY_OFF,
            ASYL_GUARD_OK,
            command->revision > state->persisted_revision
        );
    }

    if (command->revision == 0 || command->session_id == 0 ||
        command->target_total == 0) {
        return result(
            ASYL_GUARD_REJECT_FAIL_OFF, ASYL_GUARD_INVALID_SESSION, false
        );
    }
    if (command->lease_ms == 0 || command->lease_ms > policy->max_lease_ms) {
        return result(
            ASYL_GUARD_REJECT_FAIL_OFF, ASYL_GUARD_INVALID_LEASE, false
        );
    }
    if (!clock_synchronized) {
        return result(
            ASYL_GUARD_REJECT_FAIL_OFF, ASYL_GUARD_CLOCK_UNSYNCED, false
        );
    }
    if (!state->off_seen_this_boot) {
        return result(
            ASYL_GUARD_REJECT_FAIL_OFF,
            ASYL_GUARD_BOOT_OFF_HANDSHAKE_REQUIRED,
            false
        );
    }
    const int64_t delta = command->server_time - local_time;
    const int64_t absolute_delta = delta < 0 ? -delta : delta;
    if (absolute_delta > (int64_t)policy->max_clock_skew_seconds) {
        return result(
            ASYL_GUARD_REJECT_FAIL_OFF, ASYL_GUARD_STALE_SERVER_TIME, false
        );
    }
    if (command->revision < state->persisted_revision) {
        return result(
            ASYL_GUARD_REJECT_FAIL_OFF, ASYL_GUARD_STALE_REVISION, false
        );
    }
    if (command->revision <= state->blocked_revision) {
        return result(
            ASYL_GUARD_REJECT_FAIL_OFF, ASYL_GUARD_FAULT_LATCHED, false
        );
    }

    if (command->revision == state->persisted_revision) {
        /* Persisted revision without matching RAM state means the ESP rebooted.
         * The server must issue a new revision; power recovery never resumes ON. */
        if (!state->runtime_valid ||
            state->runtime_revision != command->revision) {
            return result(
                ASYL_GUARD_REJECT_FAIL_OFF,
                ASYL_GUARD_REBOOT_RESUME_BLOCKED,
                false
            );
        }
        if (state->runtime_state != 1 ||
            state->runtime_session_id != command->session_id) {
            return result(
                ASYL_GUARD_REJECT_FAIL_OFF,
                ASYL_GUARD_REVISION_CONFLICT,
                false
            );
        }
        return result(ASYL_GUARD_RENEW_ON, ASYL_GUARD_OK, false);
    }

    return result(ASYL_GUARD_APPLY_ON, ASYL_GUARD_OK, true);
}

void asyl_guard_commit(
    asyl_guard_state_t *state,
    const asyl_command_t *command,
    const asyl_guard_result_t *result_value
) {
    if (state == NULL || command == NULL || result_value == NULL ||
        result_value->decision == ASYL_GUARD_REJECT_FAIL_OFF) {
        return;
    }
    if (result_value->persist_revision &&
        command->revision > state->persisted_revision) {
        state->persisted_revision = command->revision;
    }
    if (
        command->state == 0 &&
        command->revision >= state->persisted_revision
    ) {
        /* A stale OFF is always safe to apply physically, but it must not be
         * usable as the post-boot handshake before a replayed newer ON. */
        state->off_seen_this_boot = true;
    }
    if (command->revision < state->persisted_revision) {
        /* A stale OFF is physically honored, but never rolls runtime replay
         * state backwards.  Invalidate the in-RAM ON binding so the previously
         * active revision cannot silently re-energize on its next renewal. */
        if (command->state == 0) {
            state->runtime_valid = false;
        }
        return;
    }
    state->runtime_valid = true;
    state->runtime_revision = command->revision;
    state->runtime_state = command->state;
    state->runtime_session_id = command->session_id;
}

void asyl_guard_block_revision(asyl_guard_state_t *state, uint64_t revision) {
    if (state != NULL && revision > state->blocked_revision) {
        state->blocked_revision = revision;
    }
}

bool asyl_guard_can_clear_fault(
    uint64_t blocked_revision,
    uint64_t off_revision,
    bool feedback_is_off
) {
    /* A fault is cleared only after the server has observed it and answered
     * with a newer OFF fence, while the independent contact confirms OFF.
     * The blocked revision remains durable, so the failed ON can never return. */
    return feedback_is_off && off_revision > blocked_revision;
}

const char *asyl_guard_reason_name(asyl_guard_reason_t reason) {
    switch (reason) {
        case ASYL_GUARD_OK:
            return "ok";
        case ASYL_GUARD_INVALID_STATE:
            return "invalid_state";
        case ASYL_GUARD_INVALID_LEASE:
            return "invalid_lease";
        case ASYL_GUARD_INVALID_SESSION:
            return "invalid_session";
        case ASYL_GUARD_CLOCK_UNSYNCED:
            return "clock_unsynced";
        case ASYL_GUARD_BOOT_OFF_HANDSHAKE_REQUIRED:
            return "boot_off_handshake_required";
        case ASYL_GUARD_STALE_SERVER_TIME:
            return "stale_server_time";
        case ASYL_GUARD_STALE_REVISION:
            return "stale_revision";
        case ASYL_GUARD_REBOOT_RESUME_BLOCKED:
            return "reboot_resume_blocked";
        case ASYL_GUARD_REVISION_CONFLICT:
            return "revision_conflict";
        case ASYL_GUARD_FAULT_LATCHED:
            return "fault_latched";
        default:
            return "unknown";
    }
}
