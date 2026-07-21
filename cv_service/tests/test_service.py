from __future__ import annotations

import hashlib
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from cv_service.app import create_app
from cv_service.contracts import Detection, ProcessorOptions
from cv_service.processor import (
    CameraProcessor,
    DroppingFrameQueue,
    LineTracker,
    ProcessorManager,
)
from cv_service.runtime import MediaMtxClient, select_h264_encoder, validate_classes
from cv_service.settings import Settings, parse_camera, parse_line
from cv_service.state import AlwaysOnStateStore


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

    def configure(self, options):
        self.options = options
        self.source_stream = self.manager.settings.source_stream(self.camera, options.source)

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

    def apply_inference(self, *_args):
        pass

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


def make_settings(max_processors=2):
    return Settings(
        api_key_sha256=DIGEST,
        model_path=Path("best.pt"),
        model_device="cpu",
        max_active_processors=max_processors,
    )


def make_manager(max_processors=2):
    return ProcessorManager(
        make_settings(max_processors),
        FakeModel(),
        FakeMediaMtx(),
        "libx264",
        processor_factory=FakeProcessor,
    )


@pytest.fixture
def service():
    manager = make_manager()
    with TestClient(create_app(manager)) as client:
        yield manager, client


def auth():
    return {"X-Api-Key": KEY}


@pytest.mark.parametrize(
    "path", ["/health", "/cameras", "/processors", "/always-on", "/processors/cam2"]
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
    assert "access-control-allow-origin" not in response.headers


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
    assert frames.put_latest((first, "old", 1.0, 0)) is None
    assert frames.put_latest((second, "other", 2.0, 0)) is None
    assert frames.put_latest((first, "middle", 3.0, 0)) is None
    assert frames.put_latest((first, "new", 4.0, 0)) is first
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


def test_line_tracker_counts_one_crossing_per_track():
    tracker = LineTracker()
    shape = (100, 100, 3)
    line = (0.0, 0.5, 1.0, 0.5)
    before = Detection(40, 30, 60, 50, 0.9, "Red_50")
    after = Detection(40, 50, 60, 70, 0.8, "Red_50")
    assert tracker.update([before], line, "any", shape) == []
    assert tracker.update([after], line, "any", shape) == [after]
    assert tracker.update([before], line, "any", shape) == []


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
