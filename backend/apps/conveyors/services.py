from __future__ import annotations

import hmac
import time
from dataclasses import dataclass
from datetime import timedelta

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
    lease_ms = int(getattr(settings, "CONVEYOR_DEVICE_LEASE_MS", 1200))
    lease_ms = min(1500, max(1, lease_ms)) if enabled else 0
    return {
        "protocol_version": 1,
        "server_time": int(time.time()),
        "next_sync_ms": int(getattr(settings, "CONVEYOR_DEVICE_SYNC_MS", 500)),
        "command": {
            "revision": device.command_revision,
            "state": int(enabled),
            "lease_ms": lease_ms,
            "session_id": device.command_session_id if enabled else None,
            "target_total": device.command_target_total if enabled else None,
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


def _session_can_run(device: ConveyorDevice, now) -> tuple[bool, str]:
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
    stale_ms = int(getattr(settings, "CONVEYOR_AI_STALE_MS", 1500))
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
        or session.target_total != data["target_total"]
        or device.command_target_total != data["target_total"]
    ):
        raise ConveyorDeviceError(
            "session_mismatch", "Observation does not match the bound session",
        )

    edge_boot_id = data["edge_boot_id"]
    sequence = data["seq"]
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
                data["total"] != device.last_ai_reported_total
                or data["terminal_reason"] != device.last_ai_terminal_reason
            ):
                raise ConveyorDeviceError(
                    "sequence_conflict", "Sequence was reused with different data",
                )
            duplicate = True
            return ObservationResult(
                _observation_response(device, duplicate=duplicate)
            )

    previous_total = device.last_total
    total = data["total"]
    device.last_ai_sequence = sequence
    device.last_ai_reported_total = total
    device.last_ai_terminal_reason = data["terminal_reason"]
    device.last_ai_seen_at = now
    failure_reason = "edge_restarted" if edge_restarted else None
    if total < previous_total:
        if failure_reason is None:
            failure_reason = "counter_regressed"
    else:
        device.last_total = total
        if total > previous_total:
            device.last_progress_at = now

    terminal_reason = data["terminal_reason"]
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


def rotate_secret(device: ConveyorDevice) -> tuple[ConveyorDevice, str]:
    pending_error: ConveyorDeviceError | None = None
    token: str | None = None
    with transaction.atomic():
        lock_camera_binding()
        locked = ConveyorDevice.objects.select_for_update().get(pk=device.pk)
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
            and not _never_seen_safe_off(locked)
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
    return locked, token


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
    stale_ms = int(getattr(settings, "CONVEYOR_AI_STALE_MS", 1500))
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


def _never_seen_safe_off(device: ConveyorDevice) -> bool:
    """Allow revoking an unprovisioned credential without first using it.

    A controller that has never authenticated cannot have received a leased ON
    command.  Keep the predicate deliberately strict so any device or session
    history still requires a fresh, independent physical OFF acknowledgement.
    """
    return bool(
        device.last_seen_at is None
        and device.last_boot_id is None
        and device.last_sequence is None
        and device.last_ack_revision is None
        and device.output_state is None
        and device.feedback_state is None
        and device.command_session_id is None
        and device.command_target_total is None
        and device.desired_state is False
        and device.command_terminal is True
        and device.run_started_at is None
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
        stale_ms = int(getattr(settings, "CONVEYOR_AI_STALE_MS", 1500))
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
