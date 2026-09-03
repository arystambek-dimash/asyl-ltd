import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _import_settings(**overrides: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    for name in (
        "SENTRY_BACKEND_DSN",
        "VEHICLE_PLATE_AUTO_EXPORT_ENABLED",
        "VEHICLE_PLATE_AUTO_SCALE_ENABLED",
        "VEHICLE_PLATE_AUTO_SCALE_POLL_SECONDS",
        "VEHICLE_PLATE_AUTO_SCALE_EMPTY_MAX_KG",
        "VEHICLE_PLATE_AUTO_SCALE_STABLE_CONFIRM_POLLS",
        "VEHICLE_PLATE_AUTO_SCALE_CLEAR_CONFIRM_POLLS",
        "VEHICLE_PLATE_AUTO_SCALE_STABLE_TOLERANCE_KG",
        "VEHICLE_PLATE_AUTO_SCALE_MAX_RECOGNITION_ATTEMPTS",
        "VEHICLE_PLATE_AUTO_SCALE_HEARTBEAT_FILE",
        "VEHICLE_PLATE_AUTO_SCALE_HEARTBEAT_MAX_AGE_SECONDS",
        "VEHICLE_PLATE_WEIGHT_FIRST_ENABLED",
        "VEHICLE_PLATE_WEIGHT_FIRST_CAMERA",
        "VEHICLE_PLATE_WEIGHT_FIRST_SOURCE",
        "VEHICLE_PLATE_WEIGHT_FIRST_TIMEOUT_SECONDS",
        "AI_SERVICE_URL",
        "AI_SERVICE_API_KEY",
        "TRUCK_SCALE_API_URL",
        "TRUCK_SCALE_TIMEOUT_SECONDS",
        "TRUCK_SCALE_PREVIEW_TIMEOUT_SECONDS",
    ):
        environment.pop(name, None)
    environment.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTEST_RUNNING": "1",
            "VEHICLE_PLATE_AUTO_EXPORT_ENABLED": "0",
            "VEHICLE_PLATE_AUTO_SCALE_ENABLED": "0",
            "VEHICLE_PLATE_WEIGHT_FIRST_ENABLED": "1",
            "VEHICLE_PLATE_WEIGHT_FIRST_CAMERA": "cam1",
            "VEHICLE_PLATE_WEIGHT_FIRST_SOURCE": "main",
            "AI_SERVICE_URL": "http://camera-pc.internal:8890",
            "AI_SERVICE_API_KEY": "k" * 32,
            **overrides,
        }
    )
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from config._settings import base; print(base.AI_SERVICE_URL)",
        ],
        cwd=BACKEND_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("camera", ["cam1", "cam32"])
def test_weight_first_settings_accept_complete_camera_service_contract(camera):
    result = _import_settings(VEHICLE_PLATE_WEIGHT_FIRST_CAMERA=camera)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "http://camera-pc.internal:8890"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"AI_SERVICE_API_KEY": ""}, "AI_SERVICE_API_KEY"),
        ({"AI_SERVICE_API_KEY": "short"}, "AI_SERVICE_API_KEY"),
        ({"AI_SERVICE_URL": "camera-pc.internal:8890"}, "AI_SERVICE_URL"),
        ({"AI_SERVICE_URL": "ftp://camera-pc.internal"}, "AI_SERVICE_URL"),
        ({"AI_SERVICE_URL": "http://:8890"}, "AI_SERVICE_URL"),
        ({"AI_SERVICE_URL": "http://camera-pc:bad"}, "AI_SERVICE_URL"),
        ({"AI_SERVICE_URL": "http://camera-pc.internal/base"}, "AI_SERVICE_URL"),
        (
            {"VEHICLE_PLATE_WEIGHT_FIRST_CAMERA": "cam" + "1" * 30},
            "VEHICLE_PLATE_WEIGHT_FIRST_CAMERA",
        ),
        (
            {"VEHICLE_PLATE_WEIGHT_FIRST_CAMERA": "cam33"},
            "VEHICLE_PLATE_WEIGHT_FIRST_CAMERA",
        ),
        (
            {"VEHICLE_PLATE_WEIGHT_FIRST_SOURCE": "preview"},
            "VEHICLE_PLATE_WEIGHT_FIRST_SOURCE",
        ),
    ],
)
def test_weight_first_settings_fail_before_physical_scale_io(overrides, message):
    result = _import_settings(**overrides)

    assert result.returncode != 0
    assert message in result.stderr


def test_auto_scale_accepts_complete_scale_and_camera_contract():
    result = _import_settings(
        VEHICLE_PLATE_WEIGHT_FIRST_ENABLED="0",
        VEHICLE_PLATE_AUTO_SCALE_ENABLED="1",
        TRUCK_SCALE_API_URL="http://scale-pc.internal:8000/api/v1/weight",
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "truck_scale_url",
    ["", "scale-pc.internal/weight", "ftp://scale-pc/weight", "http://:8000"],
)
def test_auto_scale_rejects_missing_or_invalid_scale_url(truck_scale_url):
    result = _import_settings(
        VEHICLE_PLATE_WEIGHT_FIRST_ENABLED="0",
        VEHICLE_PLATE_AUTO_SCALE_ENABLED="1",
        TRUCK_SCALE_API_URL=truck_scale_url,
    )

    assert result.returncode != 0
    assert "TRUCK_SCALE_API_URL" in result.stderr


def test_auto_scale_and_legacy_camera_first_are_mutually_exclusive():
    result = _import_settings(
        VEHICLE_PLATE_WEIGHT_FIRST_ENABLED="0",
        VEHICLE_PLATE_AUTO_SCALE_ENABLED="1",
        VEHICLE_PLATE_AUTO_EXPORT_ENABLED="1",
        TRUCK_SCALE_API_URL="http://scale-pc.internal:8000/api/v1/weight",
    )

    assert result.returncode != 0
    assert "cannot both be enabled" in result.stderr


def test_auto_scale_rejects_heartbeat_shorter_than_a_valid_iteration():
    result = _import_settings(
        VEHICLE_PLATE_WEIGHT_FIRST_ENABLED="0",
        VEHICLE_PLATE_AUTO_SCALE_ENABLED="1",
        VEHICLE_PLATE_AUTO_SCALE_POLL_SECONDS="10",
        VEHICLE_PLATE_AUTO_SCALE_HEARTBEAT_MAX_AGE_SECONDS="5",
        VEHICLE_PLATE_WEIGHT_FIRST_TIMEOUT_SECONDS="30",
        TRUCK_SCALE_API_URL="http://scale-pc.internal:8000/api/v1/weight",
    )

    assert result.returncode != 0
    assert "VEHICLE_PLATE_AUTO_SCALE_HEARTBEAT_MAX_AGE_SECONDS" in result.stderr


def test_disabled_auto_scale_rejects_heartbeat_shorter_than_poll_cadence():
    result = _import_settings(
        VEHICLE_PLATE_WEIGHT_FIRST_ENABLED="0",
        VEHICLE_PLATE_AUTO_SCALE_ENABLED="0",
        VEHICLE_PLATE_AUTO_SCALE_POLL_SECONDS="10",
        VEHICLE_PLATE_AUTO_SCALE_HEARTBEAT_MAX_AGE_SECONDS="5",
    )

    assert result.returncode != 0
    assert "VEHICLE_PLATE_AUTO_SCALE_HEARTBEAT_MAX_AGE_SECONDS" in result.stderr
    assert "poll cadence" in result.stderr


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("VEHICLE_PLATE_AUTO_SCALE_POLL_SECONDS", "0.1"),
        ("VEHICLE_PLATE_AUTO_SCALE_STABLE_CONFIRM_POLLS", "0"),
        ("VEHICLE_PLATE_AUTO_SCALE_CLEAR_CONFIRM_POLLS", "1"),
        ("VEHICLE_PLATE_AUTO_SCALE_MAX_RECOGNITION_ATTEMPTS", "11"),
    ],
)
def test_auto_scale_tuning_is_bounded(name, value):
    result = _import_settings(**{name: value})

    assert result.returncode != 0
    assert name in result.stderr
