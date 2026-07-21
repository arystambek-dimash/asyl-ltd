from __future__ import annotations

import math
import os
import subprocess
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from .contracts import Detection, ProcessorOptions
from .settings import Settings, parse_camera, parse_line


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def percentile(values: deque[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * fraction) - 1))
    return round(ordered[index], 2)


class FfmpegPublisher:
    def __init__(self, settings: Settings, stream: str, encoder: str):
        self.settings = settings
        self.stream = stream
        self.encoder = encoder
        self.process: subprocess.Popen | None = None
        self.size: tuple[int, int] | None = None
        self.state = "waiting"
        self._condition = threading.Condition()
        self._latest_frame: Any | None = None
        self._stop = threading.Event()
        self._enabled = threading.Event()
        self._process_lock = threading.RLock()
        self._thread = threading.Thread(
            target=self._publisher_loop,
            name=f"publisher-{stream}",
            daemon=True,
        )
        self._thread.start()

    def _start(self, width: int, height: int) -> None:
        destination = f"{self.settings.mediamtx_rtsp_url}/{self.stream}"
        encoder_args = ["-preset", "veryfast", "-tune", "zerolatency"]
        if self.encoder == "h264_nvenc":
            encoder_args = ["-preset", "p1", "-tune", "ull"]
        elif self.encoder == "h264_qsv":
            encoder_args = ["-preset", "veryfast"]
        command = [
            self.settings.ffmpeg_path,
            "-hide_banner", "-loglevel", "warning", "-nostdin",
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{width}x{height}", "-r", str(self.settings.output_fps),
            "-i", "pipe:0", "-an", "-c:v", self.encoder,
            *encoder_args,
            "-pix_fmt", "yuv420p", "-g", str(max(1, int(self.settings.output_fps))),
            # Make a stalled RTSP destination terminate FFmpeg instead of
            # blocking this camera's publisher forever. The next output frame
            # then starts a fresh process without affecting other cameras.
            "-rw_timeout", str(self.settings.capture_timeout_ms * 1000),
            "-f", "rtsp", "-rtsp_transport", "tcp", destination,
        ]
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.size = (width, height)
        self.state = "connecting"

    def write(self, frame: Any) -> bool:
        """Queue only the latest output frame; never block the camera decoder."""
        if not self._enabled.is_set():
            return False
        with self._condition:
            self._latest_frame = frame
            self._condition.notify()
        return True

    def _write_frame(self, frame: Any) -> bool:
        if not self._enabled.is_set():
            return False
        with self._process_lock:
            height, width = frame.shape[:2]
            if self.process is None or self.process.poll() is not None or self.size != (width, height):
                self._close_process()
                try:
                    self._start(width, height)
                except OSError:
                    self.state = "error"
                    return False
            try:
                assert self.process and self.process.stdin
                self.process.stdin.write(frame.tobytes())
                self.state = "connected"
                return True
            except (BrokenPipeError, OSError):
                self.state = "reconnecting"
                self._close_process()
                return False

    def _publisher_loop(self) -> None:
        while not self._stop.is_set():
            with self._condition:
                while self._latest_frame is None and not self._stop.is_set():
                    self._condition.wait(0.5)
                frame, self._latest_frame = self._latest_frame, None
            if frame is not None:
                try:
                    self._write_frame(frame)
                except Exception:
                    # One malformed frame or subprocess race must not kill the
                    # persistent per-camera publisher thread.
                    self.state = "reconnecting"
                    self._close_process()
                    self._stop.wait(0.25)

    @property
    def alive(self) -> bool:
        return self._thread.is_alive() and not self._stop.is_set()

    def _close_process(self) -> None:
        with self._process_lock:
            process, self.process = self.process, None
            self.size = None
            if process is None:
                return
            try:
                if process.stdin:
                    process.stdin.close()
                process.terminate()
                process.wait(timeout=2)
            except (OSError, subprocess.SubprocessError):
                try:
                    process.kill()
                except OSError:
                    pass

    def resume(self) -> None:
        self._enabled.set()
        if self.state == "paused":
            self.state = "waiting"

    def pause(self) -> None:
        self._enabled.clear()
        with self._condition:
            self._latest_frame = None
        self._close_process()
        self.state = "paused"

    def close(self) -> None:
        self.pause()
        self._stop.set()
        with self._condition:
            self._condition.notify_all()
        self._close_process()
        self._thread.join(timeout=3)


@dataclass
class Track:
    identifier: int
    label: str
    center: tuple[float, float]
    side: float
    last_seen: int
    counted: bool = False


class LineTracker:
    def __init__(self):
        self.tracks: dict[int, Track] = {}
        self.next_id = 1
        self.frame_index = 0

    def reset(self) -> None:
        self.tracks.clear()
        self.next_id = 1
        self.frame_index = 0

    @staticmethod
    def _side(center: tuple[float, float], line: tuple[float, float, float, float], shape) -> float:
        height, width = shape[:2]
        x1, y1, x2, y2 = line
        ax, ay, bx, by = x1 * width, y1 * height, x2 * width, y2 * height
        return (bx - ax) * (center[1] - ay) - (by - ay) * (center[0] - ax)

    def update(
        self,
        detections: list[Detection],
        line: tuple[float, float, float, float],
        direction: str,
        shape,
    ) -> list[Detection]:
        self.frame_index += 1
        counted: list[Detection] = []
        available = set(self.tracks)
        diagonal = math.hypot(shape[1], shape[0])
        max_distance = max(30.0, diagonal * 0.08)
        for detection in detections:
            center = detection.center
            candidates = [
                track for key, track in self.tracks.items()
                if key in available and track.label == detection.label
            ]
            match = min(
                candidates,
                key=lambda track: math.dist(track.center, center),
                default=None,
            )
            side = self._side(center, line, shape)
            if match is None or math.dist(match.center, center) > max_distance:
                match = Track(self.next_id, detection.label, center, side, self.frame_index)
                self.tracks[match.identifier] = match
                self.next_id += 1
            else:
                available.discard(match.identifier)
                crossed = match.side != 0 and side != 0 and (match.side > 0) != (side > 0)
                movement = "positive" if match.side < side else "negative"
                if crossed and not match.counted and (direction == "any" or direction == movement):
                    match.counted = True
                    counted.append(detection)
                match.center = center
                match.side = side
                match.last_seen = self.frame_index
        for key in [key for key, track in self.tracks.items() if self.frame_index - track.last_seen > 20]:
            del self.tracks[key]
        return counted


class CameraProcessor:
    def __init__(
        self,
        manager: "ProcessorManager",
        camera: str,
        options: ProcessorOptions,
    ):
        self.manager = manager
        self.settings = manager.settings
        self.camera = parse_camera(camera)
        self.stream = f"{camera}ai"
        self.options = options
        self.source_stream = self.settings.source_stream(camera, options.source)
        self.publisher = manager.publisher_factory(self.settings, self.stream, manager.encoder)
        self.tracker = LineTracker()
        self.running = False
        self.mode = "idle"
        self.total = 0
        self.per_color: dict[str, int] = defaultdict(int)
        self.confidence_sums: dict[str, float] = defaultdict(float)
        self.last_frame_at: str | None = None
        self.last_error = ""
        self.dropped_frames = 0
        self.camera_reconnects = 0
        self.frames_seen = 0
        self.inferences = 0
        self.started_monotonic = time.monotonic()
        self.inference_times: deque[float] = deque(maxlen=240)
        self.frame_latencies: deque[float] = deque(maxlen=240)
        self.latest_detections: list[Detection] = []
        self._last_inference_submit = 0.0
        self._source_generation = 0
        self._last_frame_generation = -1
        self._last_inference_generation = -1
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._decoder_thread = threading.Thread(
            target=self._decoder_loop,
            name=f"decoder-{camera}",
            daemon=True,
        )
        self._decoder_thread.start()

    def configure(self, options: ProcessorOptions) -> None:
        source_stream = self.settings.source_stream(self.camera, options.source)
        with self._lock:
            if source_stream != self.source_stream:
                self.source_stream = source_stream
                self._source_generation += 1
            self.options = options

    def start_session(self, options: ProcessorOptions) -> None:
        self.configure(options)
        with self._lock:
            self.tracker.reset()
            self.total = 0
            self.per_color.clear()
            self.confidence_sums.clear()
            self.running = True
            self.mode = "session"
            self.publisher.resume()

    def start_always_on(
        self, options: ProcessorOptions, *, force_session_handoff: bool = False
    ) -> None:
        self.configure(options)
        with self._lock:
            if self.mode == "session" and not force_session_handoff:
                return
            if self.mode != "always_on" or force_session_handoff:
                self.tracker.reset()
                self.total = 0
                self.per_color.clear()
                self.confidence_sums.clear()
            self.running = True
            self.mode = "always_on"
            self.publisher.pause()

    def wait_until_warm(self) -> None:
        deadline = time.monotonic() + self.settings.prewarm_timeout
        while time.monotonic() < deadline:
            with self._lock:
                generation = self._source_generation
                source_ready = (
                    self._last_frame_generation == generation
                    and self._last_inference_generation == generation
                )
            if (
                self._decoder_thread.is_alive()
                and self.publisher.alive
                and source_ready
            ):
                return
            if not self._decoder_thread.is_alive():
                break
            time.sleep(0.05)
        raise RuntimeError(
            f"processor {self.camera} did not become warm: "
            f"frame={self.last_frame_at is not None}, inferences={self.inferences}, "
            f"publisher={self.publisher.state}, error={self.last_error or 'none'}"
        )

    def reset(self) -> None:
        with self._lock:
            if not self.running:
                raise ValueError("processor is not counting")
            self.tracker.reset()
            self.total = 0
            self.per_color.clear()
            self.confidence_sums.clear()

    def idle(self) -> None:
        with self._lock:
            self.running = False
            self.mode = "idle"
            self.tracker.reset()
            self.publisher.pause()

    def close(self) -> None:
        self._stop.set()
        self._decoder_thread.join(timeout=3)
        self.publisher.close()

    def mark_dropped(self) -> None:
        with self._lock:
            self.dropped_frames += 1

    def apply_inference(
        self,
        frame: Any,
        captured_at: float,
        detections: list[Detection],
        elapsed_ms: float,
        source_generation: int,
    ) -> None:
        with self._lock:
            self.inferences += 1
            self.inference_times.append(elapsed_ms)
            self.frame_latencies.append((time.monotonic() - captured_at) * 1000)
            if source_generation != self._source_generation:
                # The operator switched sub/main while this frame waited in
                # the shared inference queue. Never feed an old-source result
                # into the new source's tracker or overlay.
                self.dropped_frames += 1
                return
            self._last_inference_generation = source_generation
            self.latest_detections = detections
            if not self.running:
                return
            counted = self.tracker.update(
                detections,
                parse_line(self.options.line or self.settings.default_line),
                self.options.direction,
                frame.shape,
            )
            for detection in counted:
                self.total += 1
                self.per_color[detection.label] += 1
                self.confidence_sums[detection.label] += detection.confidence

    def _annotate(self, frame: Any) -> Any:
        try:
            import cv2
        except ImportError:
            return frame
        with self._lock:
            detections = list(self.latest_detections)
            total = self.total
            options = self.options
            running = self.running
        output = frame.copy()
        for item in detections:
            cv2.rectangle(output, (int(item.x1), int(item.y1)), (int(item.x2), int(item.y2)), (55, 190, 95), 2)
            cv2.putText(output, f"{item.label} {item.confidence:.2f}", (int(item.x1), max(18, int(item.y1) - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (55, 190, 95), 2)
        height, width = output.shape[:2]
        x1, y1, x2, y2 = parse_line(options.line or self.settings.default_line)
        cv2.line(output, (int(x1 * width), int(y1 * height)), (int(x2 * width), int(y2 * height)), (52, 103, 235), 2)
        cv2.putText(output, f"{'COUNTING' if running else 'IDLE'}  total={total}", (16, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2)
        return output

    def _decoder_loop(self) -> None:
        os.environ.setdefault(
            "OPENCV_FFMPEG_CAPTURE_OPTIONS",
            f"rtsp_transport;tcp|stimeout;{self.settings.capture_timeout_ms * 1000}",
        )
        try:
            import cv2
        except ImportError:
            self.last_error = "opencv-python is not installed"
            return
        capture = None
        generation = -1
        last_output = 0.0
        while not self._stop.is_set():
            with self._lock:
                current_generation = self._source_generation
                source_stream = self.source_stream
                publishing = self.mode == "session"
            if capture is None or current_generation != generation:
                if capture is not None:
                    capture.release()
                capture = cv2.VideoCapture(
                    f"{self.settings.mediamtx_rtsp_url}/{source_stream}",
                    cv2.CAP_FFMPEG,
                    [
                        cv2.CAP_PROP_OPEN_TIMEOUT_MSEC,
                        self.settings.capture_timeout_ms,
                        cv2.CAP_PROP_READ_TIMEOUT_MSEC,
                        self.settings.capture_timeout_ms,
                    ],
                )
                capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                generation = current_generation
                if not capture.isOpened():
                    self.camera_reconnects += 1
                    self.last_error = f"cannot open {source_stream}"
                    capture.release()
                    capture = None
                    self._stop.wait(1.0)
                    continue
            ok, frame = capture.read()
            captured_at = time.monotonic()
            if not ok:
                self.camera_reconnects += 1
                self.last_error = f"lost {source_stream}"
                capture.release()
                capture = None
                self._stop.wait(0.5)
                continue
            with self._lock:
                self.last_error = ""
                self.frames_seen += 1
                self.last_frame_at = utc_now()
                self._last_frame_generation = generation
            if captured_at - self._last_inference_submit >= 1 / self.settings.inference_fps:
                self._last_inference_submit = captured_at
                self.manager.submit(self, frame.copy(), captured_at, generation)
            if publishing and captured_at - last_output >= 1 / self.settings.output_fps:
                last_output = captured_at
                self.publisher.write(self._annotate(frame))
        if capture is not None:
            capture.release()

    def status(self) -> dict:
        elapsed = max(0.001, time.monotonic() - self.started_monotonic)
        with self._lock:
            alive = (
                self._decoder_thread.is_alive()
                and self.manager._worker.is_alive()
                and self.publisher.alive
            )
            warm = (
                not self.running
                and alive
                and self._last_frame_generation == self._source_generation
                and self._last_inference_generation == self._source_generation
            )
            return {
                "cam": self.camera,
                "running": self.running,
                "mode": self.mode,
                "recording": self.mode == "session",
                "processor_alive": alive,
                "warm": warm,
                "stream": self.stream,
                "source": self.options.source,
                "line": self.options.line or self.settings.default_line,
                "direction": self.options.direction,
                "total": self.total,
                "per_color": dict(self.per_color),
                "confidence_sums": {key: round(value, 3) for key, value in self.confidence_sums.items()},
                "last_frame_at": self.last_frame_at,
                "error": self.last_error or None,
                "metrics": {
                    "camera_fps": round(self.frames_seen / elapsed, 2),
                    "inference_fps": round(self.inferences / elapsed, 2),
                    "inference_avg_ms": round(sum(self.inference_times) / len(self.inference_times), 2) if self.inference_times else 0.0,
                    "inference_p95_ms": percentile(self.inference_times, 0.95),
                    "frame_latency_p95_ms": percentile(self.frame_latencies, 0.95),
                    "dropped_frames": self.dropped_frames,
                    "queue_depth": self.manager.queue.qsize(self),
                    "camera_reconnects": self.camera_reconnects,
                    "publisher_state": self.publisher.state,
                },
                "model": self.manager.model.metadata(),
            }


class DroppingFrameQueue:
    """At most 1–2 latest frames per camera; overload removes the oldest."""

    def __init__(self, per_camera_size: int):
        self.per_camera_size = per_camera_size
        self._items: deque[tuple[CameraProcessor, Any, float, int]] = deque()
        self._condition = threading.Condition()

    def put_latest(
        self, item: tuple[CameraProcessor, Any, float, int]
    ) -> CameraProcessor | None:
        processor = item[0]
        dropped = None
        with self._condition:
            indexes = [
                index for index, queued in enumerate(self._items)
                if queued[0] is processor
            ]
            if len(indexes) >= self.per_camera_size:
                dropped = self._items[indexes[0]][0]
                del self._items[indexes[0]]
            self._items.append(item)
            self._condition.notify()
        return dropped

    def get(self, timeout: float) -> tuple[CameraProcessor, Any, float, int]:
        deadline = time.monotonic() + timeout
        with self._condition:
            while not self._items:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                self._condition.wait(remaining)
            return self._items.popleft()

    def qsize(self, processor: CameraProcessor | None = None) -> int:
        with self._condition:
            if processor is None:
                return len(self._items)
            return sum(item[0] is processor for item in self._items)


class ProcessorManager:
    def __init__(
        self,
        settings: Settings,
        model,
        mediamtx,
        encoder: str,
        processor_factory: Callable[..., CameraProcessor] = CameraProcessor,
        publisher_factory: Callable[..., FfmpegPublisher] = FfmpegPublisher,
        state_store=None,
    ):
        self.settings = settings
        self.model = model
        self.mediamtx = mediamtx
        self.encoder = encoder
        self.processor_factory = processor_factory
        self.publisher_factory = publisher_factory
        self.state_store = state_store
        self.processors: dict[str, CameraProcessor] = {}
        self.always_on_cameras: set[str] = set()
        self.always_on_source = "sub"
        self.queue = DroppingFrameQueue(settings.queue_size)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._inference_loop, name="inference", daemon=True)
        self._worker.start()

    def submit(
        self,
        processor: CameraProcessor,
        frame: Any,
        captured_at: float,
        source_generation: int = 0,
    ) -> None:
        dropped = self.queue.put_latest(
            (processor, frame, captured_at, source_generation)
        )
        if dropped is not None:
            dropped.mark_dropped()

    def _inference_loop(self) -> None:
        while not self._stop.is_set():
            try:
                processor, frame, captured_at, source_generation = self.queue.get(timeout=0.2)
            except TimeoutError:
                continue
            started = time.perf_counter()
            try:
                detections = self.model.predict(frame)
                processor.apply_inference(
                    frame,
                    captured_at,
                    detections,
                    (time.perf_counter() - started) * 1000,
                    source_generation,
                )
            except Exception as exc:  # worker must survive one corrupt frame
                processor.last_error = f"inference failed: {exc}"

    def _ensure(self, camera: str, options: ProcessorOptions) -> CameraProcessor:
        camera = parse_camera(camera)
        if options.line is None:
            options = options.model_copy(update={"line": self.settings.default_line})
        source_stream = self.settings.source_stream(camera, options.source)
        with self._lock:
            processor = self.processors.get(camera)
            if processor is None:
                if len(self.processors) >= self.settings.max_active_processors:
                    raise OverflowError("AI_MAX_ACTIVE_PROCESSORS limit reached")
                self.mediamtx.validate_source(camera, source_stream)
                processor = self.processor_factory(self, camera, options)
                self.processors[camera] = processor
            else:
                if processor.source_stream != source_stream:
                    self.mediamtx.validate_source(camera, source_stream)
                processor.configure(options)
            return processor

    def prewarm(self, camera: str, options: ProcessorOptions) -> dict:
        try:
            existing = self.get(camera)
        except KeyError:
            existing = None
        if existing is not None and existing.running:
            return existing.status()
        processor = self._ensure(camera, options)
        processor.wait_until_warm()
        return processor.status()

    def start(self, camera: str, options: ProcessorOptions) -> dict:
        try:
            existing = self.get(camera)
        except KeyError:
            existing = None
        if existing is not None and existing.running and existing.mode == "session":
            return existing.status()
        processor = self._ensure(camera, options)
        processor.start_session(processor.options)
        return processor.status()

    def reset(self, camera: str) -> dict:
        processor = self.get(camera)
        processor.reset()
        return processor.status()

    def idle(self, camera: str) -> dict:
        processor = self.get(camera)
        if camera in self.always_on_cameras:
            processor.start_always_on(
                ProcessorOptions(source=self.always_on_source),
                force_session_handoff=True,
            )
        else:
            processor.idle()
        return processor.status()

    def configure_always_on(
        self,
        cameras: list[str],
        source: str = "sub",
        *,
        persist: bool = True,
    ) -> dict:
        normalized = list(dict.fromkeys(parse_camera(item) for item in cameras))
        if source not in {"sub", "main"}:
            raise ValueError("source must be sub or main")
        for camera in normalized:
            self.mediamtx.validate_source(
                camera, self.settings.source_stream(camera, source)
            )
        with self._lock:
            session_cameras = {
                camera for camera, processor in self.processors.items()
                if processor.mode == "session"
            }
        if len(set(normalized) | session_cameras) > self.settings.max_active_processors:
            raise OverflowError("AI_MAX_ACTIVE_PROCESSORS limit reached")
        if persist and self.state_store is not None:
            self.state_store.save(normalized, source)

        self.always_on_cameras = set(normalized)
        self.always_on_source = source
        # Idle/prewarmed processors outside the desired set must not consume
        # capacity forever. Active order sessions are never retired here.
        with self._lock:
            retiring = [
                (camera, processor)
                for camera, processor in self.processors.items()
                if camera not in self.always_on_cameras
                and processor.mode != "session"
            ]
            for camera, _processor in retiring:
                self.processors.pop(camera, None)
        for _camera, processor in retiring:
            processor.idle()
            processor.close()
        for camera in normalized:
            processor = self._ensure(camera, ProcessorOptions(source=source))
            processor.start_always_on(processor.options)
        return self.always_on_status()

    def restore_always_on(self) -> dict:
        if self.state_store is None:
            return self.always_on_status()
        cameras, source = self.state_store.load()
        return self.configure_always_on(cameras, source, persist=False)

    def always_on_status(self) -> dict:
        statuses = []
        for camera in sorted(self.always_on_cameras):
            try:
                statuses.append(self.get(camera).status())
            except KeyError:
                statuses.append({
                    "cam": camera,
                    "running": False,
                    "mode": "always_on",
                    "recording": False,
                    "error": "processor is not running",
                })
        return {
            "cameras": sorted(self.always_on_cameras),
            "source": self.always_on_source,
            "capacity": self.settings.max_active_processors,
            "processors": statuses,
        }

    def get(self, camera: str) -> CameraProcessor:
        camera = parse_camera(camera)
        with self._lock:
            processor = self.processors.get(camera)
        if processor is None:
            raise KeyError(camera)
        return processor

    def statuses(self) -> list[dict]:
        with self._lock:
            processors = list(self.processors.values())
        return [processor.status() for processor in processors]

    def cameras(self) -> dict:
        inventory = self.mediamtx.camera_inventory()
        for camera, processor in self.processors.items():
            inventory.setdefault(camera, {"cam": camera})["ai"] = processor.status()
        return {
            "devices": self.mediamtx.device_inventory(),
            "cameras": [inventory[key] for key in sorted(inventory)],
        }

    def close(self) -> None:
        self._stop.set()
        self._worker.join(timeout=3)
        with self._lock:
            processors = list(self.processors.values())
        for processor in processors:
            processor.close()
