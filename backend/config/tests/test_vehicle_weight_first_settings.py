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
        "VEHICLE_PLATE_WEIGHT_FIRST_ENABLED",
        "VEHICLE_PLATE_WEIGHT_FIRST_CAMERA",
        "VEHICLE_PLATE_WEIGHT_FIRST_SOURCE",
        "AI_SERVICE_URL",
        "AI_SERVICE_API_KEY",
    ):
        environment.pop(name, None)
    environment.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTEST_RUNNING": "1",
            "VEHICLE_PLATE_AUTO_EXPORT_ENABLED": "0",
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
