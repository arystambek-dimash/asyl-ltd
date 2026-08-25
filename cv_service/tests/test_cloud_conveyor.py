from __future__ import annotations

import json
import ssl
import threading
import time
import uuid
from pathlib import Path

import pytest

from cv_service.cloud_conveyor import (
    CloudConveyorObserver,
    _post_json,
)
from cv_service.settings import (
    CANONICAL_CONVEYOR_CLOUD_API_URL,
    Settings,
)

DIGEST = "a" * 64
TOKEN = "cloud-observation-token"


def cloud_settings(**overrides) -> Settings:
    values = {
        "api_key_sha256": DIGEST,
        "model_path": Path("best.pt"),
        "event_db_path": Path(":memory:"),
        "conveyor_cloud_cameras": ("cam2",),
        "conveyor_cloud_api_key": TOKEN,
        "conveyor_io_timeout_seconds": 0.05,
    }
    values.update(overrides)
    return Settings(**values)


def wait_for(predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition was not reached")


class ManualClock:
    def __init__(self, value: float):
        self.value = value

    def __call__(self) -> float:
        return self.value


def wake(observer: CloudConveyorObserver) -> None:
    with observer._condition:
        observer._condition.notify_all()


def test_active_observations_coalesce_at_two_hz_and_target_is_immediate():
    clock = ManualClock(100.0)
    sent = []
    sent_event = threading.Event()

    def transport(url, key, payload, timeout, context):
        sent.append((clock(), url, key, dict(payload), timeout, context))
        sent_event.set()

    observer = CloudConveyorObserver(
        cloud_settings(),
        transport=transport,
        monotonic=clock,
    )
    try:
        assert observer.begin_session("cam2", 41, 5)
        assert observer.observe("cam2", 41, 5, 1, 99.9)
        assert sent_event.wait(1)
        sent_event.clear()

        assert observer.observe("cam2", 41, 5, 2, 100.0)
        assert observer.observe("cam2", 41, 5, 3, 100.0)
        time.sleep(0.03)
        assert len(sent) == 1

        clock.value = 100.5
        wake(observer)
        wait_for(lambda: len(sent) == 2)
        assert [row[3]["total"] for row in sent] == [1, 3]
        assert sent[1][0] - sent[0][0] == 0.5

        # Terminal target bypasses the next ordinary 500ms send window.
        assert observer.observe("cam2", 41, 5, 5, 100.5)
        wait_for(lambda: len(sent) == 3)
        terminal = sent[-1][3]
        assert terminal["terminal_reason"] == "target_reached"
        assert terminal["total"] == 5
        assert list(terminal) == [
            "protocol_version",
            "camera",
            "session_id",
            "target_total",
            "edge_boot_id",
            "seq",
            "total",
            "terminal_reason",
        ]
        assert [row[3]["seq"] for row in sent] == [1, 2, 3]
        assert len({row[3]["edge_boot_id"] for row in sent}) == 1
        uuid.UUID(terminal["edge_boot_id"])
        assert all(row[1] == CANONICAL_CONVEYOR_CLOUD_API_URL for row in sent)
        assert all(row[2] == TOKEN for row in sent)
        assert all(isinstance(row[5], ssl.SSLContext) for row in sent)
    finally:
        observer.close()


def test_failed_terminal_retries_same_idempotency_payload_until_ack():
    clock = ManualClock(200.0)
    attempts = []

    def transport(_url, _key, payload, _timeout, _context):
        attempts.append(dict(payload))
        if len(attempts) == 1:
            raise OSError(f"failure must not expose {TOKEN}")

    observer = CloudConveyorObserver(
        cloud_settings(),
        transport=transport,
        monotonic=clock,
    )
    try:
        assert observer.begin_session("cam2", 42, 2)
        assert observer.terminate("cam2", 42, 2, 1, "manual_stop")
        wait_for(lambda: len(attempts) == 1)
        assert TOKEN not in str(observer.status("cam2")["error"])

        clock.value = 200.5
        wake(observer)
        wait_for(lambda: len(attempts) == 2)
        assert attempts[1] == attempts[0]
        assert attempts[1]["seq"] == 1
        assert observer.status("cam2")["error"] is None
    finally:
        observer.close()


def test_new_session_cancels_old_terminal_retry():
    clock = ManualClock(300.0)
    attempts = []

    def transport(_url, _key, payload, _timeout, _context):
        attempts.append(dict(payload))
        if payload["session_id"] == 43:
            raise OSError("offline")

    observer = CloudConveyorObserver(
        cloud_settings(),
        transport=transport,
        monotonic=clock,
    )
    try:
        observer.begin_session("cam2", 43, 2)
        observer.terminate("cam2", 43, 2, 0, "capture_failed")
        wait_for(lambda: len(attempts) == 1)

        assert observer.begin_session("cam2", 44, 3)
        clock.value = 301.0
        observer.observe("cam2", 44, 3, 1, 301.0)
        wake(observer)
        wait_for(lambda: len(attempts) == 2)
        assert [item["session_id"] for item in attempts] == [43, 44]
    finally:
        observer.close()


def test_counter_regression_is_an_immediate_terminal_latch():
    delivered = []

    def transport(_url, _key, payload, _timeout, _context):
        delivered.append(dict(payload))

    observer = CloudConveyorObserver(cloud_settings(), transport=transport)
    try:
        observer.begin_session("cam2", 45, 10)
        observer.observe("cam2", 45, 10, 4, time.monotonic())
        wait_for(lambda: len(delivered) == 1)

        observer.observe("cam2", 45, 10, 3, time.monotonic())
        wait_for(lambda: len(delivered) == 2)
        assert delivered[-1]["terminal_reason"] == "counter_regressed"
        assert observer.observe("cam2", 45, 10, 5, time.monotonic()) is False
    finally:
        observer.close()


def test_default_transport_uses_verified_tls_bearer_and_rejects_redirects(
    monkeypatch,
):
    captured = {}

    class Response:
        status = 200

        def read(self, size):
            assert size == 4096
            return json.dumps({"accepted": True, "duplicate": False}).encode()

    class Connection:
        def __init__(self, host, port, timeout, context):
            captured.update(
                {
                    "host": host,
                    "port": port,
                    "timeout": timeout,
                    "context": context,
                }
            )

        def request(self, method, path, body, headers):
            captured.update(
                {
                    "method": method,
                    "path": path,
                    "body": body,
                    "headers": headers,
                }
            )

        def getresponse(self):
            return Response()

        def close(self):
            captured["closed"] = True

    monkeypatch.setattr(
        "cv_service.cloud_conveyor.http.client.HTTPSConnection",
        Connection,
    )
    context = ssl.create_default_context()
    _post_json(
        CANONICAL_CONVEYOR_CLOUD_API_URL,
        TOKEN,
        {"protocol_version": 1},
        0.25,
        context,
    )

    assert captured["host"] == "asyl-ltd.kz"
    assert captured["port"] == 443
    assert captured["path"] == "/api/conveyors/v1/ai/observation/"
    assert captured["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert "X-Conveyor-AI-Key" not in captured["headers"]
    assert captured["context"] is context
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert captured["closed"] is True


def _cloud_env(monkeypatch) -> None:
    monkeypatch.setenv("AI_SERVICE_API_KEY_SHA256", DIGEST)
    monkeypatch.delenv("AI_SERVICE_API_KEY", raising=False)
    monkeypatch.setenv("AI_CONVEYOR_CLOUD_CAMERAS", "cam2,cam3,cam2")
    monkeypatch.setenv("AI_CONVEYOR_CLOUD_API_KEY", TOKEN)
    monkeypatch.delenv("AI_CONVEYOR_CONTROLLERS_JSON", raising=False)
    monkeypatch.delenv("AI_CONVEYOR_CLOUD_API_URL", raising=False)


def test_cloud_settings_are_strict_canonical_and_secret_repr_is_redacted(monkeypatch):
    _cloud_env(monkeypatch)
    settings = Settings.from_env()
    assert settings.conveyor_cloud_cameras == ("cam2", "cam3")
    assert settings.conveyor_cloud_api_url == CANONICAL_CONVEYOR_CLOUD_API_URL
    assert settings.conveyor_cloud_api_key == TOKEN
    assert TOKEN not in repr(settings)

    monkeypatch.setenv("AI_CONVEYOR_CLOUD_API_URL", "https://example.invalid/")
    with pytest.raises(ValueError, match="canonical HTTPS"):
        Settings.from_env()

    monkeypatch.setenv(
        "AI_CONVEYOR_CLOUD_API_URL",
        CANONICAL_CONVEYOR_CLOUD_API_URL,
    )
    monkeypatch.setenv("AI_CONVEYOR_CLOUD_API_KEY", "")
    with pytest.raises(ValueError, match="CLOUD_API_KEY"):
        Settings.from_env()


def test_cloud_and_direct_camera_overlap_is_rejected(monkeypatch):
    _cloud_env(monkeypatch)
    monkeypatch.setenv(
        "AI_CONVEYOR_CONTROLLERS_JSON",
        json.dumps(
            {
                "cam2": {
                    "host": "192.0.2.10",
                    "address": 0,
                    "feedback_address": 1,
                },
            }
        ),
    )

    with pytest.raises(ValueError, match="overlap"):
        Settings.from_env()
