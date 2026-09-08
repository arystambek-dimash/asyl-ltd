import json

import pytest
import shipping_transport_monitor_healthcheck as healthcheck


@pytest.mark.parametrize("status", ["running", "degraded"])
def test_recent_live_heartbeat_is_healthy(tmp_path, status):
    path = tmp_path / "heartbeat.json"
    path.write_text(json.dumps({"status": status, "updated_at": 1000.5}))

    healthy, message = healthcheck.check_heartbeat(
        path, max_age_seconds=180, now=1001
    )

    assert healthy
    assert f"status={status}" in message


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"status": "failed", "updated_at": 1000},
        {"status": [], "updated_at": 1000},
        {"status": "running", "updated_at": True},
        {"status": "running", "updated_at": "1000"},
        {"status": "running", "updated_at": float("nan")},
        {"status": "running", "updated_at": float("inf")},
        {"status": "running"},
    ],
)
def test_invalid_payload_is_unhealthy(tmp_path, payload):
    path = tmp_path / "heartbeat.json"
    path.write_text(json.dumps(payload))

    assert not healthcheck.check_heartbeat(path, max_age_seconds=180, now=1000)[0]


@pytest.mark.parametrize("raw", [None, b"not-json", b"\xff", b" " * 4097])
def test_missing_malformed_and_oversized_heartbeats_are_unhealthy(tmp_path, raw):
    path = tmp_path / "heartbeat.json"
    if raw is not None:
        path.write_bytes(raw)

    assert not healthcheck.check_heartbeat(path, max_age_seconds=180, now=1000)[0]


@pytest.mark.parametrize(("updated_at", "reason"), [(819, "stale"), (1061, "future")])
def test_stale_or_future_heartbeat_is_unhealthy(tmp_path, updated_at, reason):
    path = tmp_path / "heartbeat.json"
    path.write_text(json.dumps({"status": "running", "updated_at": updated_at}))

    healthy, message = healthcheck.check_heartbeat(path, max_age_seconds=180, now=1000)

    assert not healthy
    assert reason in message


def test_main_uses_environment_and_failure_exit_status(tmp_path, monkeypatch):
    path = tmp_path / "heartbeat.json"
    path.write_text(json.dumps({"status": "running", "updated_at": 1000}))
    monkeypatch.setenv("SHIPPING_TRANSPORT_HEARTBEAT_FILE", str(path))
    monkeypatch.setenv("SHIPPING_TRANSPORT_HEARTBEAT_MAX_AGE_SECONDS", "10")
    monkeypatch.setattr(healthcheck.time, "time", lambda: 1005)
    assert healthcheck.main() == 0

    monkeypatch.setattr(healthcheck.time, "time", lambda: 1011)
    assert healthcheck.main() == 1
