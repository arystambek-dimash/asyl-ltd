import json
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

import pytest
from apps.cameras import ai as camera_ai
from apps.grain import passage_scale_automation as automation
from apps.grain import scale
from apps.grain.management.commands.monitor_passage_scale import _write_heartbeat
from apps.grain.models import AutomaticPassageCapture, PassageScaleAutomationState
from django.core.management import call_command

pytestmark = pytest.mark.django_db(transaction=True)


def test_atomic_heartbeat_uses_liveness_contract(tmp_path):
    heartbeat = tmp_path / "monitor" / "heartbeat.json"

    _write_heartbeat(str(heartbeat), "running", now=1_725_350_400.5)

    assert json.loads(heartbeat.read_text(encoding="utf-8")) == {
        "status": "running",
        "updated_at": 1_725_350_400.5,
    }
    assert list(heartbeat.parent.iterdir()) == [heartbeat]


def test_once_disabled_writes_healthy_disabled_heartbeat(settings, tmp_path):
    heartbeat = tmp_path / "heartbeat.json"
    settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED = False
    settings.VEHICLE_PLATE_AUTO_SCALE_HEARTBEAT_FILE = str(heartbeat)

    call_command("monitor_passage_scale", "--once", stdout=StringIO())

    assert json.loads(heartbeat.read_text(encoding="utf-8"))["status"] == "disabled"


def test_once_dependency_state_writes_degraded_heartbeat(settings, tmp_path):
    heartbeat = tmp_path / "heartbeat.json"
    settings.VEHICLE_PLATE_AUTO_SCALE_HEARTBEAT_FILE = str(heartbeat)
    result = automation.MonitorIteration(state="unavailable")

    with patch.object(automation, "monitor_once", return_value=result):
        call_command("monitor_passage_scale", "--once", stdout=StringIO())

    assert json.loads(heartbeat.read_text(encoding="utf-8"))["status"] == "degraded"


def test_initial_heartbeat_exists_before_first_monitor_iteration(settings, tmp_path):
    heartbeat = tmp_path / "heartbeat.json"
    settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED = True
    settings.VEHICLE_PLATE_AUTO_SCALE_HEARTBEAT_FILE = str(heartbeat)

    def inspect_initial_heartbeat():
        assert json.loads(heartbeat.read_text(encoding="utf-8"))["status"] == "running"
        return automation.MonitorIteration(state="idle")

    with patch.object(
        automation,
        "monitor_once",
        side_effect=inspect_initial_heartbeat,
    ):
        call_command("monitor_passage_scale", "--once", stdout=StringIO())


@pytest.mark.parametrize(
    ("phase", "clear_streak"),
    [
        (PassageScaleAutomationState.UNARMED, 1),
        (PassageScaleAutomationState.ARMED, 0),
        (PassageScaleAutomationState.STABILIZING, 0),
    ],
)
def test_process_start_requires_fresh_clear_before_occupied_capture(
    settings,
    tmp_path,
    phase,
    clear_streak,
):
    heartbeat = tmp_path / "heartbeat.json"
    settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED = True
    settings.VEHICLE_PLATE_AUTO_SCALE_HEARTBEAT_FILE = str(heartbeat)
    state, _created = PassageScaleAutomationState.objects.update_or_create(
        scale_number=scale.TRUCK_SCALE_KEY,
        defaults={
            "phase": phase,
            "clear_streak": clear_streak,
            "stable_streak": (
                1 if phase == PassageScaleAutomationState.STABILIZING else 0
            ),
            "candidate_weight_kg": (
                Decimal(12000)
                if phase == PassageScaleAutomationState.STABILIZING
                else None
            ),
        },
    )
    occupied = scale.ScaleObservation(
        state="ready",
        weight_kg=Decimal(12000),
        connected=True,
        stable=True,
        stale=False,
        age_seconds=Decimal("0.2"),
        updated_at="2026-09-03T07:30:00Z",
    )

    with (
        patch.object(
            scale,
            "read_truck_scale_observation",
            return_value=occupied,
        ),
        patch.object(scale, "read_truck_scale") as strict_read,
        patch.object(camera_ai, "recognize_vehicle_from_camera") as recognize,
    ):
        call_command("monitor_passage_scale", "--once", stdout=StringIO())

    state.refresh_from_db()
    assert state.phase == PassageScaleAutomationState.UNARMED
    assert state.clear_streak == 0
    assert state.stable_streak == 0
    assert state.candidate_weight_kg is None
    assert not AutomaticPassageCapture.objects.exists()
    strict_read.assert_not_called()
    recognize.assert_not_called()


def test_command_rejects_unsafe_poll_interval():
    with pytest.raises(ValueError, match="between 0.5 and 10"):
        call_command(
            "monitor_passage_scale",
            "--once",
            "--interval",
            "0.1",
            stdout=StringIO(),
        )
