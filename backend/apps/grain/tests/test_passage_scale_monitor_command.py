import json
from io import StringIO
from threading import Event
from unittest.mock import patch

import pytest
from apps.grain import outbox_importer
from django.core.management import call_command

pytestmark = pytest.mark.django_db(transaction=True)


def test_once_without_collector_writes_healthy_disabled_heartbeat(
    settings, tmp_path, monkeypatch
):
    heartbeat = tmp_path / "heartbeat.json"
    settings.VEHICLE_PLATE_AUTO_SCALE_HEARTBEAT_FILE = str(heartbeat)
    monkeypatch.setenv("WEIGHBRIDGE_OUTBOX_DIR", str(tmp_path / "outbox"))

    with patch.object(outbox_importer, "poll_once") as replay:
        call_command("monitor_passage_scale", "--once", stdout=StringIO())

    replay.assert_not_called()
    assert json.loads(heartbeat.read_text(encoding="utf-8"))["status"] == "disabled"


def test_once_dependency_state_writes_degraded_heartbeat(settings, tmp_path):
    heartbeat = tmp_path / "heartbeat.json"
    settings.VEHICLE_PLATE_AUTO_SCALE_HEARTBEAT_FILE = str(heartbeat)

    with (
        patch.object(outbox_importer, "enabled", return_value=True),
        patch.object(outbox_importer, "poll_once", return_value="unavailable"),
    ):
        call_command("monitor_passage_scale", "--once", stdout=StringIO())

    assert json.loads(heartbeat.read_text(encoding="utf-8"))["status"] == "degraded"


def test_initial_heartbeat_exists_before_first_monitor_iteration(settings, tmp_path):
    heartbeat = tmp_path / "heartbeat.json"
    settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED = True
    settings.VEHICLE_PLATE_AUTO_SCALE_HEARTBEAT_FILE = str(heartbeat)

    def inspect_initial_heartbeat():
        assert json.loads(heartbeat.read_text(encoding="utf-8"))["status"] == "running"
        return "idle"

    with (
        patch.object(outbox_importer, "enabled", return_value=True),
        patch.object(outbox_importer, "poll_once", side_effect=inspect_initial_heartbeat),
    ):
        call_command("monitor_passage_scale", "--once", stdout=StringIO())


def test_command_rejects_unsafe_poll_interval():
    with pytest.raises(ValueError, match="between 0.5 and 10"):
        call_command(
            "monitor_passage_scale",
            "--once",
            "--interval",
            "0.1",
            stdout=StringIO(),
        )


def test_once_tick_imports_wagon_stops_when_enabled(settings, monkeypatch, tmp_path):
    from apps.grain import wagon_arch
    settings.WAGON_ARCH_AUTOMATION_ENABLED = True
    settings.VEHICLE_PLATE_AUTO_SCALE_HEARTBEAT_FILE = str(tmp_path / "heartbeat.json")
    (tmp_path / "wagon").mkdir()
    monkeypatch.setenv("WEIGHBRIDGE_WAGON_OUTBOX_DIR", str(tmp_path / "wagon"))
    with (
        patch.object(wagon_arch, "poll_once", return_value={"imported": 0}) as poll,
        patch.object(outbox_importer, "enabled", return_value=False),
    ):
        call_command("monitor_passage_scale", "--once", stdout=StringIO())
    poll.assert_called_once()


def test_daemon_lanes_do_not_overlap(settings, tmp_path):
    from apps.grain import wagon_arch
    from apps.grain import weighing_identity, weighing_photos

    settings.VEHICLE_PLATE_AUTO_SCALE_HEARTBEAT_FILE = str(tmp_path / "heartbeat.json")
    release = Event()
    polls = []

    def slow_photos():
        assert release.wait(3)
        return 0

    def poll():
        polls.append(len(polls))
        if len(polls) == 3:
            release.set()
            raise KeyboardInterrupt
        return "idle"

    try:
        with (
            patch.object(outbox_importer, "enabled", return_value=True),
            patch.object(outbox_importer, "poll_once", side_effect=poll),
            patch.object(weighing_photos, "retry_due_photos", side_effect=slow_photos) as photos,
            patch.object(weighing_identity, "process_once") as identity,
            patch.object(wagon_arch, "enabled", return_value=False),
            patch.object(wagon_arch, "poll_once") as wagon,
            pytest.raises(KeyboardInterrupt),
        ):
            call_command("monitor_passage_scale", "--interval", "0.5", stdout=StringIO())
    finally:
        release.set()
    wagon.assert_not_called()
    # Занятая дорожка не запускается повторно, идентичность — не чаще раза в 5 с.
    photos.assert_called_once()
    identity.assert_called_once()
