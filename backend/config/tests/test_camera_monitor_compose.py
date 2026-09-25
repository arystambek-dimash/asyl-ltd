"""camera-monitor и ai-stock-monitor в проде: healthcheck по heartbeat цикла."""
import pytest
from django.conf import settings

from config.tests.compose_files import read_compose, service_block

COMPOSE = read_compose("docker-compose.prod.yml")


@pytest.mark.parametrize(
    ("service", "heartbeat_setting"),
    [
        ("camera-monitor", "CAMERA_MONITOR_HEARTBEAT_FILE"),
        ("ai-stock-monitor", "AI_STOCK_MONITOR_HEARTBEAT_FILE"),
    ],
)
def test_monitor_loop_is_health_checked_by_its_heartbeat(service, heartbeat_setting):
    block = service_block(COMPOSE, service)
    heartbeat = getattr(settings, heartbeat_setting)
    directory = heartbeat.rsplit("/", 1)[0]

    assert (
        f'test: ["CMD", "python", "/app/heartbeat_healthcheck.py", "{heartbeat}", "300"]'
        in block
    )
    assert f"- {directory}:rw,noexec,nosuid,nodev,size=1m,mode=1777" in block
    assert "restart: unless-stopped" in block
