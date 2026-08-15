import os
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
LEGACY_DEFAULTS = {
    "CONVEYOR_LEGACY_BRIDGE_POLL_MS": "250",
    "CONVEYOR_LEGACY_BRIDGE_REQUEST_TIMEOUT_MS": "350",
    "CONVEYOR_LEGACY_BRIDGE_STALE_MS": "750",
    "CONVEYOR_LEGACY_BRIDGE_DEVICE_SYNC_MS": "250",
    "CONVEYOR_LEGACY_BRIDGE_DEVICE_LEASE_MS": "750",
}


def _import_base_settings(**overrides: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.update(LEGACY_DEFAULTS)
    environment.update(overrides)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-c", "import config._settings.base"],
        cwd=BACKEND_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_legacy_request_budget_may_equal_stale_window():
    result = _import_base_settings(
        CONVEYOR_LEGACY_BRIDGE_POLL_MS="400",
        CONVEYOR_LEGACY_BRIDGE_REQUEST_TIMEOUT_MS="350",
    )

    assert result.returncode == 0, result.stderr


def test_legacy_request_budget_cannot_exceed_stale_window():
    result = _import_base_settings(
        CONVEYOR_LEGACY_BRIDGE_POLL_MS="400",
        CONVEYOR_LEGACY_BRIDGE_REQUEST_TIMEOUT_MS="400",
    )

    assert result.returncode != 0
    assert (
        "request timeout plus poll interval must be at most" in result.stderr
    )
