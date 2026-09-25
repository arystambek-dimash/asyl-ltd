"""camera-monitor и ai-stock-monitor: переподключение к базе и heartbeat."""

import json
import signal
import threading
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.db import DatabaseError, connection

from apps.cameras import ai, health, production
from apps.cameras.management.commands import post_always_on_stock

import heartbeat_healthcheck


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(
    threading.current_thread() is not threading.main_thread(),
    reason="SIGTERM handler is installed only in the main thread",
)
def test_stock_poster_reconnects_after_database_restart(settings, tmp_path):
    heartbeat = tmp_path / "ai-stock-monitor" / "heartbeat.json"
    settings.AI_STOCK_MONITOR_HEARTBEAT_FILE = str(heartbeat)
    rounds = []

    def post_due_stock():
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                if not rounds:
                    # PostgreSQL перезапущен посреди круга, под открытым соединением.
                    connection.connection.close()
                    cursor.execute("SELECT 1")
        except DatabaseError:
            rounds.append("failed")
            if len(rounds) > 2:
                # Без переподключения круг падает на мёртвом соединении вечно.
                raise SystemExit("no reconnect")
            raise
        rounds.append("ok")
        signal.raise_signal(signal.SIGTERM)
        return []

    connection.ensure_connection()
    with (
        patch.object(production, "post_due_stock", side_effect=post_due_stock),
        # Следующий круг — сразу, без минутной паузы.
        patch.object(post_always_on_stock, "every", lambda _interval: lambda *_: 0),
    ):
        call_command("post_always_on_stock", stdout=StringIO())

    assert rounds == ["failed", "ok"]
    assert json.loads(heartbeat.read_text(encoding="utf-8"))["status"] == "running"
    assert heartbeat_healthcheck.main([str(heartbeat), "300"]) == 0


def test_stock_poster_once_failure_is_reported_with_degraded_heartbeat(settings, tmp_path):
    heartbeat = tmp_path / "heartbeat.json"
    settings.AI_STOCK_MONITOR_HEARTBEAT_FILE = str(heartbeat)

    with (
        patch("apps.common.daemon.close_old_connections"),
        patch.object(production, "post_due_stock", side_effect=RuntimeError("boom")),
        pytest.raises(RuntimeError, match="boom"),
    ):
        call_command("post_always_on_stock", "--once", stdout=StringIO())

    assert json.loads(heartbeat.read_text(encoding="utf-8"))["status"] == "degraded"


def test_camera_monitor_once_writes_live_heartbeat(settings, tmp_path, monkeypatch):
    heartbeat = tmp_path / "camera-monitor" / "heartbeat.json"
    settings.CAMERA_MONITOR_HEARTBEAT_FILE = str(heartbeat)
    monkeypatch.setattr(ai, "enabled", lambda: False)
    state = SimpleNamespace(
        status="healthy",
        observed_status="healthy",
        online_count=8,
        expected_count=8,
        failure_streak=0,
        recovery_streak=1,
    )

    with (
        patch("apps.common.daemon.close_old_connections") as close,
        patch.object(health, "monitor_once", return_value=state),
    ):
        call_command("monitor_cameras", "--once", stdout=StringIO())

    # Соединение, разорванное рестартом PostgreSQL, закрывается до и после круга.
    assert close.call_count >= 2
    assert json.loads(heartbeat.read_text(encoding="utf-8"))["status"] == "running"
    assert heartbeat_healthcheck.main([str(heartbeat), "300"]) == 0
