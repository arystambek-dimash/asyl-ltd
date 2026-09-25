import json

import apipay_monitor_healthcheck
import heartbeat_healthcheck
import passage_scale_monitor_healthcheck
import pytest
import shipping_transport_monitor_healthcheck
import whatsapp_bot_healthcheck

from apps.common import heartbeat as heartbeat_module
from apps.common.heartbeat import (
    LIVE_STATUSES,
    MAX_HEARTBEAT_BYTES,
    check_heartbeat,
    write_heartbeat,
)


def test_atomic_heartbeat_uses_liveness_contract(tmp_path):
    heartbeat = tmp_path / "monitor" / "heartbeat.json"

    write_heartbeat(str(heartbeat), "running", now=1_725_350_400.5)

    assert json.loads(heartbeat.read_text(encoding="utf-8")) == {
        "status": "running",
        "updated_at": 1_725_350_400.5,
    }
    assert list(heartbeat.parent.iterdir()) == [heartbeat]
    assert check_heartbeat(heartbeat, max_age_seconds=60, now=1_725_350_401)[0]


@pytest.mark.parametrize("status", sorted(LIVE_STATUSES))
def test_recent_live_heartbeat_is_healthy(tmp_path, status):
    path = tmp_path / "heartbeat.json"
    path.write_text(json.dumps({"status": status, "updated_at": 1000.5}))

    healthy, message = check_heartbeat(path, max_age_seconds=60, now=1001)

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

    assert not check_heartbeat(path, max_age_seconds=180, now=1000)[0]


@pytest.mark.parametrize(
    "raw", [None, b"not-json", b"\xff", b" " * (MAX_HEARTBEAT_BYTES + 1)]
)
def test_missing_malformed_and_oversized_heartbeats_are_unhealthy(tmp_path, raw):
    path = tmp_path / "heartbeat.json"
    if raw is not None:
        path.write_bytes(raw)

    assert not check_heartbeat(path, max_age_seconds=180, now=1000)[0]


@pytest.mark.parametrize(("updated_at", "reason"), [(819, "stale"), (1061, "future")])
def test_stale_or_future_heartbeat_is_unhealthy(tmp_path, updated_at, reason):
    path = tmp_path / "heartbeat.json"
    path.write_text(json.dumps({"status": "running", "updated_at": updated_at}))

    healthy, message = check_heartbeat(path, max_age_seconds=180, now=1000)

    assert not healthy
    assert reason in message


@pytest.mark.parametrize(
    ("script", "file_env", "max_age_env", "live", "dead"),
    [
        (
            passage_scale_monitor_healthcheck,
            "VEHICLE_PLATE_AUTO_SCALE_HEARTBEAT_FILE",
            "VEHICLE_PLATE_AUTO_SCALE_HEARTBEAT_MAX_AGE_SECONDS",
            ("disabled", "running", "degraded"),
            ("error",),
        ),
        (
            whatsapp_bot_healthcheck,
            "WHATSAPP_BOT_HEARTBEAT_FILE",
            "WHATSAPP_BOT_HEARTBEAT_MAX_AGE_SECONDS",
            ("disabled", "running", "degraded"),
            ("stopped",),
        ),
        (
            shipping_transport_monitor_healthcheck,
            "SHIPPING_TRANSPORT_HEARTBEAT_FILE",
            "SHIPPING_TRANSPORT_HEARTBEAT_MAX_AGE_SECONDS",
            ("running", "degraded"),
            ("disabled",),
        ),
        (
            apipay_monitor_healthcheck,
            "APIPAY_MONITOR_HEARTBEAT_FILE",
            "APIPAY_MONITOR_HEARTBEAT_MAX_AGE_SECONDS",
            ("running", "ok"),
            ("error", "degraded"),
        ),
    ],
)
def test_monitor_scripts_read_environment_and_their_live_statuses(
    tmp_path, monkeypatch, script, file_env, max_age_env, live, dead
):
    path = tmp_path / "heartbeat.json"
    monkeypatch.setenv(file_env, str(path))
    monkeypatch.setenv(max_age_env, "10")
    monkeypatch.setattr(heartbeat_module.time, "time", lambda: 1005)
    assert script.main() == 1  # файла ещё нет

    for status in live:
        write_heartbeat(str(path), status, now=1000)
        assert script.main() == 0
    for status in dead:
        write_heartbeat(str(path), status, now=1000)
        assert script.main() == 1

    write_heartbeat(str(path), live[0], now=994)
    assert script.main() == 1


def test_generic_script_takes_path_and_max_age_from_arguments(tmp_path, monkeypatch):
    path = tmp_path / "heartbeat.json"
    monkeypatch.setattr(heartbeat_module.time, "time", lambda: 1005)
    assert heartbeat_healthcheck.main([str(path), "10"]) == 1  # файла ещё нет

    write_heartbeat(str(path), "degraded", now=1000)
    assert heartbeat_healthcheck.main([str(path), "10"]) == 0
    assert heartbeat_healthcheck.main([str(path), "4"]) == 1
    assert heartbeat_healthcheck.main([str(path)]) == 1
    assert heartbeat_healthcheck.main([str(path), "soon"]) == 1
