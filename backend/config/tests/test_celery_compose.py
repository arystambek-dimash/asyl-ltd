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
    "compose_file",
    ["docker-compose.yml", "docker-compose.prod.yml"],
)
def test_compose_has_one_serial_payments_worker_and_no_legacy_monitor(
    compose_file: str,
) -> None:
    compose = (REPO_ROOT / compose_file).read_text(encoding="utf-8")
    worker = _service_block(compose, "celery-payments")

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
    compose = (REPO_ROOT / compose_file).read_text(encoding="utf-8")
    beat = _service_block(compose, "celery-beat")

    assert compose.count("\n  celery-beat:\n") == 1
    assert "--schedule=/tmp/celerybeat/celerybeat-schedule" in beat
    assert "--pidfile=/tmp/celerybeat/celerybeat.pid" in beat
    assert "/tmp/celerybeat:rw,noexec,nosuid,nodev,size=16m,mode=1777" in beat
    assert "/app/celery_beat_healthcheck.py" in beat
