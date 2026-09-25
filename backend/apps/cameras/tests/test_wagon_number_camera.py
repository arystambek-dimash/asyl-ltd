from unittest.mock import patch

import pytest

from apps.cameras import ai, continuous
from apps.cameras.models import MonoblockCameraSettings

pytestmark = pytest.mark.django_db


def test_monitor_polls_wagon_plate_by_crm_assignment_without_camera_pc_role(
    run_camera_monitor_once,
):
    # На ПК камер нет /camera-roles/wagon-number: назначение живёт только в
    # CRM, монитор не спрашивает о нём сервис каждые 30 секунд.
    MonoblockCameraSettings.objects.create(wagon_number_camera_source="cam8")

    with (
        patch.object(continuous, "reconcile", return_value={"camera_sources": ["cam2"]}),
        patch.object(ai, "_call") as call,
        patch.object(
            continuous,
            "poll_wagon_plate",
            return_value={"seen": False},
        ) as poll,
    ):
        stdout = run_camera_monitor_once()

    call.assert_not_called()
    poll.assert_called_once_with()
    assert "camera-ai always-on=cam2" in stdout


def test_monitor_keeps_wagon_polling_independent_from_control_plane_errors(
    run_camera_monitor_once,
    caplog,
):
    with (
        patch.object(continuous, "reconcile", side_effect=RuntimeError("counter")),
        patch.object(
            continuous,
            "poll_wagon_plate",
            return_value={"seen": False},
        ) as poll,
    ):
        run_camera_monitor_once()

    poll.assert_called_once_with()
    assert "Always-on AI reconciliation failed" in caplog.text


def test_monitor_skips_wagon_plate_poll_while_the_arch_automation_runs(
    settings,
    run_camera_monitor_once,
):
    # Арка — датчик прибытия вагона: опрос таблички выключен, остальная
    # сверка идёт. Без флага опрос проверяют тесты выше.
    settings.WAGON_ARCH_AUTOMATION_ENABLED = True

    with (
        patch.object(
            continuous,
            "reconcile",
            return_value={"camera_sources": ["cam2"]},
        ) as reconcile,
        patch.object(continuous, "poll_wagon_plate") as poll,
    ):
        run_camera_monitor_once()

    poll.assert_not_called()
    reconcile.assert_called_once()
