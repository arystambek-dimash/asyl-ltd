from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


def _service_block(compose: str, service: str) -> str:
    lines = compose[compose.index(f"  {service}:\n") :].splitlines(keepends=True)
    end = len(lines)
    for index, line in enumerate(lines[1:], start=1):
        if line.startswith("  ") and not line.startswith("    "):
            end = index
            break
    return "".join(lines[:end])


@pytest.mark.parametrize(
    ("compose_file", "local_profile"),
    [("docker-compose.yml", True), ("docker-compose.prod.yml", False)],
)
def test_shipping_transport_worker_is_independent_and_health_checked(
    compose_file, local_profile
):
    compose = (REPO_ROOT / compose_file).read_text()
    monitor = _service_block(compose, "shipping-transport-monitor")

    assert compose.count("\n  shipping-transport-monitor:\n") == 1
    assert "<<: *backend-environment" in monitor
    assert "APP_SERVICE: shipping-transport-monitor" in monitor
    assert "entrypoint: []" in monitor
    assert 'command: ["python", "manage.py", "monitor_shipping_sessions"]' in monitor
    assert "init: true" in monitor
    assert "stop_grace_period: 180s" in monitor
    assert "/tmp/shipping-transport-monitor:rw,noexec,nosuid,nodev,size=1m" in monitor
    assert "/app/shipping_transport_monitor_healthcheck.py" in monitor
    assert "restart: unless-stopped" in monitor
    # The worker writes recognition evidence; backend serves the same files.
    assert "volumes:\n      - mediadata:/app/media" in monitor
    assert "- mediadata:/app/media" in _service_block(compose, "backend")
    assert "ports:" not in monitor
    assert ('profiles: ["hardware"]' in monitor) is local_profile
    assert "${SHIPPING_TRANSPORT_POLL_SECONDS:-2}" in compose
    assert "${SHIPPING_TRANSPORT_HEARTBEAT_MAX_AGE_SECONDS:-180}" in compose
    if not local_profile:
        assert "backend:\n        condition: service_healthy" in monitor
        assert "go2rtc:\n        condition: service_healthy" in monitor
        assert "image: ${BACKEND_IMAGE_REF:-" in monitor
        assert "logging: *default-logging" in monitor
