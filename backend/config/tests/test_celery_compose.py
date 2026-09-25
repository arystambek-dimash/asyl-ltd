import pytest
from celery_beat_healthcheck import PIDFILE

from config.tests.compose_files import read_compose, service_block


@pytest.mark.parametrize("compose_file", ["docker-compose.yml", "docker-compose.prod.yml"])
def test_orientation_has_its_own_worker_and_media(compose_file):
    from django.conf import settings
    compose = read_compose(compose_file)
    worker = service_block(compose, "celery-orientation")
    assert settings.CELERY_TASK_ROUTES["grain.export_orientation_samples"]["queue"] == "orientation"
    assert "--queues=orientation" in worker
    assert "--queues=payments" not in worker
    assert "mediadata:/app/media:ro" in worker
    assert "--concurrency=1" in worker
    assert "inspect ping --destination orientation@$$HOSTNAME" in worker
    assert "restart: unless-stopped" in worker


@pytest.mark.parametrize(
    "compose_file",
    ["docker-compose.yml", "docker-compose.prod.yml"],
)
def test_compose_has_one_serial_payments_worker_and_no_legacy_monitor(
    compose_file: str,
) -> None:
    compose = read_compose(compose_file)
    worker = service_block(compose, "celery-payments")

    assert compose.count("\n  celery-payments:\n") == 1
    assert "\n  payment-monitor:\n" not in compose
    assert "reconcile_apipay_invoices" not in compose
    assert "--queues=payments" in worker
    assert "--concurrency=1" in worker
    assert "--prefetch-multiplier=1" in worker
    assert "stop_grace_period: 10m" in worker
    assert "/app/apipay_monitor_healthcheck.py" in worker


@pytest.mark.parametrize(
    "compose_file",
    ["docker-compose.yml", "docker-compose.prod.yml"],
)
def test_compose_beat_uses_bounded_writable_state_and_pid_healthcheck(
    compose_file: str,
) -> None:
    compose = read_compose(compose_file)
    beat = service_block(compose, "celery-beat")

    assert compose.count("\n  celery-beat:\n") == 1
    assert "--schedule=/tmp/celerybeat/celerybeat-schedule" in beat
    assert f"--pidfile={PIDFILE}" in beat
    assert "/tmp/celerybeat:rw,noexec,nosuid,nodev,size=16m,mode=1777" in beat
    assert "/app/celery_beat_healthcheck.py" in beat
