import hashlib
import json
import os
import uuid
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.cameras import ai
from apps.cameras.models import AiCountingSession
from apps.clients.models import Client
from apps.conveyors import legacy_monitor
from apps.conveyors.legacy_monitor import (
    AdvisoryLeader,
    LeadershipError,
    LegacyConveyorMonitor,
    LegacyPollTarget,
    LegacyStatusError,
    check_heartbeat,
    claim_open_sessions,
    validate_legacy_status,
    write_heartbeat,
)
from apps.conveyors.models import ConveyorDevice
from apps.eventlog.models import EventLog
from apps.orders.models import Order

pytestmark = pytest.mark.django_db

BOOT_ID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
OLD_BOOT_ID = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


@pytest.fixture
def legacy_session_factory():
    sequence = 0

    def make(
        *,
        camera="cam2",
        status=AiCountingSession.STARTING,
        target=10,
        boot_id=None,
        with_device=True,
        observation_mode=AiCountingSession.OBSERVATION_LEGACY_BRIDGE,
    ):
        nonlocal sequence
        sequence += 1
        client = Client.objects.create_with_user(
            first_name=f"Legacy {sequence}",
            last_name="Monitor",
            phone=f"legacy-monitor-{sequence}",
        )
        order = Order.objects.create(client=client, status="confirmed")
        session = AiCountingSession.objects.create(
            order=order,
            camera=camera,
            status=status,
            target_total=target,
            conveyor_enabled=True,
            conveyor_transport=AiCountingSession.CONVEYOR_CLOUD,
            conveyor_observation_mode=observation_mode,
            legacy_bridge_boot_id=boot_id,
        )
        device = None
        if with_device:
            device = ConveyorDevice.objects.create(
                name=f"Legacy ESP {sequence}",
                camera_source=camera,
                secret_sha256=hashlib.sha256(
                    f"legacy-device-{sequence}".encode()
                ).hexdigest(),
                desired_state=status == AiCountingSession.ACTIVE,
                command_revision=10,
                command_session=session,
                command_target_total=target,
                command_terminal=False,
                stop_reason=(
                    "active_session"
                    if status == AiCountingSession.ACTIVE
                    else "prepared"
                ),
            )
        return order, session, device

    return make


def status_payload(session, total=0, **overrides):
    payload = {
        "cam": session.camera,
        "running": True,
        "mode": "session",
        "processor_alive": True,
        "error": None,
        "total": total,
        # A historical session worker has no durable backend identity.
        "session_id": None,
        "target_total": None,
    }
    payload.update(overrides)
    return payload


def test_claims_null_boot_for_open_legacy_only(legacy_session_factory):
    _, legacy, _ = legacy_session_factory(camera="cam2")
    _, edge, _ = legacy_session_factory(
        camera="cam3",
        observation_mode=AiCountingSession.OBSERVATION_EDGE,
    )
    _, closed, _ = legacy_session_factory(
        camera="cam4",
        status=AiCountingSession.CLOSED,
    )

    result = claim_open_sessions(BOOT_ID)

    assert result.claimed == 1
    assert result.foreign_session_ids == ()
    legacy.refresh_from_db()
    edge.refresh_from_db()
    closed.refresh_from_db()
    assert legacy.legacy_bridge_boot_id == BOOT_ID
    assert edge.legacy_bridge_boot_id is None
    assert closed.legacy_bridge_boot_id is None


def test_prepared_device_is_required_before_polling(
    legacy_session_factory,
    monkeypatch,
):
    _, session, _ = legacy_session_factory(with_device=False)
    monkeypatch.setattr(
        ai,
        "legacy_status",
        lambda *_args, **_kwargs: pytest.fail("pre-prepare session was polled"),
        raising=False,
    )

    stats = LegacyConveyorMonitor(boot_id=BOOT_ID).run_cycle()

    session.refresh_from_db()
    assert session.legacy_bridge_boot_id == BOOT_ID
    assert stats.claimed == 1
    assert stats.polled == 0
    assert stats.failed == 0


@pytest.mark.parametrize(
    "session_status",
    [AiCountingSession.STARTING, AiCountingSession.ACTIVE],
)
def test_polls_starting_and_active_bindings_with_bounded_timeout(
    legacy_session_factory,
    monkeypatch,
    session_status,
):
    _, session, device = legacy_session_factory(status=session_status)
    calls = []

    def legacy_status(camera, *, timeout_seconds):
        calls.append((camera, timeout_seconds))
        return status_payload(session, total=3)

    monkeypatch.setattr(ai, "legacy_status", legacy_status, raising=False)
    monitor = LegacyConveyorMonitor(
        boot_id=BOOT_ID,
        request_timeout_seconds=0.35,
    )

    stats = monitor.run_cycle()

    device.refresh_from_db()
    assert calls == [(session.camera, 0.35)]
    assert stats.polled == 1
    assert stats.observed == 1
    assert stats.failed == 0
    assert device.last_ai_boot_id == BOOT_ID
    assert device.last_ai_sequence == 0
    assert device.last_total == 3
    assert device.command_terminal is False


def test_matching_advertised_session_identity_is_accepted(
    legacy_session_factory,
    monkeypatch,
):
    _, session, device = legacy_session_factory()
    monkeypatch.setattr(
        ai,
        "legacy_status",
        lambda _camera, *, timeout_seconds: status_payload(
            session,
            total=2,
            session_id=session.pk,
            target_total=session.target_total,
        ),
        raising=False,
    )

    LegacyConveyorMonitor(boot_id=BOOT_ID).run_cycle()

    device.refresh_from_db()
    assert device.last_total == 2
    assert device.command_terminal is False


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"running": 1}, "legacy_worker_stopped"),
        ({"running": False}, "legacy_worker_stopped"),
        ({"mode": "always_on"}, "legacy_session_mismatch"),
        ({"processor_alive": False}, "legacy_worker_unhealthy"),
        ({"error": "decoder failed"}, "legacy_worker_unhealthy"),
        ({"cam": "cam3"}, "legacy_session_mismatch"),
        ({"session_id": True}, "legacy_session_mismatch"),
        ({"session_id": 999}, "legacy_session_mismatch"),
        ({"target_total": 999}, "legacy_session_mismatch"),
        ({"total": True}, "legacy_bridge_invalid"),
        ({"total": -1}, "legacy_bridge_invalid"),
        ({"total": 2_147_483_648}, "legacy_bridge_invalid"),
    ],
)
def test_status_validation_is_strict(changes, reason):
    target = LegacyPollTarget(session_id=7, camera="cam2", target_total=10)
    payload = {
        "cam": "cam2",
        "running": True,
        "mode": "session",
        "processor_alive": True,
        "total": 3,
    }
    payload.update(changes)

    with pytest.raises(LegacyStatusError) as caught:
        validate_legacy_status(target, payload)

    assert caught.value.reason == reason


def test_invalid_status_latches_off_and_writes_audit(
    legacy_session_factory,
    monkeypatch,
):
    order, session, device = legacy_session_factory(
        status=AiCountingSession.ACTIVE
    )
    monkeypatch.setattr(
        ai,
        "legacy_status",
        lambda _camera, *, timeout_seconds: status_payload(
            session,
            running=False,
        ),
        raising=False,
    )

    stats = LegacyConveyorMonitor(boot_id=BOOT_ID).run_cycle()

    device.refresh_from_db()
    assert stats.failed == 1
    assert device.command_terminal is True
    assert device.desired_state is False
    assert device.command_revision == 11
    assert device.stop_reason == "legacy_worker_stopped"
    event = EventLog.objects.get(event_type="conveyor_auto_stop")
    assert event.order == order
    assert event.payload["session_id"] == session.pk
    assert event.payload["reason"] == "legacy_worker_stopped"


def test_camera_timeout_latches_off(legacy_session_factory, monkeypatch):
    _, _session, device = legacy_session_factory(
        status=AiCountingSession.ACTIVE
    )

    def unavailable(_camera, *, timeout_seconds):
        raise ai.AiUnavailable("timed out")

    monkeypatch.setattr(ai, "legacy_status", unavailable, raising=False)

    LegacyConveyorMonitor(boot_id=BOOT_ID).run_cycle()

    device.refresh_from_db()
    assert device.command_terminal is True
    assert device.desired_state is False
    assert device.stop_reason == "legacy_bridge_unavailable"


@pytest.mark.parametrize(
    ("previous_total", "incoming_total", "reason"),
    [
        (0, 10, "target_reached"),
        (5, 4, "counter_regressed"),
    ],
)
def test_counter_terminal_conditions_are_audited(
    legacy_session_factory,
    monkeypatch,
    previous_total,
    incoming_total,
    reason,
):
    _, session, device = legacy_session_factory(
        status=AiCountingSession.ACTIVE,
        target=10,
        boot_id=BOOT_ID,
    )
    device.last_total = previous_total
    device.last_ai_boot_id = BOOT_ID
    device.last_ai_sequence = 0
    device.last_ai_reported_total = previous_total
    device.save()
    monkeypatch.setattr(
        ai,
        "legacy_status",
        lambda _camera, *, timeout_seconds: status_payload(
            session,
            total=incoming_total,
        ),
        raising=False,
    )

    stats = LegacyConveyorMonitor(boot_id=BOOT_ID).run_cycle()

    device.refresh_from_db()
    assert stats.observed == 1
    assert device.command_terminal is True
    assert device.stop_reason == reason
    assert EventLog.objects.filter(
        event_type="conveyor_auto_stop",
        payload__reason=reason,
    ).exists()


def test_new_leader_never_adopts_old_boot_and_fences_it(
    legacy_session_factory,
    monkeypatch,
):
    _, session, device = legacy_session_factory(
        status=AiCountingSession.ACTIVE,
        boot_id=OLD_BOOT_ID,
    )
    monkeypatch.setattr(
        ai,
        "legacy_status",
        lambda *_args, **_kwargs: pytest.fail("foreign session was polled"),
        raising=False,
    )

    stats = LegacyConveyorMonitor(boot_id=BOOT_ID).run_cycle()

    session.refresh_from_db()
    device.refresh_from_db()
    assert stats.claimed == 0
    assert stats.fenced == 1
    assert stats.polled == 0
    assert session.legacy_bridge_boot_id == OLD_BOOT_ID
    assert device.command_terminal is True
    assert device.stop_reason == "legacy_bridge_restarted"


def test_graceful_shutdown_fences_every_owned_session(legacy_session_factory):
    _, _, first = legacy_session_factory(camera="cam2", boot_id=BOOT_ID)
    _, _, second = legacy_session_factory(camera="cam3", boot_id=BOOT_ID)
    _, _, foreign = legacy_session_factory(camera="cam4", boot_id=OLD_BOOT_ID)

    stopped = LegacyConveyorMonitor(boot_id=BOOT_ID).shutdown()

    first.refresh_from_db()
    second.refresh_from_db()
    foreign.refresh_from_db()
    assert stopped == 2
    assert first.stop_reason == "legacy_bridge_shutdown"
    assert second.stop_reason == "legacy_bridge_shutdown"
    assert foreign.command_terminal is False


def test_heartbeat_round_trip_and_failure_states(tmp_path):
    path = tmp_path / "legacy-heartbeat"
    write_heartbeat(
        path,
        state="ok",
        boot_id=BOOT_ID,
        db_backend_pid=321,
        now=100.0,
        process_id=os.getpid(),
    )

    healthy, message = check_heartbeat(path, now=101.0)
    assert healthy is True
    assert "healthy" in message
    payload = json.loads(path.read_text())
    assert payload["boot_id"] == str(BOOT_ID)
    assert payload["db_backend_pid"] == 321

    assert check_heartbeat(path, now=106.0)[0] is False
    payload["state"] = "stopping"
    path.write_text(json.dumps(payload))
    assert check_heartbeat(path, now=101.0)[0] is False
    assert check_heartbeat(tmp_path / "missing", now=101.0)[0] is False


def test_heartbeat_rejects_malformed_and_dead_process(tmp_path, monkeypatch):
    malformed = tmp_path / "malformed"
    malformed.write_text("not-json")
    assert check_heartbeat(malformed)[0] is False

    path = tmp_path / "dead"
    write_heartbeat(
        path,
        state="ok",
        boot_id=BOOT_ID,
        db_backend_pid=321,
        process_id=987654,
    )

    def dead(_pid, _signal):
        raise ProcessLookupError

    monkeypatch.setattr(legacy_monitor.os, "kill", dead)
    assert check_heartbeat(path)[0] is False


def test_healthcheck_command_only_reads_heartbeat(tmp_path, monkeypatch):
    path = tmp_path / "command-heartbeat"
    write_heartbeat(
        path,
        state="ok",
        boot_id=BOOT_ID,
        db_backend_pid=321,
    )

    def must_not_acquire():
        pytest.fail("healthcheck attempted to acquire leadership")

    monkeypatch.setattr(AdvisoryLeader, "try_acquire", must_not_acquire)
    stdout = StringIO()
    call_command(
        "monitor_legacy_conveyors",
        "--healthcheck",
        heartbeat_file=str(path),
        stdout=stdout,
    )
    assert "healthy" in stdout.getvalue()

    path.write_text("{}")
    with pytest.raises(CommandError):
        call_command(
            "monitor_legacy_conveyors",
            "--healthcheck",
            heartbeat_file=str(path),
        )


class FakeCursor:
    def __init__(self, database):
        self.database = database
        self.row = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, _params=None):
        if "pg_try_advisory_lock" in sql:
            self.row = (self.database.lock_available, self.database.pid)
        elif "pg_advisory_unlock" in sql:
            self.database.unlocked = True
            self.row = (True,)
        elif "pg_backend_pid" in sql:
            self.row = (self.database.pid,)
        else:  # pragma: no cover - protects the deliberately tiny SQL surface.
            raise AssertionError(sql)

    def fetchone(self):
        return self.row


class FakeConnection:
    vendor = "postgresql"

    def __init__(self, *, lock_available=True, pid=123):
        self.lock_available = lock_available
        self.pid = pid
        self.connection = object()
        self.unlocked = False

    def ensure_connection(self):
        return None

    def cursor(self):
        return FakeCursor(self)


def test_advisory_leader_rejects_contention_and_pid_change(monkeypatch):
    busy = FakeConnection(lock_available=False)
    monkeypatch.setattr(legacy_monitor, "connection", busy)
    assert AdvisoryLeader.try_acquire() is None

    database = FakeConnection(pid=456)
    monkeypatch.setattr(legacy_monitor, "connection", database)
    leader = AdvisoryLeader.try_acquire()
    assert leader is not None
    leader.ensure_current()

    database.pid = 789
    with pytest.raises(LeadershipError):
        leader.ensure_current()
    leader.release()
    assert database.unlocked is False
