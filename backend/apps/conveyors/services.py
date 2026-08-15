from __future__ import annotations

import hmac
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.cameras.models import AiCountingSession, MonoblockCameraSettings

from .credentials import digest_token, generate_token
from .models import MAX_SEQUENCE, ConveyorDevice

ADMIN_PENDING_REASONS = frozenset({
    "device_disable_pending",
    "credential_rotation_pending",
})
BENCH_PULSE_REASON = "bench_pulse"
# Keep both the server renewal window and every local ESP lease short. Even if
# an ON response spends the full firmware HTTP timeout (900 ms) in flight, the
# 500 ms device lease still expires comfortably before two seconds.
BENCH_PULSE_WINDOW_MS = 500
BENCH_PULSE_LEASE_MS = 500


class ConveyorDeviceError(Exception):
    def __init__(self, code: str, detail: str, *, status: int = 409):
        self.code = code
        self.detail = detail
        self.status = status
        super().__init__(detail)


@dataclass(frozen=True)
class SyncResult:
    payload: dict
    audit: dict | None = None


@dataclass(frozen=True)
class ObservationResult:
    payload: dict
    audit: dict | None = None


def lock_camera_binding() -> None:
    """Serialize controller binding changes with creation of AI sessions."""
    row, _ = MonoblockCameraSettings.objects.get_or_create(singleton=True)
    MonoblockCameraSettings.objects.select_for_update().get(pk=row.pk)


def _camera_has_open_session(camera: str) -> bool:
    return AiCountingSession.objects.filter(
        camera=camera,
        status__in=AiCountingSession.OPEN_STATUSES,
    ).exists()


def transport_for(camera: str) -> str:
    """Choose the controller from the durable camera binding.

    An active ESP32 row is the single source of truth: new order sessions on
    that camera use the server-leased device.  Without one, the existing
    camera-PC/direct path remains unchanged.  The chosen value is still frozen
    into the session, so binding or disabling a device never changes an order
    that is already open.
    """
    if cloud_device_for(camera) is not None:
        return AiCountingSession.CONVEYOR_CLOUD
    return AiCountingSession.CONVEYOR_DIRECT


def cloud_device_for(camera: str, *, lock: bool = False) -> ConveyorDevice | None:
    queryset = ConveyorDevice.objects.filter(camera_source=camera, is_active=True)
    if lock:
        queryset = queryset.select_for_update()
    return queryset.first()


def _next_revision(device: ConveyorDevice) -> bool:
    if device.command_revision >= MAX_SEQUENCE:
        # A revision must never wrap or silently reuse an ON identifier. Stop
        # issuing leases and revoke sync authentication; the outstanding short
        # lease expires locally even if this final equal-revision OFF is lost.
        device.is_active = False
        device.desired_state = False
        device.command_terminal = True
        device.stop_reason = "revision_exhausted"
        device.run_started_at = None
        return False
    device.command_revision += 1
    return True


def _latch_off_locked(device: ConveyorDevice, reason: str) -> bool:
    # An administrator's disable/rotation intent must survive transitional
    # telemetry such as the controller reporting its previous ON output. It
    # remains latched until the dedicated operation observes fresh OFF and
    # completes; automatic faults may stop but never cancel that intent.
    if device.stop_reason in ADMIN_PENDING_REASONS:
        reason = device.stop_reason
    transition = bool(
        device.desired_state
        or not device.command_terminal
        or device.stop_reason != reason
    )
    if transition and not _next_revision(device):
        return True
    device.desired_state = False
    device.command_terminal = True
    device.stop_reason = reason[:64]
    device.run_started_at = None
    return transition


def _command_payload(device: ConveyorDevice, *, reason: str | None = None) -> dict:
    enabled = bool(device.desired_state and not device.command_terminal)
    bench_pulse = enabled and device.stop_reason == BENCH_PULSE_REASON
    legacy_bridge = bool(
        enabled
        and device.command_session is not None
        and device.command_session.conveyor_observation_mode
        == AiCountingSession.OBSERVATION_LEGACY_BRIDGE
    )
    if bench_pulse:
        lease_ms = BENCH_PULSE_LEASE_MS
        next_sync_ms = int(getattr(settings, "CONVEYOR_DEVICE_SYNC_MS", 500))
    elif legacy_bridge:
        # The compatibility bridge has no callback running beside the camera.
        # Its tighter pair keeps the last possible positive observation plus
        # the local ESP lease within the configured 1.5 second OFF envelope.
        lease_ms = int(
            getattr(settings, "CONVEYOR_LEGACY_BRIDGE_DEVICE_LEASE_MS", 750)
        )
        next_sync_ms = int(
            getattr(settings, "CONVEYOR_LEGACY_BRIDGE_DEVICE_SYNC_MS", 250)
        )
    else:
        lease_ms = int(getattr(settings, "CONVEYOR_DEVICE_LEASE_MS", 1200))
        lease_ms = min(1500, max(1, lease_ms)) if enabled else 0
        next_sync_ms = int(getattr(settings, "CONVEYOR_DEVICE_SYNC_MS", 500))
    return {
        "protocol_version": 1,
        "server_time": int(time.time()),
        "next_sync_ms": next_sync_ms,
        "command": {
            "revision": device.command_revision,
            "state": int(enabled),
            "lease_ms": lease_ms,
            # The current ESP guard requires positive binding fields for every
            # ON lease. A bench pulse has no AI session, so use stable opaque
            # sentinels which are never interpreted as an actual DB binding.
            "session_id": (
                device.pk
                if bench_pulse
                else device.command_session_id
                if enabled
                else None
            ),
            "target_total": (
                1
                if bench_pulse
                else device.command_target_total
                if enabled
                else None
            ),
            "reason": reason or device.stop_reason or (
                "active_session" if enabled else "off"
            ),
        },
    }


def _audit_payload(device: ConveyorDevice) -> dict:
    return {
        "device_id": str(device.public_id),
        "camera": device.camera_source,
        "revision": device.command_revision,
        "session_id": device.command_session_id,
        "target_total": device.command_target_total,
        "total": device.last_total,
        "reason": device.stop_reason,
    }


def _ai_stale_ms(session: AiCountingSession) -> int:
    if (
        session.conveyor_observation_mode
        == AiCountingSession.OBSERVATION_LEGACY_BRIDGE
    ):
        return int(getattr(settings, "CONVEYOR_LEGACY_BRIDGE_STALE_MS", 750))
    return int(getattr(settings, "CONVEYOR_AI_STALE_MS", 1500))


def _session_can_run(device: ConveyorDevice, now) -> tuple[bool, str]:
    if device.stop_reason == BENCH_PULSE_REASON:
        if "bench" not in (device.firmware or "").casefold():
            return False, "bench_firmware_lost"
        if _camera_has_open_session(device.camera_source):
            return False, "session_started_during_bench"
        if device.run_started_at is None:
            return False, "bench_deadline_missing"
        deadline = device.run_started_at + timedelta(
            milliseconds=BENCH_PULSE_WINDOW_MS
        )
        if now >= deadline:
            return False, "bench_timeout"
        return True, BENCH_PULSE_REASON

    session = device.command_session
    if (
        session is None
        or session.conveyor_transport != AiCountingSession.CONVEYOR_CLOUD
    ):
        return False, "transport_not_cloud"
    if (
        session.status != AiCountingSession.ACTIVE
        or session.camera != device.camera_source
        or session.target_total != device.command_target_total
        or session.target_total <= 0
    ):
        return False, "session_mismatch"
    order = session.order
    if order.status != "loading" or order.loading_camera != device.camera_source:
        return False, "order_not_loading"
    if device.last_boot_id != device.armed_device_boot_id:
        return False, "device_boot_changed"
    if device.last_ai_boot_id != device.armed_edge_boot_id:
        return False, "edge_boot_changed"
    if (
        session.conveyor_observation_mode
        == AiCountingSession.OBSERVATION_LEGACY_BRIDGE
        and (
            session.legacy_bridge_boot_id is None
            or device.last_ai_boot_id != session.legacy_bridge_boot_id
        )
    ):
        return False, "legacy_bridge_restarted"
    stale_ms = _ai_stale_ms(session)
    if (
        device.last_ai_seen_at is None
        or now - device.last_ai_seen_at >= timedelta(milliseconds=stale_ms)
    ):
        return False, "stale_ai"
    if device.last_total >= session.target_total:
        return False, "target_reached"
    if device.run_started_at is None:
        return False, "run_not_started"
    max_run = int(getattr(settings, "CONVEYOR_MAX_RUN_SECONDS", 300))
    if now - device.run_started_at >= timedelta(seconds=max_run):
        return False, "max_runtime"
    progress_at = device.last_progress_at or device.run_started_at
    no_progress = int(getattr(settings, "CONVEYOR_NO_PROGRESS_SECONDS", 15))
    if now - progress_at >= timedelta(seconds=no_progress):
        return False, "no_progress"
    return True, "active_session"


@transaction.atomic
def sync_device(
    device_id: int,
    data: dict,
    presented_secret_sha256: str,
) -> SyncResult:
    now = timezone.now()
    device = (
        ConveyorDevice.objects.select_for_update(of=("self",))
        .select_related("command_session__order")
        .get(pk=device_id)
    )
    if not device.is_active:
        raise ConveyorDeviceError(
            "device_disabled", "Device is disabled", status=401,
        )
    if (
        not isinstance(presented_secret_sha256, str)
        or not hmac.compare_digest(
            device.secret_sha256,
            presented_secret_sha256,
        )
    ):
        raise ConveyorDeviceError(
            "invalid_credential", "Device credential was rotated", status=401,
        )

    boot_id = data["boot_id"]
    sequence = data["seq"]
    rebooted = False
    if device.last_boot_id == boot_id:
        if device.last_sequence is not None and sequence <= device.last_sequence:
            raise ConveyorDeviceError(
                "stale_sequence", "Sequence must strictly increase",
            )
    else:
        rebooted = bool(
            device.last_boot_id is not None or device.command_session_id is not None
        )
        device.last_boot_id = boot_id
        device.last_sequence = None
        device.armed_device_boot_id = None

    device.last_sequence = sequence
    device.last_seen_at = now
    device.last_ack_revision = data["ack_revision"]
    device.output_state = bool(data["output_state"])
    device.feedback_state = bool(data["feedback_state"])
    device.fault = data["fault"] or ""
    device.uptime_ms = data.get("uptime_ms")
    device.wifi_rssi = data.get("wifi_rssi")
    device.firmware = data.get("firmware", "")

    # Select exactly one terminal reason.  In particular, a reboot report may
    # still show the old contactor as ON; that is one reboot transition, not a
    # second "unexpected_on" revision.
    failure_reason = "device_reboot" if rebooted else None
    if failure_reason is None and (
        device.last_ack_revision is not None
        and device.last_ack_revision > device.command_revision
    ):
        failure_reason = "invalid_ack_revision"
    elif failure_reason is None and device.fault:
        failure_reason = "device_fault"
    elif failure_reason is None and not device.desired_state and (
        device.output_state or device.feedback_state
    ):
        failure_reason = "unexpected_on"
    elif failure_reason is None and (
        device.desired_state
        and device.last_ack_revision == device.command_revision
        and not (device.output_state and device.feedback_state)
    ):
        failure_reason = "feedback_mismatch"

    reason = device.stop_reason
    if failure_reason is None and device.desired_state and not device.command_terminal:
        allowed, reason = _session_can_run(device, now)
        if not allowed:
            failure_reason = reason

    terminal_transition = False
    if failure_reason is not None:
        reason = failure_reason
        terminal_transition = _latch_off_locked(device, failure_reason)

    device.save()
    return SyncResult(
        _command_payload(device, reason=reason),
        audit=_audit_payload(device) if terminal_transition else None,
    )


@transaction.atomic
def record_ai_observation(data: dict) -> ObservationResult:
    now = timezone.now()
    camera = data["camera"]
    device = (
        ConveyorDevice.objects.select_for_update(of=("self",))
        .select_related("command_session__order")
        .filter(camera_source=camera, is_active=True)
        .first()
    )
    if device is None:
        raise ConveyorDeviceError(
            "device_not_found", "Active conveyor device not found", status=404,
        )
    session = device.command_session
    if (
        session is None
        or session.pk != data["session_id"]
        or session.camera != camera
        or session.status not in AiCountingSession.OPEN_STATUSES
        or session.conveyor_transport != AiCountingSession.CONVEYOR_CLOUD
        or session.conveyor_observation_mode != AiCountingSession.OBSERVATION_EDGE
        or session.target_total != data["target_total"]
        or device.command_target_total != data["target_total"]
    ):
        raise ConveyorDeviceError(
            "session_mismatch", "Observation does not match the bound session",
        )

    return _apply_ai_observation_locked(
        device,
        edge_boot_id=data["edge_boot_id"],
        sequence=data["seq"],
        total=data["total"],
        terminal_reason=data["terminal_reason"],
        now=now,
    )


def _apply_ai_observation_locked(
    device: ConveyorDevice,
    *,
    edge_boot_id,
    sequence: int,
    total: int,
    terminal_reason: str | None,
    now,
) -> ObservationResult:
    duplicate = False
    edge_restarted = False
    if device.last_ai_boot_id is None:
        device.last_ai_boot_id = edge_boot_id
        device.last_ai_sequence = None
    elif device.last_ai_boot_id != edge_boot_id:
        edge_restarted = True
        device.last_ai_boot_id = edge_boot_id
        device.last_ai_sequence = None

    if device.last_ai_sequence is not None:
        if sequence < device.last_ai_sequence:
            raise ConveyorDeviceError(
                "stale_sequence", "Observation sequence went backwards",
            )
        if sequence == device.last_ai_sequence:
            if (
                total != device.last_ai_reported_total
                or terminal_reason != device.last_ai_terminal_reason
            ):
                raise ConveyorDeviceError(
                    "sequence_conflict", "Sequence was reused with different data",
                )
            duplicate = True
            return ObservationResult(
                _observation_response(device, duplicate=duplicate)
            )

    previous_total = device.last_total
    device.last_ai_sequence = sequence
    device.last_ai_reported_total = total
    device.last_ai_terminal_reason = terminal_reason
    device.last_ai_seen_at = now
    failure_reason = "edge_restarted" if edge_restarted else None
    if total < previous_total:
        if failure_reason is None:
            failure_reason = "counter_regressed"
    else:
        device.last_total = total
        if total > previous_total:
            device.last_progress_at = now

    if failure_reason is None and total >= device.command_target_total:
        failure_reason = "target_reached"
    elif failure_reason is None and terminal_reason is not None:
        failure_reason = (
            "invalid_target_report"
            if terminal_reason == "target_reached"
            else terminal_reason
        )

    transitioned = False
    if failure_reason is not None:
        transitioned = _latch_off_locked(device, failure_reason)

    device.save()
    return ObservationResult(
        _observation_response(device, duplicate=duplicate),
        _audit_payload(device) if transitioned else None,
    )


@transaction.atomic
def record_legacy_ai_observation(
    session_id: int,
    bridge_boot_id,
    total: int,
    *,
    observed_at: datetime,
) -> ObservationResult:
    """Apply one backend-polled legacy counter sample.

    Sequence allocation is performed while holding the device row, so even an
    accidental duplicate monitor cannot refresh freshness with reordered data.
    ``observed_at`` is captured before the legacy HTTP request, ensuring a slow
    response cannot extend the positive observation lease.
    """
    if type(total) is not int or not 0 <= total <= 2_147_483_647:
        raise ConveyorDeviceError(
            "invalid_legacy_total", "Legacy camera returned an invalid total",
            status=503,
        )
    now = timezone.now()
    if (
        not isinstance(observed_at, datetime)
        or timezone.is_naive(observed_at)
        or observed_at > now
    ):
        raise ConveyorDeviceError(
            "invalid_legacy_observed_at",
            "Legacy observation time must be an aware request-start timestamp",
            status=503,
        )
    device = (
        ConveyorDevice.objects.select_for_update(of=("self",))
        .select_related("command_session__order")
        .filter(command_session_id=session_id, is_active=True)
        .first()
    )
    session = device.command_session if device is not None else None
    if (
        device is None
        or session is None
        or session.status not in AiCountingSession.OPEN_STATUSES
        or session.conveyor_transport != AiCountingSession.CONVEYOR_CLOUD
        or session.conveyor_observation_mode
        != AiCountingSession.OBSERVATION_LEGACY_BRIDGE
        or session.legacy_bridge_boot_id != bridge_boot_id
        or session.camera != device.camera_source
        or session.target_total != device.command_target_total
    ):
        raise ConveyorDeviceError(
            "legacy_session_mismatch",
            "Legacy observation does not match its frozen session and leader",
        )
    if device.command_terminal:
        raise ConveyorDeviceError(
            "session_terminal", "Legacy conveyor session is terminal",
        )
    if now - observed_at >= timedelta(milliseconds=_ai_stale_ms(session)):
        transitioned = _latch_off_locked(device, "stale_ai")
        device.save()
        return ObservationResult(
            _observation_response(device, duplicate=False),
            _audit_payload(device) if transitioned else None,
        )
    if device.last_ai_boot_id not in (None, bridge_boot_id):
        transitioned = _latch_off_locked(device, "legacy_bridge_restarted")
        device.save()
        return ObservationResult(
            _observation_response(device, duplicate=False),
            _audit_payload(device) if transitioned else None,
        )
    sequence = 0 if device.last_ai_sequence is None else device.last_ai_sequence + 1
    if sequence > MAX_SEQUENCE:
        transitioned = _latch_off_locked(device, "legacy_sequence_exhausted")
        device.save()
        return ObservationResult(
            _observation_response(device, duplicate=False),
            _audit_payload(device) if transitioned else None,
        )
    return _apply_ai_observation_locked(
        device,
        edge_boot_id=bridge_boot_id,
        sequence=sequence,
        total=total,
        terminal_reason=None,
        # Freshness starts when the legacy HTTP request began, not when a slow
        # or timing-out camera service eventually returned its payload.
        now=observed_at,
    )


@transaction.atomic
def fail_legacy_ai_session(
    session_id: int,
    reason: str,
    *,
    bridge_boot_id=None,
) -> ObservationResult | None:
    """Fence one legacy session OFF without being able to energize anything."""
    device = (
        ConveyorDevice.objects.select_for_update(of=("self",))
        .select_related("command_session")
        .filter(command_session_id=session_id, is_active=True)
        .first()
    )
    if device is None or device.command_session is None:
        return None
    session = device.command_session
    if (
        session.conveyor_observation_mode
        != AiCountingSession.OBSERVATION_LEGACY_BRIDGE
        or (
            bridge_boot_id is not None
            and session.legacy_bridge_boot_id != bridge_boot_id
        )
    ):
        return None
    transitioned = _latch_off_locked(device, reason[:64])
    device.save()
    return ObservationResult(
        _observation_response(device, duplicate=False),
        _audit_payload(device) if transitioned else None,
    )


def _observation_response(device: ConveyorDevice, *, duplicate: bool) -> dict:
    return {
        "protocol_version": 1,
        "accepted": True,
        "duplicate": duplicate,
        "server_time": int(time.time()),
        "command_revision": device.command_revision,
        "desired_state": int(device.desired_state and not device.command_terminal),
        "terminal": device.command_terminal,
        "reason": device.stop_reason,
    }


@transaction.atomic
def emergency_stop(device: ConveyorDevice, reason: str = "emergency_stop") -> ConveyorDevice:
    locked = ConveyorDevice.objects.select_for_update().get(pk=device.pk)
    pending_reason = (
        locked.stop_reason if locked.stop_reason in ADMIN_PENDING_REASONS else None
    )
    # Explicit stop always gets a new revision so an ESP that acknowledged an
    # earlier state cannot mistake this operation for an old OFF response.
    advanced = _next_revision(locked)
    locked.desired_state = False
    locked.command_terminal = True
    if advanced:
        locked.stop_reason = pending_reason or reason
    locked.run_started_at = None
    locked.save()
    return locked


@transaction.atomic
def start_bench_pulse(device: ConveyorDevice) -> ConveyorDevice:
    """Arm one short relay-only pulse on an explicitly isolated bench.

    This is deliberately not a general manual conveyor start. It exists only
    for firmware images which identify themselves as bench builds, and it
    requires fresh physical OFF telemetry before creating a 500 ms lease.
    """
    lock_camera_binding()
    locked = ConveyorDevice.objects.select_for_update().get(pk=device.pk)
    if not locked.is_active:
        raise ConveyorDeviceError(
            "device_disabled", "Bench device is disabled",
        )
    if locked.stop_reason in ADMIN_PENDING_REASONS:
        raise ConveyorDeviceError(
            "device_transition_pending",
            "Finish the pending ESP32 disable or credential rotation first",
        )
    if _camera_has_open_session(locked.camera_source):
        raise ConveyorDeviceError(
            "device_busy", "Close the active AI session before a bench pulse",
        )
    if locked.desired_state or not locked.command_terminal:
        raise ConveyorDeviceError(
            "device_busy", "ESP32 already has a non-terminal command",
        )
    if "bench" not in (locked.firmware or "").casefold():
        raise ConveyorDeviceError(
            "bench_firmware_required",
            "ESP32 must report an explicit bench firmware build",
        )
    if not confirmed_state(locked, False):
        raise ConveyorDeviceError(
            "off_not_confirmed",
            "Fresh acknowledged output OFF and feedback OFF are required",
        )
    if not _next_revision(locked):
        locked.save()
        raise ConveyorDeviceError(
            "revision_exhausted", "ESP32 command revision is exhausted",
        )
    locked.desired_state = True
    locked.command_terminal = False
    locked.stop_reason = BENCH_PULSE_REASON
    locked.run_started_at = timezone.now()
    locked.save()
    return locked


def rotate_secret(device: ConveyorDevice) -> tuple[ConveyorDevice, str, dict]:
    pending_error: ConveyorDeviceError | None = None
    token: str | None = None
    rotation_audit: dict | None = None
    with transaction.atomic():
        lock_camera_binding()
        locked = ConveyorDevice.objects.select_for_update().get(pk=device.pk)
        never_armed = _never_armed_safe_off(locked)
        if locked.stop_reason == "device_disable_pending":
            pending_error = ConveyorDeviceError(
                "device_transition_pending",
                "Finish the pending ESP32 disable before rotating credentials",
            )
        elif _camera_has_open_session(locked.camera_source):
            _latch_off_locked(locked, "credential_rotation_pending")
            locked.save()
            pending_error = ConveyorDeviceError(
                "device_busy",
                "Close the active AI session before rotating credentials",
            )
        elif (
            locked.is_active
            and not never_armed
            and not confirmed_state(locked, False)
        ):
            # Keep the old credential valid long enough for the controller to
            # fetch and physically acknowledge this durable OFF revision.
            _latch_off_locked(locked, "credential_rotation_pending")
            locked.save()
            pending_error = ConveyorDeviceError(
                "off_not_confirmed",
                "ESP32 must report fresh physical OFF before credential rotation",
            )
        else:
            rotation_audit = {
                "rotation_basis": (
                    "never_armed"
                    if never_armed
                    else "fresh_physical_off"
                    if locked.is_active
                    else "inactive"
                ),
                "previous_revision": locked.command_revision,
                "previous_ack_revision": locked.last_ack_revision,
                "last_seen_at": (
                    locked.last_seen_at.isoformat()
                    if locked.last_seen_at is not None
                    else None
                ),
                "output_state": locked.output_state,
                "feedback_state": locked.feedback_state,
            }
            advanced = _next_revision(locked)
            locked.desired_state = False
            locked.command_terminal = True
            if advanced:
                locked.stop_reason = "credential_rotated"
                token = generate_token()
                locked.secret_sha256 = digest_token(token)
            else:
                pending_error = ConveyorDeviceError(
                    "revision_exhausted",
                    "Command revision is exhausted; replace the controller identity",
                )
            locked.run_started_at = None
            locked.save()
    if pending_error is not None:
        raise pending_error
    assert token is not None
    assert rotation_audit is not None
    return locked, token, rotation_audit


def disable_device(device: ConveyorDevice) -> ConveyorDevice:
    pending_error: ConveyorDeviceError | None = None
    with transaction.atomic():
        lock_camera_binding()
        locked = ConveyorDevice.objects.select_for_update().get(pk=device.pk)
        if not locked.is_active:
            return locked
        if locked.stop_reason == "credential_rotation_pending":
            pending_error = ConveyorDeviceError(
                "device_transition_pending",
                "Finish the pending credential rotation before disabling ESP32",
            )
        elif _camera_has_open_session(locked.camera_source):
            _latch_off_locked(locked, "device_disable_pending")
            locked.save()
            pending_error = ConveyorDeviceError(
                "device_busy",
                "Close the active AI session before disabling its ESP32",
            )
        elif not confirmed_state(locked, False):
            # Do not revoke authentication before the old controller has
            # fetched and acknowledged OFF. The administrator retries after
            # the next authenticated heartbeat proves both output channels.
            _latch_off_locked(locked, "device_disable_pending")
            locked.save()
            pending_error = ConveyorDeviceError(
                "off_not_confirmed",
                "ESP32 must report fresh physical OFF before it can be disabled",
            )
        else:
            locked.desired_state = False
            locked.command_terminal = True
            locked.stop_reason = "device_disabled"
            locked.run_started_at = None
            locked.is_active = False
            locked.save()
    if pending_error is not None:
        raise pending_error
    return locked


@transaction.atomic
def enroll_device(values: dict, user) -> tuple[ConveyorDevice, str]:
    camera = values["camera_source"]
    lock_camera_binding()
    if values.get("is_active", True) and _camera_has_open_session(camera):
        raise ConveyorDeviceError(
            "device_busy",
            "Close the active AI session before enabling an ESP32",
        )
    token = generate_token()
    device = ConveyorDevice.objects.create(
        **values,
        secret_sha256=digest_token(token),
        created_by=user,
    )
    return device, token


@transaction.atomic
def update_device(device: ConveyorDevice, values: dict) -> ConveyorDevice:
    lock_camera_binding()
    locked = ConveyorDevice.objects.select_for_update().get(pk=device.pk)
    camera = values.get("camera_source", locked.camera_source)
    next_active = values.get("is_active", locked.is_active)
    if locked.is_active and not next_active:
        raise ConveyorDeviceError(
            "use_disable_endpoint",
            "Use the disable action so physical OFF is confirmed before revoking access",
        )
    activating = next_active and not locked.is_active
    open_binding = AiCountingSession.objects.filter(
        camera__in=(locked.camera_source, camera),
        status__in=AiCountingSession.OPEN_STATUSES,
    ).exists()
    if camera != locked.camera_source and (
        locked.desired_state
        or locked.output_state is True
        or locked.feedback_state is True
        or open_binding
        or (locked.is_active and not confirmed_state(locked, False))
    ):
        raise ConveyorDeviceError(
            "device_busy", "Stop the active conveyor session before rebinding",
        )
    if activating and open_binding:
        raise ConveyorDeviceError(
            "device_busy", "Close the active AI session before enabling an ESP32",
        )
    was_active = locked.is_active
    for field, value in values.items():
        setattr(locked, field, value)
    if was_active != locked.is_active:
        advanced = _next_revision(locked)
        locked.desired_state = False
        locked.command_terminal = True
        if advanced:
            locked.stop_reason = (
                "device_enabled" if locked.is_active else "device_disabled"
            )
        locked.run_started_at = None
    locked.save()
    return locked


@transaction.atomic
def prepare_session(session: AiCountingSession) -> ConveyorDevice:
    # The transport is frozen when the order reserves the camera.  Re-reading
    # mutable deployment settings here could switch an already-open workflow
    # to a different physical master after a restart/config rollout.
    if session.conveyor_transport != AiCountingSession.CONVEYOR_CLOUD:
        raise ConveyorDeviceError(
            "transport_not_cloud", "Session is not bound to cloud control",
        )
    device = cloud_device_for(session.camera, lock=True)
    if device is None:
        raise ConveyorDeviceError(
            "device_not_found", "Active conveyor device not found", status=503,
        )
    if device.stop_reason in ADMIN_PENDING_REASONS:
        raise ConveyorDeviceError(
            "device_transition_pending",
            "Finish the pending ESP32 disable or credential rotation first",
        )
    if device.command_session_id not in (None, session.pk):
        previous = device.command_session
        previous_open = bool(
            previous is not None
            and previous.status in AiCountingSession.OPEN_STATUSES
        )
        if (
            previous_open
            or not device.command_terminal
            or device.desired_state
            or not confirmed_state(device, False)
        ):
            raise ConveyorDeviceError(
                "device_busy",
                "Previous session must be closed with fresh physical OFF proof",
            )
    if device.command_session_id == session.pk:
        if (
            device.command_target_total != session.target_total
            or device.command_terminal
            or device.desired_state
        ):
            raise ConveyorDeviceError(
                "session_terminal",
                "Existing cloud session cannot be automatically re-prepared",
            )
        return device
    if not _next_revision(device):
        device.save()
        return device
    device.desired_state = False
    device.command_terminal = False
    device.stop_reason = "prepared"
    device.command_session = session
    device.command_target_total = session.target_total
    device.armed_device_boot_id = None
    device.armed_edge_boot_id = None
    device.last_ai_boot_id = None
    device.last_ai_sequence = None
    device.last_ai_reported_total = None
    device.last_ai_terminal_reason = None
    device.last_ai_seen_at = None
    device.last_total = 0
    device.last_progress_at = None
    device.run_started_at = None
    device.save()
    return device


def prepared_off(device: ConveyorDevice) -> bool:
    now = timezone.now()
    fresh_ms = int(getattr(settings, "CONVEYOR_DEVICE_FRESH_MS", 1500))
    return bool(
        device.is_active
        and device.last_seen_at is not None
        and now - device.last_seen_at < timedelta(milliseconds=fresh_ms)
        and device.last_ack_revision == device.command_revision
        and device.output_state is False
        and device.feedback_state is False
        and not device.fault
        and device.last_boot_id is not None
    )


@transaction.atomic
def arm_session(session: AiCountingSession) -> ConveyorDevice:
    device = (
        ConveyorDevice.objects.select_for_update(of=("self",))
        .select_related("command_session")
        .get(camera_source=session.camera, is_active=True)
    )
    if (
        session.conveyor_transport != AiCountingSession.CONVEYOR_CLOUD
        or device.command_session_id != session.pk
        or device.command_target_total != session.target_total
        or device.command_terminal
        or not prepared_off(device)
        or device.last_ai_seen_at is None
        or device.last_ai_boot_id is None
    ):
        raise ConveyorDeviceError(
            "not_ready", "Fresh AI and physical OFF confirmation are required",
            status=503,
        )
    stale_ms = _ai_stale_ms(session)
    if timezone.now() - device.last_ai_seen_at >= timedelta(milliseconds=stale_ms):
        raise ConveyorDeviceError(
            "ai_observation_stale", "AI observation is stale", status=503,
        )
    if not _next_revision(device):
        device.save()
        return device
    device.desired_state = True
    device.stop_reason = "active_session"
    device.armed_device_boot_id = device.last_boot_id
    device.armed_edge_boot_id = device.last_ai_boot_id
    device.run_started_at = timezone.now()
    device.last_progress_at = device.run_started_at
    device.save()
    return device


def confirmed_state(device: ConveyorDevice, desired: bool) -> bool:
    now = timezone.now()
    fresh_ms = int(getattr(settings, "CONVEYOR_DEVICE_FRESH_MS", 1500))
    return bool(
        device.last_seen_at is not None
        and now - device.last_seen_at < timedelta(milliseconds=fresh_ms)
        and device.last_ack_revision == device.command_revision
        and device.output_state is desired
        and device.feedback_state is desired
        and not device.fault
    )


def _never_armed_safe_off(device: ConveyorDevice) -> bool:
    """Allow revoking a controller that the server has never armed.

    ``command_session`` is durable provenance: it is assigned before any ON
    lease and is never cleared.  A device without that provenance cannot have
    received server-authorised ON.  This also recovers an unprovisioned or
    stale-OFF controller whose credential was exposed, without requiring the
    compromised credential merely to acknowledge another OFF revision.

    Any observed ON, fault, AI history, or session history still requires a
    fresh independent physical OFF acknowledgement.
    """
    no_run_history = bool(
        device.command_session_id is None
        and device.command_target_total is None
        and device.run_started_at is None
        and device.armed_device_boot_id is None
        and device.armed_edge_boot_id is None
        and device.last_ai_seen_at is None
        and device.last_ai_boot_id is None
        and device.last_ai_sequence is None
        and device.last_ai_reported_total is None
        and device.last_ai_terminal_reason is None
        and device.last_total == 0
        and device.last_progress_at is None
    )
    durable_off = bool(
        device.desired_state is False
        and device.command_terminal is True
    )
    never_seen = bool(
        device.last_seen_at is None
        and device.last_boot_id is None
        and device.last_sequence is None
        and device.last_ack_revision is None
        and device.output_state is None
        and device.feedback_state is None
        and not device.fault
    )
    last_reported_safe_off = bool(
        device.last_seen_at is not None
        and device.last_boot_id is not None
        and device.last_sequence is not None
        and device.last_ack_revision is not None
        and device.last_ack_revision <= device.command_revision
        and device.output_state is False
        and device.feedback_state is False
        and not device.fault
    )
    return no_run_history and durable_off and (
        never_seen or last_reported_safe_off
    )


def wait_prepared(session: AiCountingSession, timeout: float) -> ConveyorDevice:
    deadline = time.monotonic() + timeout
    while True:
        device = (
            ConveyorDevice.objects.select_related("command_session")
            .filter(camera_source=session.camera, is_active=True)
            .first()
        )
        if device is None:
            raise ConveyorDeviceError(
                "device_not_found", "Active conveyor device not found", status=503,
            )
        if (
            device.command_session_id != session.pk
            or device.command_target_total != session.target_total
            or device.command_terminal
        ):
            raise ConveyorDeviceError(
                "session_terminal", "Cloud conveyor session is no longer prepared",
            )
        now = timezone.now()
        stale_ms = _ai_stale_ms(session)
        ai_fresh = bool(
            device.last_ai_seen_at is not None
            and now - device.last_ai_seen_at < timedelta(milliseconds=stale_ms)
            and device.last_ai_boot_id is not None
        )
        if prepared_off(device) and ai_fresh:
            return device
        if time.monotonic() >= deadline:
            raise ConveyorDeviceError(
                "prepare_timeout",
                "Fresh ESP32 OFF feedback and AI observation were not received",
                status=503,
            )
        time.sleep(0.1)


def wait_confirmed(
    device_id: int,
    revision: int,
    desired: bool,
    timeout: float,
    *,
    seen_after=None,
) -> ConveyorDevice:
    deadline = time.monotonic() + timeout
    while True:
        device = ConveyorDevice.objects.get(pk=device_id)
        if device.command_revision != revision:
            raise ConveyorDeviceError(
                "command_superseded", "Conveyor command was superseded",
            )
        if desired and (device.command_terminal or not device.desired_state):
            raise ConveyorDeviceError(
                "session_terminal", "Conveyor start was terminally stopped",
            )
        if (
            confirmed_state(device, desired)
            and (
                seen_after is None
                or (
                    device.last_seen_at is not None
                    and device.last_seen_at > seen_after
                )
            )
        ):
            return device
        if time.monotonic() >= deadline:
            raise ConveyorDeviceError(
                "confirmation_timeout",
                "ESP32 did not confirm the physical state",
                status=503,
            )
        time.sleep(0.1)


def control_payload(device: ConveyorDevice) -> dict:
    online = False
    if device.last_seen_at is not None:
        fresh_ms = int(getattr(settings, "CONVEYOR_DEVICE_FRESH_MS", 1500))
        online = bool(
            device.is_active
            and timezone.now() - device.last_seen_at < timedelta(
                milliseconds=fresh_ms
            )
        )
    desired = bool(device.desired_state and not device.command_terminal)
    feedback = None if device.feedback_state is None else int(device.feedback_state)
    goal_reached = bool(
        device.command_target_total is not None
        and device.last_total >= device.command_target_total
    )
    if desired and confirmed_state(device, True):
        state = "running"
    elif desired:
        state = "starting"
    elif goal_reached and confirmed_state(device, False):
        state = "goal_reached"
    elif not device.command_terminal and prepared_off(device):
        state = "armed"
    elif confirmed_state(device, False):
        state = "off"
    else:
        state = "fault" if device.command_terminal else "unknown"
    return {
        "configured": True,
        "enabled": True,
        "transport": "cloud",
        "session_id": device.command_session_id,
        "target_total": device.command_target_total,
        "total": device.last_total,
        "state": state,
        "desired": int(desired),
        "feedback": feedback,
        "online": online,
        "terminal": device.command_terminal,
        "goal_reached": goal_reached,
        "stop_reason": device.stop_reason,
        "last_seen_at": (
            device.last_seen_at.isoformat() if device.last_seen_at else None
        ),
        "revision": device.command_revision,
        "fault": device.fault or None,
    }


def control_payload_for_session(session: AiCountingSession) -> dict:
    device = ConveyorDevice.objects.filter(
        camera_source=session.camera,
        command_session_id=session.pk,
    ).first()
    if device is not None:
        return control_payload(device)
    return {
        "configured": True,
        "enabled": True,
        "transport": "cloud",
        "session_id": session.pk,
        "target_total": session.target_total,
        "total": 0,
        "state": "fault",
        "desired": 0,
        "feedback": None,
        "online": False,
        "terminal": True,
        "goal_reached": False,
        "stop_reason": "device_missing",
        "last_seen_at": None,
        "revision": None,
        "fault": "device_missing",
    }
