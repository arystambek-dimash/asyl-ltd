from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


def _service_block(compose: str, service: str) -> str:
    marker = f"  {service}:\n"
    start = compose.index(marker)
    lines = compose[start:].splitlines(keepends=True)
    end = len(lines)
    for index, line in enumerate(lines[1:], start=1):
        if line.startswith("  ") and not line.startswith("    "):
            end = index
            break
    return "".join(lines[:end])


@pytest.mark.parametrize(
    ("compose_file", "local_profile"),
    [
        ("docker-compose.yml", True),
        ("docker-compose.prod.yml", False),
    ],
)
def test_passage_scale_monitor_is_a_single_health_checked_backend_process(
    compose_file: str,
    local_profile: bool,
) -> None:
    compose = (REPO_ROOT / compose_file).read_text(encoding="utf-8")
    monitor = _service_block(compose, "passage-scale-monitor")

    assert compose.count("\n  passage-scale-monitor:\n") == 1
    assert "APP_SERVICE: passage-scale-monitor" in monitor
    assert "entrypoint: []" in monitor
    assert 'command: ["python", "manage.py", "monitor_passage_scale"]' in monitor
    assert "init: true" in monitor
    assert "stop_grace_period: 60s" in monitor
    assert (
        "/tmp/passage-scale-monitor:rw,noexec,nosuid,nodev,size=1m,mode=1777" in monitor
    )
    assert "/app/passage_scale_monitor_healthcheck.py" in monitor
    assert "restart: unless-stopped" in monitor
    assert ('profiles: ["hardware"]' in monitor) is local_profile


@pytest.mark.parametrize(
    "compose_file", ["docker-compose.yml", "docker-compose.prod.yml"]
)
def test_passage_scale_monitor_environment_is_default_off(compose_file: str) -> None:
    compose = (REPO_ROOT / compose_file).read_text(encoding="utf-8")

    assert "VEHICLE_PLATE_AUTO_SCALE_ENABLED" in compose
    assert "${VEHICLE_PLATE_AUTO_SCALE_ENABLED:-0}" in compose
    assert "${VEHICLE_PLATE_AUTO_SCALE_POLL_SECONDS:-1}" in compose
    assert "${VEHICLE_PLATE_AUTO_SCALE_EMPTY_MAX_KG:-500}" in compose
    assert "${VEHICLE_PLATE_AUTO_SCALE_STABLE_CONFIRM_POLLS:-2}" in compose
    assert "${VEHICLE_PLATE_AUTO_SCALE_CLEAR_CONFIRM_POLLS:-3}" in compose
    assert "${VEHICLE_PLATE_AUTO_SCALE_STABLE_TOLERANCE_KG:-50}" in compose
    assert "${VEHICLE_PLATE_AUTO_SCALE_MAX_RECOGNITION_ATTEMPTS:-3}" in compose
    assert "${VEHICLE_PLATE_AUTO_SCALE_HEARTBEAT_MAX_AGE_SECONDS:-60}" in compose
