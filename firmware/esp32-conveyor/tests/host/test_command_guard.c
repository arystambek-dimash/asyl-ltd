#include "command_guard.h"

#include <assert.h>
#include <stdio.h>

static const asyl_guard_policy_t POLICY = {
    .max_lease_ms = 1500,
    .max_clock_skew_seconds = 10,
};

static asyl_command_t command(uint64_t revision, int state) {
    asyl_command_t value = {
        .revision = revision,
        .state = state,
        .target_total = state ? 100 : 0,
        .lease_ms = state ? 1500 : 0,
        .server_time = 200000,
    };
    value.session_id = state ? 42 : 0;
    return value;
}

static void test_new_on_and_same_revision_renewal(void) {
    asyl_guard_state_t state = {.off_seen_this_boot = true};
    asyl_command_t value = command(7, 1);
    asyl_guard_result_t first = asyl_guard_evaluate(
        &state, &POLICY, &value, 200001, true
    );
    assert(first.decision == ASYL_GUARD_APPLY_ON);
    assert(first.persist_revision);
    asyl_guard_commit(&state, &value, &first);

    asyl_guard_result_t renewed = asyl_guard_evaluate(
        &state, &POLICY, &value, 200002, true
    );
    assert(renewed.decision == ASYL_GUARD_RENEW_ON);
    assert(!renewed.persist_revision);
}

static void test_reboot_never_resumes_same_on_revision(void) {
    asyl_guard_state_t rebooted = {.persisted_revision = 7};
    asyl_command_t value = command(7, 1);
    asyl_guard_result_t checked = asyl_guard_evaluate(
        &rebooted, &POLICY, &value, 200001, true
    );
    assert(checked.decision == ASYL_GUARD_REJECT_FAIL_OFF);
    assert(checked.reason == ASYL_GUARD_BOOT_OFF_HANDSHAKE_REQUIRED);

    value.revision = 8;
    checked = asyl_guard_evaluate(
        &rebooted, &POLICY, &value, 200001, true
    );
    assert(checked.reason == ASYL_GUARD_BOOT_OFF_HANDSHAKE_REQUIRED);

    asyl_command_t off = command(7, 0);
    checked = asyl_guard_evaluate(&rebooted, &POLICY, &off, 0, false);
    assert(checked.decision == ASYL_GUARD_APPLY_OFF);
    asyl_guard_commit(&rebooted, &off, &checked);
    value.revision = 7;
    checked = asyl_guard_evaluate(
        &rebooted, &POLICY, &value, 200001, true
    );
    assert(checked.reason == ASYL_GUARD_REVISION_CONFLICT);
    value.revision = 8;
    checked = asyl_guard_evaluate(
        &rebooted, &POLICY, &value, 200001, true
    );
    assert(checked.decision == ASYL_GUARD_APPLY_ON);
}

static void test_stale_expired_and_oversized_commands_fail_off(void) {
    asyl_guard_state_t state = {
        .persisted_revision = 10,
        .off_seen_this_boot = true,
    };
    asyl_command_t value = command(9, 1);
    assert(asyl_guard_evaluate(&state, &POLICY, &value, 200000, true).reason ==
           ASYL_GUARD_STALE_REVISION);

    value.revision = 11;
    value.server_time = 199980;
    assert(asyl_guard_evaluate(&state, &POLICY, &value, 200000, true).reason ==
           ASYL_GUARD_STALE_SERVER_TIME);

    value.server_time = 200000;
    value.lease_ms = 1501;
    assert(asyl_guard_evaluate(&state, &POLICY, &value, 200000, true).reason ==
           ASYL_GUARD_INVALID_LEASE);
}

static void test_fault_latch_needs_new_revision(void) {
    asyl_guard_state_t state = {
        .persisted_revision = 4,
        .blocked_revision = 4,
        .off_seen_this_boot = true,
        .runtime_valid = true,
        .runtime_revision = 4,
        .runtime_state = 1,
        .runtime_session_id = 42,
    };
    asyl_command_t value = command(4, 1);
    assert(asyl_guard_evaluate(&state, &POLICY, &value, 200000, true).reason ==
           ASYL_GUARD_FAULT_LATCHED);
    value.revision = 5;
    assert(asyl_guard_evaluate(&state, &POLICY, &value, 200000, true).decision ==
           ASYL_GUARD_APPLY_ON);
}

static void test_off_is_immediate_and_safe_without_clock(void) {
    asyl_guard_state_t state = {.persisted_revision = 12};
    asyl_command_t value = command(2, 0);
    asyl_guard_result_t checked = asyl_guard_evaluate(
        &state, &POLICY, &value, 0, false
    );
    assert(checked.decision == ASYL_GUARD_APPLY_OFF);
    assert(!checked.persist_revision);
    asyl_guard_commit(&state, &value, &checked);
    assert(state.persisted_revision == 12);
    assert(!state.off_seen_this_boot);

    value.revision = 12;
    checked = asyl_guard_evaluate(&state, &POLICY, &value, 0, false);
    assert(checked.decision == ASYL_GUARD_APPLY_OFF);
    asyl_guard_commit(&state, &value, &checked);
    assert(state.off_seen_this_boot);

    value.lease_ms = 1;
    checked = asyl_guard_evaluate(&state, &POLICY, &value, 0, false);
    assert(checked.decision == ASYL_GUARD_REJECT_FAIL_OFF);
}

static void test_same_revision_cannot_change_session(void) {
    asyl_guard_state_t state = {
        .persisted_revision = 20,
        .off_seen_this_boot = true,
        .runtime_valid = true,
        .runtime_revision = 20,
        .runtime_state = 1,
        .runtime_session_id = 99,
    };
    asyl_command_t value = command(20, 1);
    assert(asyl_guard_evaluate(&state, &POLICY, &value, 200000, true).reason ==
           ASYL_GUARD_REVISION_CONFLICT);
}

static void test_fault_clear_requires_newer_off_and_physical_off(void) {
    assert(!asyl_guard_can_clear_fault(40, 40, true));
    assert(!asyl_guard_can_clear_fault(40, 41, false));
    assert(asyl_guard_can_clear_fault(40, 41, true));
}

static void test_stale_off_cannot_be_followed_by_old_on_renewal(void) {
    asyl_guard_state_t state = {
        .persisted_revision = 30,
        .off_seen_this_boot = true,
        .runtime_valid = true,
        .runtime_revision = 30,
        .runtime_state = 1,
        .runtime_session_id = 42,
    };
    asyl_command_t stale_off = command(29, 0);
    asyl_guard_result_t checked = asyl_guard_evaluate(
        &state, &POLICY, &stale_off, 0, false
    );
    assert(checked.decision == ASYL_GUARD_APPLY_OFF);
    asyl_guard_commit(&state, &stale_off, &checked);
    assert(!state.runtime_valid);

    asyl_command_t old_on = command(30, 1);
    checked = asyl_guard_evaluate(&state, &POLICY, &old_on, 200000, true);
    assert(checked.decision == ASYL_GUARD_REJECT_FAIL_OFF);
    assert(checked.reason == ASYL_GUARD_REBOOT_RESUME_BLOCKED);
}

int main(void) {
    test_new_on_and_same_revision_renewal();
    test_reboot_never_resumes_same_on_revision();
    test_stale_expired_and_oversized_commands_fail_off();
    test_fault_latch_needs_new_revision();
    test_off_is_immediate_and_safe_without_clock();
    test_same_revision_cannot_change_session();
    test_fault_clear_requires_newer_off_and_physical_off();
    test_stale_off_cannot_be_followed_by_old_on_renewal();
    puts("command_guard: all tests passed");
    return 0;
}
