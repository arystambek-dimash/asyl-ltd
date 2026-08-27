from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import threading
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from cv_service.app import create_app
from cv_service.contracts import Detection, ProcessorOptions
from cv_service.event_journal import (
    CountEvent,
    CountEventJournal,
    JournalIntegrityError,
    JournalTransientError,
)
from cv_service.processor import (
    CameraProcessor,
    DroppingFrameQueue,
    LineTracker,
    ProcessorManager,
)
from cv_service.runtime import MediaMtxClient, select_h264_encoder, validate_classes
from cv_service.settings import Settings, parse_camera, parse_line
from cv_service.state import (
    AlwaysOnStateStore,
    CameraRoleStateStore,
    CountingLineStateStore,
)

KEY = "backend-only-secret"
DIGEST = hashlib.sha256(KEY.encode()).hexdigest()


class FakeModel:
    def __init__(self):
        self.calls = 0
        self.concurrent = 0
        self.max_concurrent = 0
        self.lock = threading.Lock()

    def metadata(self):
        return {"id": "best.pt", "device": "cpu", "classes": ["Red_50"]}

    def predict(self, _frame):
        with self.lock:
            self.concurrent += 1
            self.max_concurrent = max(self.max_concurrent, self.concurrent)
        time.sleep(0.005)
        self.calls += 1
        with self.lock:
            self.concurrent -= 1
        return [Detection(0, 0, 10, 10, 0.9, "Red_50")]


class FakeMediaMtx:
    def __init__(self, cameras=("cam2", "cam3")):
        self.sources = {f"{camera}sub" for camera in cameras} | set(cameras)

    def validate_source(self, camera, source_stream):
        if source_stream not in self.sources:
            raise ValueError(f"MediaMTX source does not exist for {camera}: {source_stream}")

    def camera_inventory(self):
        cameras = {item.removesuffix("sub") for item in self.sources}
        return {
            camera: {"cam": camera, "main": camera in self.sources, "sub": f"{camera}sub" in self.sources}
            for camera in cameras
        }

    def device_inventory(self):
        return [
            {
                "kind": "nvr-channel",
                "path": camera,
                "sub": f"{camera}sub",
                "channel": int(camera.removeprefix("cam")),
                "online": True,
            }
            for camera in sorted({item.removesuffix("sub") for item in self.sources})
        ]

    def path_ready(self, _stream):
        return True

    def delete_recording_segments(self, stream, starts):
        self.deleted_recordings = (stream, starts)
        return len(starts)


class FakeProcessor:
    def __init__(self, manager, camera, options):
        self.manager = manager
        self.camera = camera
        self.options = options
        self.source_stream = manager.settings.source_stream(camera, options.source)
        self.running = False
        self.mode = "idle"
        self.total = 0
        self.start_calls = 0
        self.closed = False
        self.last_detections = []

    def configure(self, options):
        self.options = options
        self.source_stream = self.manager.settings.source_stream(self.camera, options.source)

    def update_counting_line(self, line, direction):
        self.options = self.options.model_copy(update={
            "line": line,
            "direction": direction,
        })

    def start_session(self, options):
        self.options = options
        self.total = 0
        self.running = True
        self.mode = "session"
        self.start_calls += 1

    def start_always_on(self, options, *, force_session_handoff=False):
        self.options = options
        if self.mode == "session" and not force_session_handoff:
            return
        if self.mode != "always_on" or force_session_handoff:
            self.total = 0
        self.running = True
        self.mode = "always_on"

    def wait_until_warm(self):
        return None

    def reset(self):
        if not self.running:
            raise ValueError("processor is not counting")
        self.total = 0

    def idle(self):
        self.running = False
        self.mode = "idle"

    def close(self):
        self.closed = True

    def mark_dropped(self):
        pass

    def apply_inference(self, _frame, _captured_at, detections, *_args):
        self.last_detections = detections

    def status(self):
        return {
            "cam": self.camera,
            "running": self.running,
            "mode": self.mode,
            "recording": self.mode == "session",
            "processor_alive": not self.closed,
            "warm": not self.running and not self.closed,
            "stream": f"{self.camera}ai",
            "source": self.options.source,
            "line": self.options.line or self.manager.settings.default_line,
            "direction": self.options.direction,
            "total": self.total,
            "per_color": {},
            "confidence_sums": {},
            "last_frame_at": None,
            "metrics": {
                "camera_fps": 0,
                "inference_fps": 0,
                "inference_avg_ms": 0,
                "inference_p95_ms": 0,
                "frame_latency_p95_ms": 0,
                "dropped_frames": 0,
                "queue_depth": self.manager.queue.qsize(),
                "camera_reconnects": 0,
                "publisher_state": "connected",
            },
            "model": self.manager.model.metadata(),
        }


def make_settings(max_processors=2, **overrides):
    values = {
        "api_key_sha256": DIGEST,
        "model_path": Path("best.pt"),
        "model_device": "cpu",
        "max_active_processors": max_processors,
        "event_db_path": Path(":memory:"),
    }
    values.update(overrides)
    return Settings(**values)


def make_manager(
    max_processors=2, *, settings=None, model=None, line_state_store=None,
    wagon_detector=None,
):
    return ProcessorManager(
        settings or make_settings(max_processors),
        model or FakeModel(),
        FakeMediaMtx(),
        "libx264",
        processor_factory=FakeProcessor,
        line_state_store=line_state_store,
        wagon_detector=wagon_detector,
    )


def test_manager_close_joins_worker_without_timeout_before_journal_close():
    order = []

    class Processor:
        def close(self):
            order.append("processor")

    class Closable:
        def __init__(self, name):
            self.name = name

        def close(self):
            order.append(self.name)

    class StopEvent:
        def set(self):
            order.append("stop")

    class Worker:
        def join(self, *args, **kwargs):
            assert args == ()
            assert kwargs == {}
            order.append("worker")

    class Journal:
        def close(self):
            assert order[-1] == "worker"
            order.append("journal")

    manager = ProcessorManager.__new__(ProcessorManager)
    manager._lock = threading.RLock()
    manager.processors = {"cam3": Processor()}
    manager._stop = StopEvent()
    manager._worker = Worker()
    manager.event_journal = Journal()

    manager.close()

    assert order == ["processor", "stop", "worker", "journal"]


def test_manager_does_not_retry_permanent_journal_error(monkeypatch):
    calls = []

    class Journal:
        def append(self, count_event):
            calls.append(count_event)
            raise JournalIntegrityError("event_key replay changed contents")

    manager = ProcessorManager.__new__(ProcessorManager)
    manager.event_journal = Journal()
    monkeypatch.setattr(
        "cv_service.processor.time.sleep",
        lambda _seconds: pytest.fail("permanent errors must not retry"),
    )
    crossing = object()

    with pytest.raises(JournalIntegrityError, match="replay changed"):
        manager.record_count_event(crossing)
    assert calls == [crossing]


@pytest.fixture
def service():
    manager = make_manager()
    with TestClient(create_app(manager)) as client:
        yield manager, client


def auth():
    return {"X-Api-Key": KEY}


@pytest.mark.parametrize(
    "path", [
        "/health",
        "/events",
        "/cameras",
        "/cameras/cam2/line",
        "/processors",
        "/always-on",
        "/camera-roles/wagon-number",
        "/processors/cam2",
    ]
)
def test_every_endpoint_requires_header_and_query_key_is_ignored(service, path):
    _manager, client = service
    assert client.get(path).status_code == 401
    assert client.get(f"{path}?api_key={KEY}").status_code == 401
    assert client.get(path, headers={"X-Api-Key": "wrong"}).status_code == 401


def test_health_has_startup_proof_and_no_browser_cors(service):
    _manager, client = service
    response = client.get("/health", headers={**auth(), "Origin": "https://app.invalid"})
    assert response.status_code == 200
    assert response.json()["startup"] == {
        "model_reused": True,
        "model_instances": 1,
        "encoder": "libx264",
    }
    assert response.json()["capabilities"]["wagon_plate"] == {
        "available": False,
        "provider": None,
        "ocr": False,
    }
    assert "access-control-allow-origin" not in response.headers


def test_health_returns_degraded_without_waiting_for_processor_lock(service):
    manager, client = service
    manager.event_journal._last_write_error = "database is locked"
    manager.statuses = lambda: (_ for _ in ()).throw(
        AssertionError("health must not acquire processor status locks")
    )

    response = client.get("/health", headers=auth())

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["event_journal"] == {
        "available": False,
        "journal_id": manager.event_journal.journal_id,
        "error": "database is locked",
    }
    assert response.json()["counting"] is None


def test_events_endpoint_exposes_durable_cursor_contract(service):
    manager, client = service
    event_id = manager.record_count_event(CountEvent(
        created_at="2026-08-25T08:30:00.000+00:00",
        cam="cam3",
        source="sub",
        mode="always_on",
        generation=4,
        frame=812,
        track_id=23,
        class_id=0,
        class_name="Red_50",
        confidence=0.91,
        direction="positive",
        point_x=120.5,
        point_y=241.0,
        weight_kg=50.0,
        total_after=2_292,
        total_weight_after=114_600.0,
    ))

    response = client.get(
        "/events?after_id=0&limit=500&cam=cam3", headers=auth(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {
        "journal_id", "events", "next_after_id", "has_more",
    }
    assert payload["journal_id"] == manager.event_journal.journal_id
    assert payload["next_after_id"] == event_id
    assert payload["has_more"] is False
    assert payload["events"][0]["id"] == event_id
    assert payload["events"][0]["total_after"] == 2_292

    empty = client.get(
        f"/events?after_id={event_id}&limit=500&cam=cam3", headers=auth(),
    ).json()
    assert empty["events"] == []
    assert empty["next_after_id"] == event_id


@pytest.mark.parametrize(
    "query",
    ["after_id=-1", "limit=0", "limit=501", "cam=CAM3"],
)
def test_events_endpoint_rejects_invalid_cursor_query(service, query):
    _manager, client = service
    assert client.get(f"/events?{query}", headers=auth()).status_code == 400


def test_delete_recordings_is_authenticated_and_deletes_exact_segments(service):
    manager, client = service
    starts = ["2026-07-21T10:00:00+06:00", "2026-07-21T10:01:00+06:00"]
    assert client.request(
        "DELETE", "/recordings", json={"stream": "cam2ai", "starts": starts}
    ).status_code == 401

    response = client.request(
        "DELETE", "/recordings", headers=auth(),
        json={"stream": "cam2ai", "starts": starts},
    )

    assert response.status_code == 200
    assert response.json() == {"deleted": 2, "requested": 2}
    assert manager.mediamtx.deleted_recordings == ("cam2ai", starts)


def test_camera_inventory_keeps_backend_compatible_devices(service):
    _manager, client = service
    payload = client.get("/cameras", headers=auth()).json()
    assert payload["devices"][0]["kind"] == "nvr-channel"
    assert payload["devices"][0]["path"] == "cam2"
    assert payload["devices"][0]["sub"] == "cam2sub"
    assert payload["cameras"][0]["cam"] == "cam2"
    assert payload["line_configs"] == {}


def test_counting_line_contract_persists_applies_and_joins_inventory(tmp_path):
    store = CountingLineStateStore(tmp_path / "state" / "counting-lines.json")
    manager = make_manager(line_state_store=store)
    with TestClient(create_app(manager)) as client:
        initial = client.get("/cameras/cam2/line", headers=auth())
        assert initial.status_code == 200
        assert initial.json() == {
            "cam": "cam2",
            "configured": False,
            "coordinate_space": "normalized",
            "line": {"x1": 0.0, "y1": 0.5, "x2": 1.0, "y2": 0.5},
            "line_spec": "0,0.5,1,0.5",
            "direction": "any",
            "updated_at": None,
        }

        saved = client.put(
            "/cameras/cam2/line",
            headers=auth(),
            json={
                "line": {"x1": 0.08, "y1": 0.61, "x2": 0.93, "y2": 0.58},
                "direction": "down",
            },
        )
        assert saved.status_code == 200
        assert saved.json()["saved"] is True
        assert saved.json()["applied_to_processor"] is False
        assert saved.json()["line_spec"] == "0.08,0.61,0.93,0.58"
        inventory = client.get("/cameras", headers=auth()).json()
        assert inventory["line_configs"]["cam2"] == {
            key: value
            for key, value in saved.json().items()
            if key not in {"ok", "saved", "applied_to_processor"}
        }

        started = client.post("/processors/cam2", headers=auth(), json={})
        assert started.json()["line"] == "0.08,0.61,0.93,0.58"
        assert started.json()["direction"] == "down"
        updated = client.put(
            "/cameras/cam2/line",
            headers=auth(),
            json={"line": [0.1, 0.2, 0.8, 0.9], "direction": "up"},
        )
        assert updated.status_code == 200
        assert updated.json()["applied_to_processor"] is True
        assert manager.get("cam2").options.line == "0.1,0.2,0.8,0.9"
        assert manager.get("cam2").options.direction == "up"

    restored = make_manager(line_state_store=store)
    with TestClient(create_app(restored)) as client:
        payload = client.get("/cameras/cam2/line", headers=auth()).json()
        assert payload["configured"] is True
        assert payload["line"] == {"x1": 0.1, "y1": 0.2, "x2": 0.8, "y2": 0.9}
        assert payload["direction"] == "up"


@pytest.mark.parametrize(
    "body",
    [
        {"line": [0.1, 0.2, 0.1, 0.2], "direction": "any"},
        {"line": [0.1, 0.2, 2, 0.9], "direction": "any"},
        {"line": [0.1, True, 0.8, 0.9], "direction": "any"},
        {"line": [0.1, 0.2, 0.8, 0.9], "direction": "sideways"},
        {"line": {"x1": 0.1, "y1": 0.2, "x2": 0.8}, "direction": "any"},
    ],
)
def test_counting_line_rejects_invalid_contract_without_writing(tmp_path, body):
    path = tmp_path / "counting-lines.json"
    manager = make_manager(line_state_store=CountingLineStateStore(path))
    with TestClient(create_app(manager)) as client:
        response = client.put("/cameras/cam2/line", headers=auth(), json=body)
        assert response.status_code in {400, 422}
    assert not path.exists()


def test_counting_line_rejects_unknown_camera_without_writing(tmp_path):
    path = tmp_path / "counting-lines.json"
    manager = make_manager(line_state_store=CountingLineStateStore(path))
    with TestClient(create_app(manager)) as client:
        assert client.put(
            "/cameras/cam9/line",
            headers=auth(),
            json={"line": [0.1, 0.2, 0.8, 0.9], "direction": "any"},
        ).status_code == 400
    assert not path.exists()


@pytest.mark.parametrize("payload", [None, [], 7, "invalid"])
def test_counting_line_state_rejects_non_object_root(tmp_path, payload):
    path = tmp_path / "counting-lines.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuntimeError, match="cannot read counting-lines state"):
        CountingLineStateStore(path).load()


def test_saved_line_is_reported_when_live_apply_fails(tmp_path, monkeypatch):
    store = CountingLineStateStore(tmp_path / "counting-lines.json")
    manager = make_manager(line_state_store=store)
    with TestClient(create_app(manager)) as client:
        client.post("/processors/cam2", headers=auth(), json={})
        processor = manager.get("cam2")

        def fail_apply(_line, _direction):
            raise RuntimeError("processor unavailable")

        monkeypatch.setattr(processor, "update_counting_line", fail_apply)
        response = client.put(
            "/cameras/cam2/line",
            headers=auth(),
            json={"line": [0.1, 0.2, 0.8, 0.9], "direction": "any"},
        )

        assert response.status_code == 503
        assert response.json()["saved"] is True
        assert response.json()["applied_to_processor"] is False
        persisted = client.get("/cameras/cam2/line", headers=auth()).json()
        assert persisted["line_spec"] == "0.1,0.2,0.8,0.9"


def test_processor_creation_and_line_save_are_serialized():
    old_config = {
        "line": "0,0.5,1,0.5",
        "direction": "any",
        "updated_at": "2026-08-10T00:00:00.000+00:00",
    }
    new_config = {
        "line": "0.1,0.2,0.8,0.9",
        "direction": "up",
        "updated_at": "2026-08-10T00:01:00.000+00:00",
    }

    class CoordinatedLineStore:
        def __init__(self):
            self.config = old_config
            self.get_entered = threading.Event()
            self.release_get = threading.Event()
            self.save_entered = threading.Event()

        def get(self, _camera):
            # Snapshot before blocking reproduces the dangerous stale read.
            snapshot = dict(self.config)
            self.get_entered.set()
            if not self.release_get.wait(timeout=2):
                raise RuntimeError("test did not release line-state read")
            return snapshot

        def save(self, _camera, _value, _direction):
            self.save_entered.set()
            self.config = new_config
            return dict(new_config)

    store = CoordinatedLineStore()
    manager = make_manager(line_state_store=store)
    save_started = threading.Event()

    def ensure_processor():
        return manager._ensure("cam2", ProcessorOptions())

    def save_line():
        save_started.set()
        return manager.save_counting_line(
            "cam2", [0.1, 0.2, 0.8, 0.9], "up"
        )

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        ensure_future = executor.submit(ensure_processor)
        assert store.get_entered.wait(timeout=1)
        save_future = executor.submit(save_line)
        assert save_started.wait(timeout=1)

        # save() must still be waiting for ProcessorManager._lock while the
        # creation path owns it and is resolving its durable-line snapshot.
        save_entered_during_read = store.save_entered.wait(timeout=0.25)
        store.release_get.set()
        ensure_future.result(timeout=2)
        status, payload = save_future.result(timeout=2)

        assert save_entered_during_read is False
        assert status == 200
        assert payload["applied_to_processor"] is True
        assert manager.get("cam2").options.line == new_config["line"]
        assert manager.get("cam2").options.direction == "up"
    finally:
        store.release_get.set()
        executor.shutdown(wait=True)
        manager.close()


def test_session_start_and_line_save_are_serialized():
    start_entered = threading.Event()
    release_start = threading.Event()
    save_entered = threading.Event()
    save_started = threading.Event()
    old_config = {
        "line": "0,0.5,1,0.5",
        "direction": "any",
        "updated_at": "2026-08-10T00:00:00.000+00:00",
    }
    new_config = {
        "line": "0.1,0.2,0.8,0.9",
        "direction": "down",
        "updated_at": "2026-08-10T00:01:00.000+00:00",
    }

    class BlockingStartProcessor(FakeProcessor):
        def start_session(self, options):
            start_entered.set()
            if not release_start.wait(timeout=2):
                raise RuntimeError("test did not release session start")
            super().start_session(options)

    class ObservedLineStore:
        def __init__(self):
            self.config = old_config

        def get(self, _camera):
            return dict(self.config)

        def save(self, _camera, _value, _direction):
            save_entered.set()
            self.config = new_config
            return dict(new_config)

    store = ObservedLineStore()
    manager = ProcessorManager(
        make_settings(),
        FakeModel(),
        FakeMediaMtx(),
        "libx264",
        processor_factory=BlockingStartProcessor,
        line_state_store=store,
    )

    def save_line():
        save_started.set()
        return manager.save_counting_line(
            "cam2", [0.1, 0.2, 0.8, 0.9], "down"
        )

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        start_future = executor.submit(
            manager.start, "cam2", ProcessorOptions()
        )
        assert start_entered.wait(timeout=1)
        save_future = executor.submit(save_line)
        assert save_started.wait(timeout=1)

        save_entered_during_start = save_entered.wait(timeout=0.25)
        release_start.set()
        start_future.result(timeout=2)
        status, payload = save_future.result(timeout=2)

        assert save_entered_during_start is False
        assert status == 200
        assert payload["applied_to_processor"] is True
        processor = manager.get("cam2")
        assert processor.options.line == new_config["line"]
        assert processor.options.direction == "down"
    finally:
        release_start.set()
        executor.shutdown(wait=True)
        manager.close()


def test_wagon_detection_contract_is_bounded_and_fail_closed(monkeypatch):
    frame = SimpleNamespace(shape=(720, 1280, 3))
    monkeypatch.setattr("cv_service.processor.decode_jpeg", lambda _data: frame)

    unavailable = make_manager()
    with TestClient(create_app(unavailable)) as client:
        response = client.post(
            "/wagon-number/detect",
            headers={**auth(), "Content-Type": "image/jpeg"},
            content=b"\xff\xd8\xffframe",
        )
        assert response.status_code == 503
        assert "not installed" in response.json()["detail"]

    class WagonDetector:
        def metadata(self):
            return {"provider": "wagon-number.pt+paddleocr", "ocr": True}

        def detect(self, _frame):
            return {
                "number": "12345678",
                "detections": [{
                    "bbox": [100.0, 200.0, 400.0, 300.0],
                    "class_name": "wagon_plate",
                    "confidence": 0.95,
                    "ocr": {"number": "12345678", "accepted": True},
                }],
            }

    capable = make_manager(wagon_detector=WagonDetector())
    with TestClient(create_app(capable)) as client:
        assert client.post(
            "/wagon-number/detect",
            headers={"Content-Type": "image/jpeg"},
            content=b"\xff\xd8\xffframe",
        ).status_code == 401
        response = client.post(
            "/wagon-number/detect",
            headers={**auth(), "Content-Type": "image/jpeg"},
            content=b"\xff\xd8\xffframe",
        )
        assert response.status_code == 200
        assert response.json() == {
            "number": "12345678",
            "detections": [{
                "bbox": [100.0, 200.0, 400.0, 300.0],
                "class_name": "wagon_plate",
                "confidence": 0.95,
                "ocr": {"number": "12345678", "accepted": True},
            }],
            "detection_frame": {"width": 1280, "height": 720},
        }


def test_wagon_detection_rejects_invalid_or_oversized_jpeg(monkeypatch):
    manager = make_manager(settings=make_settings(wagon_frame_max_bytes=4))
    with TestClient(create_app(manager)) as client:
        invalid = client.post(
            "/wagon-number/detect",
            headers={**auth(), "Content-Type": "image/jpeg"},
            content=b"bad",
        )
        assert invalid.status_code == 400
        oversized = client.post(
            "/wagon-number/detect",
            headers={**auth(), "Content-Type": "image/jpeg"},
            content=b"\xff\xd8\xff12",
        )
        assert oversized.status_code == 413
        wrong_type = client.post(
            "/wagon-number/detect",
            headers={**auth(), "Content-Type": "application/octet-stream"},
            content=b"\xff\xd8\xff",
        )
        assert wrong_type.status_code == 415


def test_mediamtx_inventory_includes_direct_wall_camera_but_not_ai_output(monkeypatch):
    client = MediaMtxClient("http://mediamtx.invalid")
    monkeypatch.setattr(client, "paths", lambda: {
        "cam2": {"ready": True},
        "cam2sub": {"ready": True},
        "cam2ai": {"ready": True},
        "cam_8c28": {"ready": True},
        "cam_8c28sub": {"ready": True},
    })
    devices = client.device_inventory()
    assert devices == [
        {
            "kind": "nvr-channel", "path": "cam2", "sub": "cam2sub",
            "channel": 2, "model": "Камера 2", "online": True,
        },
        {
            "kind": "direct", "path": "cam_8c28", "sub": "cam_8c28sub",
            "model": "cam_8c28", "online": True,
        },
    ]


def test_encoder_probe_falls_back_when_listed_gpu_encoder_cannot_start(monkeypatch):
    calls = []

    def run(command, **_kwargs):
        calls.append(command)
        if "-encoders" in command:
            return SimpleNamespace(stdout="h264_nvenc h264_qsv libx264", stderr="")
        if "h264_nvenc" in command:
            raise subprocess.CalledProcessError(1, command)
        return SimpleNamespace(stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    assert select_h264_encoder("ffmpeg") == "h264_qsv"
    assert len(calls) == 3


def test_prewarm_is_idle_and_start_resets_only_once(service):
    manager, client = service
    response = client.post("/processors/cam2/prewarm", headers=auth(), json={"source": "sub"})
    assert response.status_code == 200
    assert response.json()["warm"] is True
    assert response.json()["running"] is False
    processor = manager.get("cam2")
    processor.total = 19

    started = client.post("/processors/cam2", headers=auth(), json={"source": "sub"})
    assert started.json()["running"] is True
    assert started.json()["total"] == 0
    processor.total = 7
    repeated = client.post(
        "/processors/cam2",
        headers=auth(),
        json={"source": "main", "line": "0.5,0,0.5,1"},
    )
    assert repeated.json()["total"] == 7
    assert repeated.json()["source"] == "sub"
    assert processor.start_calls == 1


def test_delete_freezes_result_and_next_start_clears_it(service):
    manager, client = service
    client.post("/processors/cam2", headers=auth(), json={})
    manager.get("cam2").total = 42
    stopped = client.delete("/processors/cam2", headers=auth())
    assert stopped.status_code == 200
    assert stopped.json()["running"] is False
    assert stopped.json()["warm"] is True
    assert stopped.json()["total"] == 42
    assert client.get("/processors/cam2", headers=auth()).json()["total"] == 42
    assert manager.get("cam2").closed is False
    assert client.post("/processors/cam2", headers=auth(), json={}).json()["total"] == 0


def test_always_on_is_persisted_inference_only_and_session_reuses_processor(tmp_path):
    store = AlwaysOnStateStore(tmp_path / "state" / "always-on.json")
    manager = ProcessorManager(
        make_settings(), FakeModel(), FakeMediaMtx(), "libx264",
        processor_factory=FakeProcessor, state_store=store,
    )
    with TestClient(create_app(manager)) as client:
        configured = client.put(
            "/always-on", headers=auth(), json={"cameras": ["cam2"]},
        )
        assert configured.status_code == 200
        assert configured.json()["processors"][0]["mode"] == "always_on"
        assert configured.json()["processors"][0]["recording"] is False
        processor = manager.get("cam2")
        processor.total = 31

        session = client.post("/processors/cam2", headers=auth(), json={})
        assert session.json()["mode"] == "session"
        assert session.json()["recording"] is True
        assert session.json()["total"] == 0
        assert manager.get("cam2") is processor

        processor.total = 9
        stopped = client.delete("/processors/cam2", headers=auth())
        assert stopped.json()["mode"] == "always_on"
        assert stopped.json()["recording"] is False
        assert stopped.json()["running"] is True
    assert store.load() == (["cam2"], "sub")


def test_always_on_state_restores_after_service_restart(tmp_path):
    store = AlwaysOnStateStore(tmp_path / "always-on.json")
    store.save(["cam3", "cam2", "cam2"], "sub")
    manager = ProcessorManager(
        make_settings(), FakeModel(), FakeMediaMtx(), "libx264",
        processor_factory=FakeProcessor, state_store=store,
    )
    restored = manager.restore_always_on()
    assert restored["cameras"] == ["cam2", "cam3"]
    assert all(item["running"] for item in restored["processors"])
    assert all(item["recording"] is False for item in restored["processors"])
    manager.close()


def test_wagon_number_camera_role_is_single_and_survives_restart(tmp_path):
    store = CameraRoleStateStore(tmp_path / "camera-roles.json")
    manager = ProcessorManager(
        make_settings(), FakeModel(), FakeMediaMtx(), "libx264",
        processor_factory=FakeProcessor, role_state_store=store,
    )
    with TestClient(create_app(manager)) as client:
        configured = client.put(
            "/camera-roles/wagon-number",
            headers=auth(),
            json={"camera": "cam2", "source": "main"},
        )
        assert configured.status_code == 200
        assert configured.json() == {
            "camera": "cam2",
            "source": "main",
            "stream": "cam2",
            "assigned": True,
            "mode": "wagon_number_24_7",
        }
    assert store.load_wagon_number() == ("cam2", "main")

    restored = ProcessorManager(
        make_settings(), FakeModel(), FakeMediaMtx(), "libx264",
        processor_factory=FakeProcessor, role_state_store=store,
    )
    assert restored.restore_camera_roles()["camera"] == "cam2"
    restored.close()


def test_wagon_number_camera_role_can_be_reassigned_and_cleared(service):
    manager, client = service
    assigned = client.put(
        "/camera-roles/wagon-number",
        headers=auth(),
        json={"camera": "cam3"},
    )
    assert assigned.status_code == 200
    assert assigned.json()["camera"] == "cam3"
    assert manager.processors == {}

    cleared = client.put(
        "/camera-roles/wagon-number",
        headers=auth(),
        json={"camera": None},
    )
    assert cleared.status_code == 200
    assert cleared.json()["assigned"] is False
    assert cleared.json()["camera"] is None


def test_only_configured_capacity_can_run_always_on(service):
    manager = ProcessorManager(
        make_settings(max_processors=2), FakeModel(),
        FakeMediaMtx(cameras=("cam2", "cam3", "cam4")), "libx264",
        processor_factory=FakeProcessor,
    )
    with TestClient(create_app(manager)) as client:
        response = client.put(
            "/always-on", headers=auth(),
            json={"cameras": ["cam2", "cam3", "cam4"]},
        )
        assert response.status_code == 409
        assert client.get("/always-on", headers=auth()).json()["cameras"] == []


def test_always_on_rejects_unknown_fields_and_camera_ids(service):
    _manager, client = service
    assert client.put(
        "/always-on", headers=auth(),
        json={"cameras": ["cam2"], "record": True},
    ).status_code == 422
    assert client.put(
        "/always-on", headers=auth(), json={"cameras": ["../cam2"]},
    ).status_code == 400


def test_reset_requires_counting_processor(service):
    _manager, client = service
    client.post("/processors/cam2/prewarm", headers=auth(), json={})
    assert client.post("/processors/cam2/reset", headers=auth()).status_code == 400
    client.post("/processors/cam2", headers=auth(), json={})
    assert client.post("/processors/cam2/reset", headers=auth()).status_code == 200


@pytest.mark.parametrize("camera", ["cam0", "cam_2", "cam02", "camx", "CAM2", "cam2/../x"])
def test_camera_id_is_strict(service, camera):
    _manager, client = service
    assert client.post(f"/processors/{camera}", headers=auth(), json={}).status_code in {400, 404}


def test_only_safe_options_are_accepted(service):
    _manager, client = service
    assert client.post("/processors/cam2", headers=auth(), json={"source": "rtsp://evil"}).status_code == 422
    assert client.post("/processors/cam2", headers=auth(), json={"url": "rtsp://evil"}).status_code == 422
    assert client.post("/processors/cam2", headers=auth(), json={"direction": "sideways"}).status_code == 422
    assert client.post("/processors/cam2", headers=auth(), json={"line": "0,2,1,2"}).status_code == 422


def test_capacity_is_enforced_and_unknown_source_rejected():
    manager = make_manager(max_processors=1)
    with TestClient(create_app(manager)) as client:
        assert client.post("/processors/cam2/prewarm", headers=auth(), json={}).status_code == 200
        assert client.post("/processors/cam3/prewarm", headers=auth(), json={}).status_code == 409
    manager = make_manager()
    with TestClient(create_app(manager)) as client:
        assert client.post("/processors/cam9/prewarm", headers=auth(), json={}).status_code == 400


def test_global_inference_worker_is_sequential():
    manager = make_manager()
    p2 = manager._ensure("cam2", ProcessorOptions())
    p3 = manager._ensure("cam3", ProcessorOptions())
    frame = object()
    for index in range(4):
        manager.submit(p2 if index % 2 else p3, frame, time.monotonic())
    deadline = time.monotonic() + 1
    while manager.model.calls < 4 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert manager.model.calls == 4
    assert manager.model.max_concurrent == 1
    manager.close()


def test_frame_queue_keeps_only_latest_two_per_camera():
    class Slot:
        pass

    first, second = Slot(), Slot()
    frames = DroppingFrameQueue(2)
    assert frames.put_latest((first, "old", 1.0, 0, 7)) is None
    assert frames.put_latest((second, "other", 2.0, 0, 3)) is None
    assert frames.put_latest((first, "middle", 3.0, 0, 7)) is None
    assert frames.put_latest((first, "new", 4.0, 0, 8)) is first
    assert frames.qsize(first) == 2
    assert frames.qsize(second) == 1
    queued = [frames.get(0.01)[1] for _ in range(3)]
    assert queued == ["other", "middle", "new"]


def test_stale_inference_from_previous_source_is_not_applied():
    processor = CameraProcessor.__new__(CameraProcessor)
    processor._lock = threading.RLock()
    processor._source_generation = 2
    processor._last_inference_generation = -1
    processor.inferences = 0
    processor.inference_times = deque(maxlen=10)
    processor.frame_latencies = deque(maxlen=10)
    processor.latest_detections = []
    processor.dropped_frames = 0
    processor.running = True
    processor.tracker = LineTracker()
    processor.options = ProcessorOptions()
    processor.settings = make_settings()
    processor.total = 0
    processor.per_color = {}
    processor.confidence_sums = {}
    frame = SimpleNamespace(shape=(100, 100, 3))
    detection = Detection(40, 30, 60, 50, 0.9, "Red_50")

    processor.apply_inference(frame, time.monotonic(), [detection], 1.0, 1)

    assert processor.latest_detections == []
    assert processor._last_inference_generation == -1
    assert processor.dropped_frames == 1


class FingerprintFrame:
    shape = (128, 128, 3)
    dtype = "uint8"

    def __init__(self, payload: bytes):
        self.payload = payload

    def __getitem__(self, _key):
        return self

    def tobytes(self):
        return self.payload

    def copy(self):
        return FingerprintFrame(self.payload)


def test_accounting_epoch_discards_blocked_read_and_reopens_capture(
    monkeypatch,
):
    first_read_entered = threading.Event()
    release_first_read = threading.Event()
    captures = []
    submitted = []

    class FakeCapture:
        def __init__(self, index):
            self.index = index
            self.reads = 0
            self.released = False

        def set(self, *_args):
            return True

        def isOpened(self):
            return True

        def read(self):
            self.reads += 1
            if self.index == 0:
                first_read_entered.set()
                assert release_first_read.wait(timeout=2)
                return True, FingerprintFrame(bytes((16, 16, 16)))
            if self.reads == 1:
                return True, FingerprintFrame(bytes((32, 32, 32)))
            return True, FingerprintFrame(bytes((64, 64, 64)))

        def release(self):
            self.released = True

    def video_capture(*_args):
        capture = FakeCapture(len(captures))
        captures.append(capture)
        return capture

    fake_cv2 = SimpleNamespace(
        VideoCapture=video_capture,
        CAP_FFMPEG=1,
        CAP_PROP_OPEN_TIMEOUT_MSEC=2,
        CAP_PROP_READ_TIMEOUT_MSEC=3,
        CAP_PROP_BUFFERSIZE=4,
    )
    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)

    processor = CameraProcessor.__new__(CameraProcessor)
    processor.settings = make_settings(inference_fps=1000.0)
    processor.camera = "cam2"
    processor.source_stream = "cam2sub"
    processor._source_generation = 0
    processor._accounting_generation = 0
    processor._last_frame_generation = -1
    processor._last_frame_accounting_generation = -1
    processor._last_inference_generation = -1
    processor._last_inference_accounting_generation = -1
    processor._last_inference_submit = 0.0
    processor._lock = threading.RLock()
    processor._stop = threading.Event()
    processor.mode = "session"
    processor.frames_seen = 0
    processor.dropped_frames = 0
    processor.camera_reconnects = 0
    processor.last_error = ""
    processor.last_frame_at = None
    processor.publisher = SimpleNamespace(write=lambda _frame: True)
    processor._annotate = lambda frame: frame

    class RecordingManager:
        def submit(self, *args):
            submitted.append(args)
            processor._stop.set()

    processor.manager = RecordingManager()
    decoder = threading.Thread(target=processor._decoder_loop)
    decoder.start()
    assert first_read_entered.wait(timeout=1)
    with processor._lock:
        processor._advance_accounting_generation_locked()
    release_first_read.set()
    decoder.join(timeout=2)

    assert not decoder.is_alive()
    assert len(captures) == 2
    assert captures[0].released is True
    assert captures[1].released is True
    assert len(submitted) == 1
    _processor, _frame, _captured_at, source_epoch, accounting_epoch = (
        submitted[0]
    )
    assert source_epoch == 0
    assert accounting_epoch == 1
    assert processor.frames_seen == 1
    assert processor.dropped_frames >= 1


class CountEveryDetection:
    def reset(self):
        return None

    def update(self, detections, *_args):
        return detections


def _accounting_processor():
    processor = _overlay_processor()
    processor.manager = SimpleNamespace()
    processor.camera = "cam2"
    processor.mode = "session"
    processor.source_stream = processor.settings.source_stream("cam2", "sub")
    processor.publisher = SimpleNamespace(resume=lambda: None, pause=lambda: None)
    processor._accounting_generation = 0
    processor._last_frame_accounting_generation = -1
    processor._last_inference_accounting_generation = -1
    processor.tracker = CountEveryDetection()
    return processor


@pytest.mark.parametrize("boundary", ["start", "reset", "handoff"])
def test_accounting_generation_fences_queued_results_across_boundaries(boundary):
    processor = _accounting_processor()
    queued_generation = processor._accounting_generation

    if boundary == "start":
        processor.running = False
        processor.mode = "idle"
        processor.start_session(ProcessorOptions())
    elif boundary == "reset":
        processor.total = 9
        processor.reset()
    else:
        processor.start_always_on(
            ProcessorOptions(), force_session_handoff=True,
        )

    processor.apply_inference(
        SimpleNamespace(shape=(100, 100, 3)),
        time.monotonic(),
        [Detection(10, 10, 20, 20, 0.9, "Red_50")],
        1.0,
        source_generation=processor._source_generation,
        accounting_generation=queued_generation,
    )

    assert processor._accounting_generation == queued_generation + 1
    assert processor.total == 0
    assert processor.latest_detections == []
    assert processor.dropped_frames == 1


def test_line_tracker_counts_one_crossing_per_track():
    tracker = LineTracker()
    shape = (100, 100, 3)
    line = (0.0, 0.5, 1.0, 0.5)
    before = Detection(40, 30, 60, 50, 0.9, "Red_50")
    after = Detection(40, 50, 60, 70, 0.8, "Red_50")
    assert tracker.update([before], line, "any", shape) == []
    assert tracker.update([after], line, "any", shape) == [after]
    assert tracker.update([before], line, "any", shape) == []


@pytest.mark.parametrize(
    ("direction", "before_y", "after_y", "counted"),
    [
        ("up", 65, 35, True),
        ("up", 35, 65, False),
        ("down", 35, 65, True),
        ("down", 65, 35, False),
    ],
)
def test_line_tracker_supports_absolute_vertical_directions(
    direction, before_y, after_y, counted,
):
    tracker = LineTracker()
    shape = (100, 100, 3)
    line = (0.0, 0.5, 1.0, 0.5)
    before = Detection(45, before_y - 5, 55, before_y + 5, 0.9, "Red_50")
    after = Detection(45, after_y - 5, 55, after_y + 5, 0.9, "Red_50")

    assert tracker.update([before], line, direction, shape) == []
    result = tracker.update([after], line, direction, shape)

    assert bool(result) is counted


def test_settings_reject_plaintext_key_and_parsers_are_strict(monkeypatch):
    monkeypatch.setenv("AI_SERVICE_API_KEY_SHA256", DIGEST)
    monkeypatch.setenv("AI_SERVICE_API_KEY", KEY)
    with pytest.raises(ValueError, match="plaintext"):
        Settings.from_env()
    monkeypatch.delenv("AI_SERVICE_API_KEY")
    assert Settings.from_env().api_key_sha256 == DIGEST
    assert parse_camera("cam12") == "cam12"
    assert parse_line("0,0.5,1,0.5") == (0.0, 0.5, 1.0, 0.5)
    with pytest.raises(ValueError):
        parse_line("0,0,0,0")
    validate_classes(["Red_50", "White_25"])
    with pytest.raises(RuntimeError, match="color/weight"):
        validate_classes(["bag"])


# ── Рамки детекций для оверлея в браузере ─────────────────────────────────
# Всегда-включённый режим не публикует аннотированное видео, поэтому рамки
# отдаются координатами, а рисует их интерфейс поверх обычного потока.


def _overlay_processor(*, running=True):
    processor = CameraProcessor.__new__(CameraProcessor)
    processor._lock = threading.RLock()
    processor._source_generation = 1
    processor._last_inference_generation = -1
    processor.inferences = 0
    processor.inference_times = deque(maxlen=10)
    processor.frame_latencies = deque(maxlen=10)
    processor.latest_detections = []
    processor.latest_frame_shape = None
    processor.latest_counted = frozenset()
    processor.dropped_frames = 0
    processor.running = running
    processor.tracker = LineTracker()
    processor.options = ProcessorOptions()
    processor.settings = make_settings()
    processor.total = 0
    processor.per_color = defaultdict(int)
    processor.confidence_sums = defaultdict(float)
    return processor


def test_live_line_update_resets_tracks_but_preserves_count():
    processor = _overlay_processor()
    processor.options = ProcessorOptions(line="0,0.5,1,0.5")
    processor.total = 17
    processor.tracker.update(
        [Detection(40, 30, 60, 45, 0.9, "Red_50")],
        (0.0, 0.5, 1.0, 0.5),
        "any",
        (100, 100, 3),
    )
    assert processor.tracker.tracks

    processor.update_counting_line("0.1,0.2,0.8,0.9", "down")

    assert processor.total == 17
    assert processor.tracker.tracks == {}
    assert processor.options.line == "0.1,0.2,0.8,0.9"
    assert processor.options.direction == "down"


def test_live_line_update_reads_options_under_processor_lock():
    class TrackingRLock:
        def __init__(self):
            self.lock = threading.RLock()
            self.owner = None
            self.depth = 0

        def __enter__(self):
            self.lock.acquire()
            self.owner = threading.get_ident()
            self.depth += 1
            return self

        def __exit__(self, *_args):
            self.depth -= 1
            if self.depth == 0:
                self.owner = None
            self.lock.release()

        def owned_by_current_thread(self):
            return self.owner == threading.get_ident()

    class LockAwareOptions:
        source = "main"
        line = "0,0.5,1,0.5"
        direction = "any"

        def __init__(self, lock):
            self.lock = lock

        def model_copy(self, *, update):
            assert self.lock.owned_by_current_thread()
            return ProcessorOptions(
                source=self.source,
                line=update["line"],
                direction=update["direction"],
            )

    processor = _overlay_processor()
    tracking_lock = TrackingRLock()
    processor._lock = tracking_lock
    processor.options = LockAwareOptions(tracking_lock)

    processor.update_counting_line("0.1,0.2,0.8,0.9", "up")

    assert processor.options.source == "main"
    assert processor.options.line == "0.1,0.2,0.8,0.9"


def test_detection_overlay_uses_fractions_of_the_frame():
    """Координаты в долях: разрешение камеры меняется, а вёрстка — нет."""
    processor = _overlay_processor()
    frame = SimpleNamespace(shape=(200, 400, 3))
    detection = Detection(40, 30, 120, 80, 0.912, "Red_50")

    processor.apply_inference(frame, time.monotonic(), [detection], 1.0, 1)
    overlay = processor._detection_overlay()

    assert overlay == [{
        "x": 0.1, "y": 0.15, "w": 0.2, "h": 0.25,
        "label": "Red_50", "confidence": 0.912, "counted": False,
    }]


def test_detection_overlay_marks_the_bag_that_was_counted():
    """Засчитанный мешок помечен — по нему видно работу счётчика."""
    processor = _overlay_processor()
    frame = SimpleNamespace(shape=(100, 100, 3))
    processor.options = ProcessorOptions(line="0,0.5,1,0.5", direction="any")

    processor.apply_inference(
        frame, time.monotonic(), [Detection(40, 30, 60, 45, 0.9, "Red_50")], 1.0, 1)
    assert processor._detection_overlay()[0]["counted"] is False

    crossed = Detection(40, 55, 60, 70, 0.9, "Red_50")
    processor.apply_inference(frame, time.monotonic(), [crossed], 1.0, 1)

    assert processor.total == 1
    assert processor._detection_overlay()[0]["counted"] is True


def test_real_crossing_retries_lost_commit_ack_without_new_frame(
    tmp_path, monkeypatch,
):
    journal = CountEventJournal(tmp_path / "count-events.sqlite3")

    class LostCommitAcknowledgementOnce:
        def __init__(self):
            self.calls = []

        def append(self, count_event):
            event_id = journal.append(count_event)
            self.calls.append(count_event.event_key)
            if len(self.calls) == 1:
                raise JournalTransientError(
                    "simulated lost COMMIT acknowledgement"
                )
            return event_id

    proxy = LostCommitAcknowledgementOnce()
    manager = ProcessorManager.__new__(ProcessorManager)
    manager.event_journal = proxy
    manager._class_ids = {"Red_50": 0}
    sleeps = []
    monkeypatch.setattr("cv_service.processor.time.sleep", sleeps.append)
    processor = _overlay_processor()
    processor.camera = "cam3"
    processor.mode = "always_on"
    processor.manager = manager
    processor.options = ProcessorOptions(
        source="sub", line="0,0.5,1,0.5", direction="any",
    )
    frame = SimpleNamespace(shape=(100, 100, 3))
    try:
        processor.apply_inference(
            frame,
            time.monotonic(),
            [Detection(40, 30, 60, 45, 0.8, "Red_50")],
            1.0,
            1,
        )
        processor.apply_inference(
            frame,
            time.monotonic(),
            [Detection(40, 55, 60, 70, 0.9, "Red_50")],
            1.0,
            1,
        )

        assert processor.total == 1
        assert sleeps == [0.1]
        assert len(proxy.calls) == 2
        assert proxy.calls[0] == proxy.calls[1]
        page = journal.page(after_id=0, limit=500, cam="cam3")
        assert len(page["events"]) == 1
        assert page["events"][0] == {
            "id": 1,
            "created_at": page["events"][0]["created_at"],
            "cam": "cam3",
            "source": "sub",
            "mode": "always_on",
            "generation": 0,
            "frame": 2,
            "track_id": 1,
            "class_id": 0,
            "class_name": "Red_50",
            "confidence": 0.9,
            "direction": "positive",
            "point_x": 50.0,
            "point_y": 62.5,
            "weight_kg": 50.0,
            "total_after": 1,
            "total_weight_after": 50.0,
        }
    finally:
        journal.close()


def test_session_handoff_appends_new_generation_without_mutating_old_event(
    tmp_path,
):
    journal = CountEventJournal(tmp_path / "count-events.sqlite3")
    manager = ProcessorManager.__new__(ProcessorManager)
    manager.event_journal = journal
    manager._class_ids = {"Red_50": 0}
    processor = _overlay_processor()
    processor.manager = manager
    processor.camera = "cam3"
    processor.mode = "session"
    processor._accounting_generation = 0
    processor.source_stream = processor.settings.source_stream("cam3", "sub")
    processor.publisher = SimpleNamespace(pause=lambda: None, resume=lambda: None)
    processor.options = ProcessorOptions(
        source="sub", line="0,0.5,1,0.5", direction="any",
    )
    frame = SimpleNamespace(shape=(100, 100, 3))

    def cross_line():
        processor.apply_inference(
            frame,
            time.monotonic(),
            [Detection(40, 30, 60, 45, 0.8, "Red_50")],
            1.0,
            1,
        )
        processor.apply_inference(
            frame,
            time.monotonic(),
            [Detection(40, 55, 60, 70, 0.9, "Red_50")],
            1.0,
            1,
        )

    try:
        cross_line()
        first_before_handoff = dict(
            journal.page(after_id=0, limit=500, cam="cam3")["events"][0]
        )

        processor.start_always_on(
            ProcessorOptions(
                source="sub", line="0,0.5,1,0.5", direction="any",
            ),
            force_session_handoff=True,
        )
        assert processor.total == 0
        cross_line()

        events = journal.page(after_id=0, limit=500, cam="cam3")["events"]
        assert len(events) == 2
        assert events[0] == first_before_handoff
        assert [
            (item["id"], item["mode"], item["generation"], item["total_after"])
            for item in events
        ] == [
            (1, "session", 0, 1),
            (2, "always_on", 1, 1),
        ]
    finally:
        journal.close()


def test_journal_failure_leaves_crossing_retryable_and_count_unchanged(tmp_path):
    journal = CountEventJournal(tmp_path / "count-events.sqlite3")
    processor = _overlay_processor()
    processor.camera = "cam3"
    processor.mode = "always_on"
    processor.manager = SimpleNamespace(
        class_id_for=lambda _class_name: 0,
        record_count_event=journal.append,
    )
    frame = SimpleNamespace(shape=(100, 100, 3))
    before = Detection(40, 30, 60, 45, 0.8, "Red_50")
    crossed = Detection(40, 55, 60, 70, 0.9, "Red_50")
    try:
        journal._connection.execute(
            """
            CREATE TRIGGER fail_count_event_insert
            BEFORE INSERT ON count_events
            BEGIN SELECT RAISE(ABORT, 'simulated full disk'); END
            """
        )
        processor.apply_inference(
            frame, time.monotonic(), [before], 1.0, 1,
        )
        with pytest.raises(RuntimeError, match="simulated full disk"):
            processor.apply_inference(
                frame, time.monotonic(), [crossed], 1.0, 1,
            )

        assert processor.total == 0
        assert journal.page(after_id=0, limit=500)["events"] == []
        assert journal.health()["available"] is False
        assert "simulated full disk" in journal.health()["error"]
        assert all(not track.counted for track in processor.tracker.tracks.values())

        journal._connection.execute("DROP TRIGGER fail_count_event_insert")
        processor.apply_inference(
            frame, time.monotonic(), [crossed], 1.0, 1,
        )
        assert processor.total == 1
        assert len(journal.page(after_id=0, limit=500)["events"]) == 1
        assert journal.health()["available"] is True
    finally:
        journal.close()


def test_detection_overlay_is_empty_before_the_first_frame():
    assert _overlay_processor()._detection_overlay() == []


def test_paused_processor_still_reports_boxes_but_counts_nothing():
    """Модель видно и на паузе: рамки есть, счёт не идёт."""
    processor = _overlay_processor(running=False)
    frame = SimpleNamespace(shape=(100, 100, 3))

    processor.apply_inference(
        frame, time.monotonic(), [Detection(10, 10, 30, 30, 0.8, "Red_50")], 1.0, 1)
    overlay = processor._detection_overlay()

    assert len(overlay) == 1
    assert overlay[0]["counted"] is False
    assert processor.total == 0


def test_stale_frame_leaves_the_overlay_untouched():
    """Кадр от прошлого источника не должен подменить рамки нового."""
    processor = _overlay_processor()
    processor._source_generation = 2
    frame = SimpleNamespace(shape=(100, 100, 3))

    processor.apply_inference(
        frame, time.monotonic(), [Detection(10, 10, 30, 30, 0.8, "Red_50")], 1.0, 1)

    assert processor._detection_overlay() == []
