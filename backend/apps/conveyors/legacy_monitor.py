"""Fail-safe polling bridge for legacy camera counting workers.

The legacy camera service can expose a counter but cannot push authenticated
observations to Django.  This module keeps that compatibility path deliberately
small: one PostgreSQL leader owns a random boot identity, polls only durably
bound legacy sessions, and can only refresh observations or latch the conveyor
OFF.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone
from legacy_conveyor_monitor_healthcheck import HEARTBEAT_VERSION

from apps.cameras import ai
from apps.cameras.models import AiCountingSession
from apps.eventlog.services import log_event
from apps.orders.models import Order

from .models import ConveyorDevice
from .services import (
    ConveyorDeviceError,
    fail_legacy_ai_session,
    record_legacy_ai_observation,
)

log = logging.getLogger(__name__)

ADVISORY_LOCK_KEY = 0x4C45474143594252  # "LEGACYBR", signed bigint-safe.
MAX_LEGACY_TOTAL = 2_147_483_647


class LeadershipError(RuntimeError):
    """The process does not hold the PostgreSQL monitor lock."""


class LegacyStatusError(ValueError):
    """A legacy status payload cannot safely refresh an ESP lease."""

    def __init__(self, reason: str, detail: str):
        self.reason = reason
        super().__init__(detail)


@dataclass(frozen=True)
class LegacyPollTarget:
    session_id: int
    camera: str
    target_total: int


@dataclass(frozen=True)
class RemotePoll:
    target: LegacyPollTarget
    payload: dict[str, Any] | None = None
    failure_reason: str | None = None
    observed_at: datetime | None = None


@dataclass(frozen=True)
class ClaimResult:
    claimed: int
    foreign_session_ids: tuple[int, ...]


@dataclass(frozen=True)
class CycleStats:
    claimed: int = 0
    fenced: int = 0
    polled: int = 0
    observed: int = 0
    failed: int = 0


class AdvisoryLeader:
    """A session-scoped PostgreSQL advisory-lock lease."""

    def __init__(self, backend_pid: int):
        self.backend_pid = backend_pid
        self._released = False

    @classmethod
    def try_acquire(cls) -> AdvisoryLeader | None:
        if connection.vendor != "postgresql":
            raise LeadershipError(
                "legacy conveyor monitor requires PostgreSQL advisory locks"
            )
        connection.ensure_connection()
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_try_advisory_lock(%s), pg_backend_pid()",
                [ADVISORY_LOCK_KEY],
            )
            row = cursor.fetchone()
        if row is None or row[0] is not True:
            return None
        return cls(int(row[1]))

    def ensure_current(self) -> None:
        if self._released or connection.connection is None:
            raise LeadershipError("legacy monitor database session was lost")
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                row = cursor.fetchone()
        except Exception as exc:
            raise LeadershipError(
                "legacy monitor database session was lost"
            ) from exc
        if row is None or int(row[0]) != self.backend_pid:
            raise LeadershipError("legacy monitor advisory lock was lost")

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        if connection.connection is None:
            return
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                row = cursor.fetchone()
                if row is not None and int(row[0]) == self.backend_pid:
                    cursor.execute(
                        "SELECT pg_advisory_unlock(%s)",
                        [ADVISORY_LOCK_KEY],
                    )
        except Exception:
            # PostgreSQL releases the lock with the dead connection.  Cleanup
            # must never conceal the original monitor failure.
            log.exception("Could not explicitly release legacy monitor lock")


def _log_automatic_stop(audit: dict[str, Any] | None) -> None:
    if audit is None:
        return
    order = None
    session_id = audit.get("session_id")
    if session_id is not None:
        order = Order.objects.filter(
            ai_counting_sessions__pk=session_id
        ).first()
    log_event(
        "conveyor_auto_stop",
        f"Автоматическая остановка {audit['camera']}: {audit['reason']}",
        order=order,
        payload=audit,
    )


@transaction.atomic
def claim_open_sessions(bridge_boot_id: uuid.UUID) -> ClaimResult:
    """Claim only unowned OPEN legacy rows; never adopt another boot."""

    sessions = list(
        AiCountingSession.objects.select_for_update()
        .filter(
            status__in=AiCountingSession.OPEN_STATUSES,
            conveyor_transport=AiCountingSession.CONVEYOR_CLOUD,
            conveyor_observation_mode=(
                AiCountingSession.OBSERVATION_LEGACY_BRIDGE
            ),
        )
        .only("pk", "legacy_bridge_boot_id")
        .order_by("pk")
    )
    claimed = 0
    foreign: list[int] = []
    for session in sessions:
        if session.legacy_bridge_boot_id is None:
            session.legacy_bridge_boot_id = bridge_boot_id
            session.save(update_fields=["legacy_bridge_boot_id"])
            claimed += 1
        elif session.legacy_bridge_boot_id != bridge_boot_id:
            foreign.append(session.pk)
    return ClaimResult(claimed, tuple(foreign))


def poll_targets(bridge_boot_id: uuid.UUID) -> list[LegacyPollTarget]:
    """Return only prepared/active device bindings owned by this process."""

    devices = (
        ConveyorDevice.objects.select_related("command_session")
        .filter(
            is_active=True,
            command_terminal=False,
            command_session__status__in=AiCountingSession.OPEN_STATUSES,
            command_session__conveyor_transport=(
                AiCountingSession.CONVEYOR_CLOUD
            ),
            command_session__conveyor_observation_mode=(
                AiCountingSession.OBSERVATION_LEGACY_BRIDGE
            ),
            command_session__legacy_bridge_boot_id=bridge_boot_id,
        )
        .order_by("command_session_id")
    )
    return [
        LegacyPollTarget(
            session_id=device.command_session_id,
            camera=device.camera_source,
            target_total=device.command_target_total or 0,
        )
        for device in devices
    ]


def validate_legacy_status(
    target: LegacyPollTarget,
    payload: object,
) -> int:
    """Validate the small legacy contract without coercing scalar values."""

    if not isinstance(payload, dict):
        raise LegacyStatusError(
            "legacy_bridge_invalid", "legacy status is not an object"
        )
    if payload.get("running") is not True:
        raise LegacyStatusError(
            "legacy_worker_stopped", "legacy worker is not running"
        )
    if payload.get("mode") != "session":
        raise LegacyStatusError(
            "legacy_session_mismatch", "legacy worker is not in session mode"
        )
    if payload.get("processor_alive") is not True:
        raise LegacyStatusError(
            "legacy_worker_unhealthy", "legacy processor is not alive"
        )
    if payload.get("error") not in (None, ""):
        raise LegacyStatusError(
            "legacy_worker_unhealthy", "legacy worker reported an error"
        )

    camera = payload.get("cam", payload.get("camera"))
    if camera != target.camera:
        raise LegacyStatusError(
            "legacy_session_mismatch", "legacy camera identity does not match"
        )

    # Historical workers have no session binding and return either no fields
    # or null.  A worker which does advertise identity must match exactly.
    session_id = payload.get("session_id")
    if session_id is not None and (
        type(session_id) is not int or session_id != target.session_id
    ):
        raise LegacyStatusError(
            "legacy_session_mismatch", "legacy session identity does not match"
        )
    target_total = payload.get("target_total")
    if target_total is not None and (
        type(target_total) is not int or target_total != target.target_total
    ):
        raise LegacyStatusError(
            "legacy_session_mismatch", "legacy target does not match"
        )

    total = payload.get("total")
    if type(total) is not int or not 0 <= total <= MAX_LEGACY_TOTAL:
        raise LegacyStatusError(
            "legacy_bridge_invalid", "legacy total is invalid"
        )
    return total


def _remote_poll(
    target: LegacyPollTarget,
    timeout_seconds: float,
) -> RemotePoll:
    observed_at = timezone.now()
    try:
        payload = ai.legacy_status(
            target.camera,
            timeout_seconds=timeout_seconds,
        )
    except ai.AiError as exc:
        log.warning(
            "Legacy camera HTTP refusal camera=%s session=%s status=%s",
            target.camera,
            target.session_id,
            exc.status,
        )
        return RemotePoll(
            target,
            failure_reason="legacy_bridge_http_error",
            observed_at=observed_at,
        )
    except ai.AiUnavailable:
        log.warning(
            "Legacy camera unavailable camera=%s session=%s",
            target.camera,
            target.session_id,
        )
        return RemotePoll(
            target,
            failure_reason="legacy_bridge_unavailable",
            observed_at=observed_at,
        )
    except Exception:
        log.exception(
            "Unexpected legacy camera poll failure camera=%s session=%s",
            target.camera,
            target.session_id,
        )
        return RemotePoll(
            target,
            failure_reason="legacy_bridge_unavailable",
            observed_at=observed_at,
        )
    if payload is None:
        return RemotePoll(
            target,
            failure_reason="legacy_worker_missing",
            observed_at=observed_at,
        )
    return RemotePoll(target, payload=payload, observed_at=observed_at)


def poll_remote_statuses(
    targets: list[LegacyPollTarget],
    timeout_seconds: float,
) -> list[RemotePoll]:
    """Poll all configured cameras within one request-timeout window."""

    if not targets:
        return []
    executor = ThreadPoolExecutor(
        max_workers=len(targets),
        thread_name_prefix="legacy-conveyor-poll",
    )
    futures: dict[Future[RemotePoll], LegacyPollTarget] = {
        executor.submit(_remote_poll, target, timeout_seconds): target
        for target in targets
    }
    done, pending = wait(futures, timeout=max(0.001, timeout_seconds) + 0.05)
    outcomes: list[RemotePoll] = []
    for future in done:
        outcomes.append(future.result())
    for future in pending:
        future.cancel()
        outcomes.append(
            RemotePoll(
                futures[future],
                failure_reason="legacy_bridge_timeout",
            )
        )
    # legacy_status itself has the same hard timeout.  Avoid extending the
    # control-loop deadline merely to join a worker which is already fenced.
    executor.shutdown(wait=False, cancel_futures=True)
    return sorted(outcomes, key=lambda item: item.target.session_id)


class LegacyConveyorMonitor:
    def __init__(
        self,
        *,
        boot_id: uuid.UUID | None = None,
        request_timeout_seconds: float | None = None,
    ):
        self.boot_id = boot_id or uuid.uuid4()
        self.request_timeout_seconds = (
            float(request_timeout_seconds)
            if request_timeout_seconds is not None
            else float(
                getattr(
                    settings,
                    "CONVEYOR_LEGACY_BRIDGE_REQUEST_TIMEOUT_MS",
                    350,
                )
            )
            / 1000.0
        )

    def _fail(self, session_id: int, reason: str, *, owned: bool = True) -> bool:
        result = fail_legacy_ai_session(
            session_id,
            reason,
            bridge_boot_id=self.boot_id if owned else None,
        )
        if result is None:
            return False
        _log_automatic_stop(result.audit)
        return result.audit is not None

    def run_cycle(self) -> CycleStats:
        claim = claim_open_sessions(self.boot_id)
        fenced = 0
        for session_id in claim.foreign_session_ids:
            fenced += int(
                self._fail(
                    session_id,
                    "legacy_bridge_restarted",
                    owned=False,
                )
            )

        targets = poll_targets(self.boot_id)
        outcomes = poll_remote_statuses(
            targets,
            self.request_timeout_seconds,
        )
        observed = 0
        failed = 0
        for outcome in outcomes:
            if outcome.failure_reason is not None:
                self._fail(outcome.target.session_id, outcome.failure_reason)
                failed += 1
                continue
            try:
                total = validate_legacy_status(
                    outcome.target,
                    outcome.payload,
                )
            except LegacyStatusError as exc:
                log.warning(
                    "Rejected legacy status camera=%s session=%s: %s",
                    outcome.target.camera,
                    outcome.target.session_id,
                    exc,
                )
                self._fail(outcome.target.session_id, exc.reason)
                failed += 1
                continue
            try:
                result = record_legacy_ai_observation(
                    outcome.target.session_id,
                    self.boot_id,
                    total,
                    observed_at=outcome.observed_at,
                )
            except ConveyorDeviceError as exc:
                log.warning(
                    "Could not apply legacy status camera=%s session=%s code=%s",
                    outcome.target.camera,
                    outcome.target.session_id,
                    exc.code,
                )
                self._fail(
                    outcome.target.session_id,
                    "legacy_bridge_record_error",
                )
                failed += 1
                continue
            _log_automatic_stop(result.audit)
            observed += 1

        return CycleStats(
            claimed=claim.claimed,
            fenced=fenced,
            polled=len(targets),
            observed=observed,
            failed=failed,
        )

    def shutdown(self) -> int:
        """Best-effort terminal OFF for every session owned by this boot."""

        session_ids = list(
            AiCountingSession.objects.filter(
                status__in=AiCountingSession.OPEN_STATUSES,
                conveyor_transport=AiCountingSession.CONVEYOR_CLOUD,
                conveyor_observation_mode=(
                    AiCountingSession.OBSERVATION_LEGACY_BRIDGE
                ),
                legacy_bridge_boot_id=self.boot_id,
            ).values_list("pk", flat=True)
        )
        stopped = 0
        for session_id in session_ids:
            stopped += int(
                self._fail(session_id, "legacy_bridge_shutdown")
            )
        return stopped


def write_heartbeat(
    path: str | Path,
    *,
    state: str,
    boot_id: uuid.UUID,
    db_backend_pid: int,
    now: float | None = None,
    process_id: int | None = None,
) -> None:
    """Atomically publish process liveness for the in-container healthcheck."""

    heartbeat = Path(path)
    heartbeat.parent.mkdir(parents=True, exist_ok=True)
    pid = os.getpid() if process_id is None else process_id
    payload = {
        "version": HEARTBEAT_VERSION,
        "state": state,
        "timestamp": time.time() if now is None else now,
        "pid": pid,
        "boot_id": str(boot_id),
        "db_backend_pid": db_backend_pid,
    }
    temporary = heartbeat.with_name(f".{heartbeat.name}.{pid}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, heartbeat)
    finally:
        temporary.unlink(missing_ok=True)
