from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.management import call_command

from apps.cameras import ai, continuous
from apps.cameras.models import MonoblockCameraSettings

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    ("saved_camera", "effective_camera"),
    [("", None), ("cam8", "cam8")],
)
def test_wagon_role_404_uses_durable_assignment_without_remote_put(
    saved_camera,
    effective_camera,
):
    MonoblockCameraSettings.objects.create(wagon_number_camera_source=saved_camera)

    with (
        patch.object(
            ai,
            "wagon_number_status",
            side_effect=ai.AiError(404, "not found"),
        ),
        patch.object(ai, "configure_wagon_number") as configure,
    ):
        status = continuous.reconcile_wagon_number()

    assert status == {
        "camera": effective_camera,
        "source": "main",
        "stream": effective_camera,
        "assigned": effective_camera is not None,
        "mode": "wagon_number_24_7",
        "role_api_supported": False,
    }
    configure.assert_not_called()
    assert MonoblockCameraSettings.wagon_number_source() == saved_camera


@pytest.mark.parametrize(
    "error",
    [
        ai.AiError(401, "unauthorized"),
        ai.AiError(500, "boom"),
        ai.AiUnavailable("offline"),
    ],
)
def test_wagon_role_real_errors_are_not_hidden(error):
    MonoblockCameraSettings.objects.create(wagon_number_camera_source="cam8")

    with (
        patch.object(ai, "wagon_number_status", side_effect=error),
        patch.object(ai, "configure_wagon_number") as configure,
        pytest.raises((ai.AiError, ai.AiUnavailable)) as caught,
    ):
        continuous.reconcile_wagon_number()

    assert caught.value is error
    configure.assert_not_called()


def test_wagon_role_supported_mismatch_is_configured_remotely():
    MonoblockCameraSettings.objects.create(wagon_number_camera_source="cam8")
    configured = {
        "camera": "cam8",
        "source": "main",
        "stream": "cam8",
        "assigned": True,
        "mode": "wagon_number_24_7",
    }

    with (
        patch.object(
            ai,
            "wagon_number_status",
            return_value={"camera": None, "source": "main"},
        ),
        patch.object(
            ai,
            "configure_wagon_number",
            return_value=configured,
        ) as configure,
    ):
        status = continuous.reconcile_wagon_number()

    assert status == configured
    configure.assert_called_once_with("cam8", "main")


def test_monitor_still_polls_wagon_plate_when_role_api_is_unsupported(monkeypatch):
    MonoblockCameraSettings.objects.create(wagon_number_camera_source="cam8")
    monkeypatch.setattr(ai, "AI_KEY", "test-key")
    health_state = SimpleNamespace(
        status="healthy",
        observed_status="healthy",
        online_count=8,
        expected_count=8,
        failure_streak=0,
        recovery_streak=1,
    )
    stdout = StringIO()

    with (
        patch(
            "apps.cameras.management.commands.monitor_cameras.health.monitor_once",
            return_value=health_state,
        ),
        patch.object(continuous, "reconcile", return_value={"cameras": ["cam2"]}),
        patch.object(
            ai,
            "wagon_number_status",
            side_effect=ai.AiError(404, "not found"),
        ),
        patch.object(ai, "configure_wagon_number") as configure,
        patch.object(
            continuous,
            "poll_wagon_plate",
            return_value={"seen": False},
        ) as poll,
    ):
        call_command("monitor_cameras", "--once", stdout=stdout)

    configure.assert_not_called()
    poll.assert_called_once_with()
    assert "camera-ai wagon-number=cam8" in stdout.getvalue()


def test_monitor_keeps_wagon_polling_independent_from_control_plane_errors(
    monkeypatch,
    caplog,
):
    monkeypatch.setattr(ai, "AI_KEY", "test-key")
    health_state = SimpleNamespace(
        status="healthy",
        observed_status="healthy",
        online_count=8,
        expected_count=8,
        failure_streak=0,
        recovery_streak=1,
    )

    with (
        patch(
            "apps.cameras.management.commands.monitor_cameras.health.monitor_once",
            return_value=health_state,
        ),
        patch.object(continuous, "reconcile", side_effect=RuntimeError("counter")),
        patch.object(
            continuous,
            "reconcile_wagon_number",
            side_effect=RuntimeError("role"),
        ),
        patch.object(
            continuous,
            "poll_wagon_plate",
            return_value={"seen": False},
        ) as poll,
    ):
        call_command("monitor_cameras", "--once", stdout=StringIO())

    poll.assert_called_once_with()
    assert "Always-on AI reconciliation failed" in caplog.text
    assert "Wagon-number camera reconciliation failed" in caplog.text
