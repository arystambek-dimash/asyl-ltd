import json
import signal
import threading
from io import StringIO
from unittest.mock import Mock, patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import OperationalError

from apps.cameras.management.commands import monitor_shipping_transports as monitor


@pytest.fixture
def heartbeat_path(tmp_path, monkeypatch):
    path = tmp_path / "monitor" / "heartbeat.json"
    monkeypatch.setenv("SHIPPING_TRANSPORT_HEARTBEAT_FILE", str(path))
    return path


def test_initial_heartbeat_is_published_before_first_poll(heartbeat_path):
    def inspect_initial_heartbeat():
        assert json.loads(heartbeat_path.read_text())["status"] == "running"
        assert list(heartbeat_path.parent.iterdir()) == [heartbeat_path]
        return {"processed": 0, "errors": 0}

    with (
        patch.object(monitor, "close_old_connections"),
        patch.object(
            monitor.shipping_automation,
            "poll_once",
            side_effect=inspect_initial_heartbeat,
        ) as poll,
    ):
        call_command("monitor_shipping_transports", "--once", stdout=StringIO())

    poll.assert_called_once_with()


def test_camera_errors_leave_live_degraded_heartbeat(heartbeat_path):
    with (
        patch.object(monitor, "close_old_connections"),
        patch.object(
            monitor.shipping_automation,
            "poll_once",
            return_value={"processed": 2, "errors": 1},
        ),
    ):
        call_command("monitor_shipping_transports", "--once", stdout=StringIO())

    assert json.loads(heartbeat_path.read_text())["status"] == "degraded"


def test_once_dependency_failure_is_reported_and_not_hidden(heartbeat_path):
    with (
        patch.object(monitor, "close_old_connections"),
        patch.object(
            monitor.shipping_automation,
            "poll_once",
            side_effect=OperationalError("database unavailable"),
        ),
        pytest.raises(OperationalError, match="database unavailable"),
    ):
        call_command("monitor_shipping_transports", "--once", stdout=StringIO())

    assert json.loads(heartbeat_path.read_text())["status"] == "degraded"


def test_loop_recovers_from_dependency_failure_and_stops_after_active_poll(
    heartbeat_path, monkeypatch
):
    monkeypatch.setenv("SHIPPING_TRANSPORT_POLL_SECONDS", "4")
    stopped = threading.Event()
    handlers = {}
    calls = 0
    waits = []

    def handle_signal(signum, handler):
        previous = handlers.get(signum, signal.SIG_DFL)
        handlers[signum] = handler
        return previous

    def tick():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OperationalError("database unavailable")
        assert json.loads(heartbeat_path.read_text())["status"] == "degraded"
        handlers[signal.SIGTERM](signal.SIGTERM, None)
        return {"processed": 1, "errors": 0}

    scheduler = Mock()
    scheduler.tick.side_effect = tick
    with (
        patch.object(monitor, "close_old_connections"),
        patch.object(monitor.threading, "Event", return_value=stopped),
        patch.object(stopped, "wait", side_effect=waits.append),
        patch.object(monitor.signal, "signal", side_effect=handle_signal),
        patch.object(monitor.time, "monotonic", side_effect=[0, 1, 2, 3]),
        patch.object(
            monitor, "ShippingTransportScheduler", return_value=scheduler
        ) as scheduler_class,
        patch.object(monitor.shipping_automation, "poll_once") as synchronous_poll,
    ):
        call_command("monitor_shipping_transports", stdout=StringIO())

    assert calls == 2
    scheduler_class.assert_called_once_with(4)
    scheduler.close.assert_called_once_with()
    synchronous_poll.assert_not_called()
    assert waits == [3, 3]
    assert handlers == {signal.SIGTERM: signal.SIG_DFL, signal.SIGINT: signal.SIG_DFL}
    assert json.loads(heartbeat_path.read_text())["status"] == "running"


def test_programming_error_exits_for_supervisor_restart(heartbeat_path):
    with (
        patch.object(monitor, "close_old_connections"),
        patch.object(
            monitor.shipping_automation, "poll_once", side_effect=ValueError("bug")
        ),
        pytest.raises(ValueError, match="bug"),
    ):
        call_command("monitor_shipping_transports", "--once", stdout=StringIO())


@pytest.mark.parametrize("interval", ["0", "0.1", "61", "nan", "inf"])
def test_command_rejects_invalid_poll_interval(interval):
    with pytest.raises(CommandError, match="between 0.5 and 60"):
        call_command("monitor_shipping_transports", "--once", "--interval", interval)
