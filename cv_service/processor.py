from __future__ import annotations

import hashlib
import math
import os
import subprocess
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .cloud_conveyor import CloudConveyorObserver
from .contracts import ControlledSessionOptions, Detection, ProcessorOptions
from .conveyor import ConveyorConflictError, ConveyorSupervisor
from .event_journal import (
    CountEvent,
    CountEventJournal,
    JournalTransientError,
    class_weight_kg,
)
from .runtime import decode_jpeg
from .settings import Settings, parse_camera, parse_line
from .state import LINE_DIRECTIONS, line_payload


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def percentile(values: deque[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * fraction) - 1))
    return round(ordered[index], 2)


def frame_content_fingerprint(frame: Any) -> bytes:
    """Small perceptual fingerprint of the decoded conveyor image.

    The central sampled grid intentionally ignores low-bit codec noise. This
    is a fail-closed liveness signal, not object recognition: a static healthy
    scene can be rejected, while repeated black/frozen images cannot masquerade
    as fresh frames merely because ``VideoCapture.read`` returned again.
    """

    height, width = frame.shape[:2]
    if height <= 0 or width <= 0:
        raise ValueError("decoded frame has invalid dimensions")
    y_start, y_stop = height // 8, max(height // 8 + 1, height * 7 // 8)
    x_start, x_stop = width // 8, max(width // 8 + 1, width * 7 // 8)
    y_step = max(1, (y_stop - y_start) // 64)
    x_step = max(1, (x_stop - x_start) // 64)
    sample = frame[y_start:y_stop:y_step, x_start:x_stop:x_step]
    raw = sample.tobytes()
    quantized = bytes(value >> 4 for value in raw)
    digest = hashlib.blake2s(digest_size=16)
    digest.update(f"{height}x{width}:{getattr(frame, 'dtype', '')}".encode())
    digest.update(quantized)
    return digest.digest()


class FrameContentProgress:
    """Content-based liveness epoch with a monotonic progress sequence."""

    def __init__(self, stale_seconds: float):
        self.stale_seconds = stale_seconds
        self.sequence = 0
        self._epoch: tuple[int, int] | None = None
        self._fingerprint: bytes | None = None
        self._last_change_at: float | None = None

    def reset_epoch(self, epoch: tuple[int, int]) -> None:
        self._epoch = epoch
        self._fingerprint = None
        self._last_change_at = None

    def observe(
        self,
        epoch: tuple[int, int],
        fingerprint: bytes,
        captured_at: float,
    ) -> tuple[bool, int, str | None]:
        if epoch != self._epoch:
            self.reset_epoch(epoch)
        if self._fingerprint is None:
            self._fingerprint = fingerprint
            self._last_change_at = captured_at
            return (
                False,
                self.sequence,
                "camera source liveness pending: waiting for changed image content",
            )
        if fingerprint != self._fingerprint:
            self._fingerprint = fingerprint
            self._last_change_at = captured_at
            self.sequence += 1
            return True, self.sequence, None
        changed_at = (
            self._last_change_at
            if self._last_change_at is not None
            else captured_at
        )
        unchanged_for = max(0.0, captured_at - changed_at)
        if unchanged_for >= self.stale_seconds:
            return (
                False,
                self.sequence,
                "camera source frozen: repeated image content did not change",
            )
        return (
            False,
            self.sequence,
            "camera source liveness pending: repeated image content",
        )


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
                except Exception:  # noqa: BLE001 - persistent worker boundary
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


@dataclass(frozen=True)
class LineCrossing:
    detection: Detection
    track_id: int
    direction: str


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
        return self.update_with_callback(
            detections,
            line,
            direction,
            shape,
            before_count=None,
        )

    def update_with_callback(
        self,
        detections: list[Detection],
        line: tuple[float, float, float, float],
        direction: str,
        shape,
        *,
        before_count: Callable[[LineCrossing], None] | None,
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
                if direction in {"up", "down"}:
                    movement = (
                        "down" if center[1] > match.center[1]
                        else "up" if center[1] < match.center[1]
                        else ""
                    )
                else:
                    movement = "positive" if match.side < side else "negative"
                if crossed and not match.counted and (direction == "any" or direction == movement):
                    crossing = LineCrossing(
                        detection=detection,
                        track_id=match.identifier,
                        direction=movement,
                    )
                    # The durable journal owns the financial boundary. If its
                    # commit fails, leave this track on the pre-crossing side
                    # so a later frame can retry instead of losing the bag.
                    if before_count is not None:
                        before_count(crossing)
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
        manager: ProcessorManager,
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
        self.session_id: int | None = None
        self.target_total: int | None = None
        self.conveyor_transport = "direct"
        self.goal_reached = False
        self._session_ai_ready = threading.Event()
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
        # Кадр, к которому относятся latest_detections, и id засчитанных на
        # нём рамок. Нужны только для оверлея в браузере.
        self.latest_frame_shape: tuple[int, int] | None = None
        self.latest_counted: frozenset[int] = frozenset()
        self._last_inference_submit = 0.0
        self._source_generation = 0
        # Independent from the RTSP source generation: every accounting
        # boundary fences frames already captured or queued for inference.
        # Otherwise a result captured before START/RESET could be applied to
        # the freshly-zeroed counter (and could mark a controlled session AI
        # ready).
        self._accounting_generation = 0
        self._last_frame_generation = -1
        self._last_frame_accounting_generation = -1
        self._last_inference_generation = -1
        self._last_inference_accounting_generation = -1
        self._session_ai_captured_at: float | None = None
        self._frame_content_progress = FrameContentProgress(
            self.settings.conveyor_stale_ai_seconds,
        )
        self._accounting_progress_floor = 0
        self._last_control_progress_sequence = 0
        self._source_liveness_error = ""
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
                self._frame_content_progress.reset_epoch((
                    self._source_generation,
                    self._accounting_generation,
                ))
                self._source_liveness_error = ""
            self.options = options

    def update_counting_line(self, line: str, direction: str) -> None:
        """Apply an admin edit without resetting the accumulated total."""

        parse_line(line)
        if direction not in LINE_DIRECTIONS:
            raise ValueError("invalid counting-line direction")
        with self._lock:
            if (
                self.running
                and getattr(self, "mode", "idle") == "session"
                and self.session_id is not None
            ):
                raise ConveyorConflictError(
                    "counting line cannot change while a controlled session is active"
                )
            self.options = self.options.model_copy(update={
                "line": line,
                "direction": direction,
            })
            # Tracks observed against the old line must not cross the new one.
            self.tracker.reset()
            self._advance_accounting_generation_locked()

    def _advance_accounting_generation_locked(self) -> None:
        """Fence all frames captured before an accounting-state boundary."""

        self._accounting_generation = getattr(
            self, "_accounting_generation", 0,
        ) + 1
        frame_progress = getattr(self, "_frame_content_progress", None)
        progress_sequence = frame_progress.sequence if frame_progress is not None else 0
        self._accounting_progress_floor = progress_sequence
        self._last_control_progress_sequence = progress_sequence
        if frame_progress is not None:
            # Reopened capture must prove at least two distinct post-boundary
            # frames. A source switch/accounting reset is never itself motion.
            frame_progress.reset_epoch((
                self._source_generation,
                self._accounting_generation,
            ))
        self._last_inference_submit = 0.0
        self._source_liveness_error = ""
        self._last_frame_accounting_generation = -1
        self._last_inference_accounting_generation = -1
        self._session_ai_captured_at = None
        self._session_ai_ready.clear()

    def start_session(self, options: ProcessorOptions) -> None:
        self.configure(options)
        with self._lock:
            self._advance_accounting_generation_locked()
            self.tracker.reset()
            self.session_id = None
            self.target_total = None
            self.conveyor_transport = "direct"
            self.goal_reached = False
            self.total = 0
            self.per_color.clear()
            self.confidence_sums.clear()
            self.running = True
            self.mode = "session"
            self.publisher.resume()

    def start_controlled_session(
        self,
        options: ProcessorOptions,
        *,
        session_id: int,
        target_total: int,
        conveyor_transport: str = "direct",
    ) -> None:
        self.configure(options)
        with self._lock:
            self._advance_accounting_generation_locked()
            self.tracker.reset()
            self.session_id = session_id
            self.target_total = target_total
            self.conveyor_transport = conveyor_transport
            self.goal_reached = False
            self.total = 0
            self.per_color.clear()
            self.confidence_sums.clear()
            self.running = True
            self.mode = "session"
            self.publisher.resume()

    def wait_for_session_ai(self, session_id: int) -> None:
        deadline = time.monotonic() + self.settings.prewarm_timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not self._session_ai_ready.wait(timeout=remaining):
                with self._lock:
                    detail = (
                        getattr(self, "_source_liveness_error", "")
                        or getattr(self, "last_error", "")
                    )
                raise RuntimeError(
                    f"processor {self.camera} did not produce fresh inference "
                    f"for controlled session: {detail or 'source liveness not proven'}"
                )
            with self._lock:
                if self.session_id != session_id or self.mode != "session":
                    raise ConveyorConflictError(
                        "controlled session changed while waiting for AI"
                    )
                captured_at = self._session_ai_captured_at
                age = (
                    time.monotonic() - captured_at
                    if captured_at is not None else float("inf")
                )
                if 0 <= age < self.settings.conveyor_stale_ai_seconds:
                    return
                # The one-shot readiness event outlived its captured frame.
                # Clear it under the same lock used by apply_inference so a
                # concurrent fresh result cannot be lost.
                self._session_ai_ready.clear()

    def control_session(self) -> tuple[int | None, int | None, bool]:
        with self._lock:
            return self.session_id, self.target_total, self.goal_reached

    def start_always_on(
        self, options: ProcessorOptions, *, force_session_handoff: bool = False
    ) -> None:
        self.configure(options)
        with self._lock:
            if self.mode == "session" and not force_session_handoff:
                return
            if self.mode != "always_on" or force_session_handoff:
                self._advance_accounting_generation_locked()
                self.tracker.reset()
                self.session_id = None
                self.target_total = None
                self.conveyor_transport = "direct"
                self.goal_reached = False
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
                    and self._last_frame_accounting_generation
                    == self._accounting_generation
                    and self._last_inference_accounting_generation
                    == self._accounting_generation
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
            self._advance_accounting_generation_locked()
            self.tracker.reset()
            self.total = 0
            self.per_color.clear()
            self.confidence_sums.clear()

    def idle(self) -> None:
        with self._lock:
            self._advance_accounting_generation_locked()
            self.running = False
            self.mode = "idle"
            self.tracker.reset()
            self.publisher.pause()

    def close(self) -> None:
        self._stop.set()
        # A decoder that can still submit frames is not stopped. Shutdown is
        # deliberately fail-closed: keep the journal alive until intake has
        # actually ended instead of timing out and losing a detected crossing.
        self._decoder_thread.join()
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
        accounting_generation: int | None = None,
        frame_progress_sequence: int | None = None,
    ) -> None:
        control_update: tuple[int, int, float, int, int] | None = None
        with self._lock:
            current_accounting_generation = getattr(
                self, "_accounting_generation", 0,
            )
            if accounting_generation is None:
                # Compatibility for direct/internal callers: production frame
                # submissions always carry the generation captured by the
                # decoder before reading the frame.
                accounting_generation = current_accounting_generation
            self.inferences += 1
            self.inference_times.append(elapsed_ms)
            now = time.monotonic()
            frame_age = now - captured_at
            self.frame_latencies.append(frame_age * 1000)
            if (
                source_generation != self._source_generation
                or accounting_generation != current_accounting_generation
            ):
                # The operator switched sub/main or crossed an accounting
                # boundary while this frame waited in the shared queue. Never
                # feed that result into the new tracker, overlay or readiness
                # latch.
                self.dropped_frames += 1
                return
            controlled = self.session_id is not None and self.target_total is not None
            if controlled and not (
                0 <= frame_age < self.settings.conveyor_stale_ai_seconds
            ):
                # Freshness is measured from capture, not from slow inference
                # completion. Old/future frames cannot count or refresh the
                # physical conveyor watchdog.
                self.dropped_frames += 1
                return
            if controlled:
                progress_floor = getattr(self, "_accounting_progress_floor", 0)
                last_progress = getattr(
                    self, "_last_control_progress_sequence", progress_floor,
                )
                if frame_progress_sequence is None:
                    # Compatibility for direct unit/internal callers. The
                    # production decoder always supplies content progress.
                    frame_progress_sequence = max(progress_floor, last_progress) + 1
                if (
                    frame_progress_sequence <= progress_floor
                    or frame_progress_sequence <= last_progress
                ):
                    self.dropped_frames += 1
                    return
            self._last_inference_generation = source_generation
            self._last_inference_accounting_generation = accounting_generation
            self.latest_detections = detections
            # Размер кадра нужен, чтобы отдать координаты в долях: браузер
            # рисует рамки поверх видео произвольного размера.
            self.latest_frame_shape = frame.shape[:2]
            if not self.running:
                self.latest_counted = frozenset()
                return
            line = parse_line(self.options.line or self.settings.default_line)
            update_with_callback = getattr(
                self.tracker, "update_with_callback", None,
            )
            if update_with_callback is None:
                # Compatibility for focused test doubles. The production
                # LineTracker always commits through the callback before it
                # marks a crossing counted.
                counted = self.tracker.update(
                    detections,
                    line,
                    self.options.direction,
                    frame.shape,
                )
                for detection in counted:
                    self._commit_crossing(
                        LineCrossing(
                            detection=detection,
                            track_id=0,
                            direction=self.options.direction,
                        ),
                        accounting_generation,
                    )
            else:
                counted = update_with_callback(
                    detections,
                    line,
                    self.options.direction,
                    frame.shape,
                    before_count=lambda crossing: self._commit_crossing(
                        crossing,
                        accounting_generation,
                    ),
                )
            # Какие именно рамки этого кадра попали в счётчик — по ним оверлей
            # отличает засчитанный мешок от просто замеченного.
            self.latest_counted = frozenset(id(detection) for detection in counted)
            if controlled:
                assert self.session_id is not None
                if self.total >= self.target_total:
                    self.goal_reached = True
                control_update = (
                    self.session_id,
                    self.total,
                    captured_at,
                    accounting_generation,
                    frame_progress_sequence,
                )
        if control_update is not None:
            # Only cached state and a condition notification happen here. All
            # Modbus I/O is isolated in the actuator worker.
            observe_with_capture_time = getattr(
                self.manager, "observe_conveyor_ai", None,
            )
            if observe_with_capture_time is None:
                # Lightweight test doubles predating capture-time watchdogs.
                accepted = self.manager.conveyor.observe_ai(
                    self.camera, control_update[0], control_update[1],
                )
            else:
                accepted = observe_with_capture_time(
                    self.camera,
                    control_update[0],
                    control_update[1],
                    control_update[2],
                )
            with self._lock:
                if (
                    accepted is not False
                    and self.session_id == control_update[0]
                    and self.mode == "session"
                    and getattr(self, "_accounting_generation", 0)
                    == control_update[3]
                ):
                    self._session_ai_captured_at = control_update[2]
                    self._last_control_progress_sequence = control_update[4]
                    self._session_ai_ready.set()

    def _commit_crossing(
        self,
        crossing: LineCrossing,
        accounting_generation: int,
    ) -> None:
        """Durably record one crossing before exposing it in live counters."""

        detection = crossing.detection
        next_total = self.total + 1
        weight_kg = class_weight_kg(detection.label)
        total_weight_after = weight_kg + sum(
            class_weight_kg(label) * count
            for label, count in self.per_color.items()
        )
        manager = getattr(self, "manager", None)
        recorder = getattr(manager, "record_count_event", None)
        if recorder is not None:
            recorder(CountEvent(
                created_at=utc_now(),
                cam=self.camera,
                source=self.options.source,
                mode=self.mode,
                generation=accounting_generation,
                frame=getattr(self.tracker, "frame_index", 0),
                track_id=crossing.track_id,
                class_id=manager.class_id_for(detection.label),
                class_name=detection.label,
                confidence=detection.confidence,
                direction=crossing.direction,
                point_x=detection.center[0],
                point_y=detection.center[1],
                weight_kg=weight_kg,
                total_after=next_total,
                total_weight_after=total_weight_after,
            ))

        self.total = next_total
        self.per_color[detection.label] = self.per_color.get(detection.label, 0) + 1
        self.confidence_sums[detection.label] = (
            self.confidence_sums.get(detection.label, 0.0)
            + detection.confidence
        )

    def _detection_overlay(self) -> list[dict]:
        """Рамки последнего кадра в долях кадра — для отрисовки в браузере.

        Координаты нормализуются (0..1), поэтому оверлею не нужно знать
        разрешение камеры: оно меняется при переключении sub/main, а видео в
        интерфейсе масштабируется под размер карточки.

        Вызывается под уже захваченным ``self._lock`` из :meth:`status`.
        """
        shape = self.latest_frame_shape
        if not shape:
            return []
        height, width = shape
        if not height or not width:
            return []
        counted = self.latest_counted
        return [
            {
                "x": round(item.x1 / width, 4),
                "y": round(item.y1 / height, 4),
                "w": round((item.x2 - item.x1) / width, 4),
                "h": round((item.y2 - item.y1) / height, 4),
                "label": item.label,
                "confidence": round(item.confidence, 3),
                "counted": id(item) in counted,
            }
            for item in self.latest_detections
        ]

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
        opened_source_generation = -1
        opened_accounting_generation = -1
        last_output = 0.0
        while not self._stop.is_set():
            with self._lock:
                source_generation = self._source_generation
                accounting_generation = self._accounting_generation
                source_stream = self.source_stream
                publishing = self.mode == "session"
                controlled = publishing and self.session_id is not None
            capture_epoch = (source_generation, accounting_generation)
            if (
                capture is None
                or opened_source_generation != source_generation
                or opened_accounting_generation != accounting_generation
            ):
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
                opened_source_generation = source_generation
                opened_accounting_generation = accounting_generation
                if not capture.isOpened():
                    self.camera_reconnects += 1
                    self.last_error = f"cannot open {source_stream}"
                    capture.release()
                    capture = None
                    if controlled:
                        self.manager.terminate_cloud_conveyor(
                            self, "capture_failed", captured_at=time.monotonic(),
                        )
                    self._stop.wait(1.0)
                    continue
            ok, frame = capture.read()
            captured_at = time.monotonic()
            if not ok:
                self.camera_reconnects += 1
                self.last_error = f"lost {source_stream}"
                capture.release()
                capture = None
                if controlled:
                    self.manager.terminate_cloud_conveyor(
                        self, "capture_failed", captured_at=captured_at,
                    )
                self._stop.wait(0.5)
                continue
            # read() can block while START/RESET/handoff advances the epoch.
            # Check immediately afterward so the buffered old frame cannot
            # seed the new content baseline or enter inference.
            with self._lock:
                if capture_epoch != (
                    self._source_generation,
                    self._accounting_generation,
                ):
                    self.dropped_frames += 1
                    continue
            fingerprint = None
            if controlled:
                try:
                    fingerprint = frame_content_fingerprint(frame)
                except (AttributeError, TypeError, ValueError) as exc:
                    with self._lock:
                        if capture_epoch == (
                            self._source_generation,
                            self._accounting_generation,
                        ):
                            self._source_liveness_error = (
                                f"camera source liveness check failed: {exc}"
                            )
                            self.dropped_frames += 1
                    continue
            with self._lock:
                if capture_epoch != (
                    self._source_generation,
                    self._accounting_generation,
                ):
                    self.dropped_frames += 1
                    continue
                self.last_error = ""
                self.frames_seen += 1
                frame_progress_sequence = None
                cloud_terminal_reason = None
                content_live = True
                if controlled:
                    assert fingerprint is not None
                    (
                        content_live,
                        frame_progress_sequence,
                        self._source_liveness_error,
                    ) = self._frame_content_progress.observe(
                        capture_epoch,
                        fingerprint,
                        captured_at,
                    )
                    content_live = (
                        content_live
                        and frame_progress_sequence > self._accounting_progress_floor
                    )
                    if not content_live:
                        self.dropped_frames += 1
                        if self._source_liveness_error.startswith(
                            "camera source frozen:"
                        ):
                            cloud_terminal_reason = "stale_ai"
                else:
                    self._source_liveness_error = ""
                if content_live:
                    self.last_frame_at = utc_now()
                    self._last_frame_generation = source_generation
                    self._last_frame_accounting_generation = accounting_generation
                submit_inference = (
                    content_live
                    and captured_at - self._last_inference_submit
                    >= 1 / self.settings.inference_fps
                )
                if submit_inference:
                    self._last_inference_submit = captured_at
            if cloud_terminal_reason is not None:
                self.manager.terminate_cloud_conveyor(
                    self,
                    cloud_terminal_reason,
                    captured_at=captured_at,
                )
            if submit_inference:
                self.manager.submit(
                    self,
                    frame.copy(),
                    captured_at,
                    source_generation,
                    accounting_generation,
                    frame_progress_sequence,
                )
            if publishing and captured_at - last_output >= 1 / self.settings.output_fps:
                last_output = captured_at
                self.publisher.write(self._annotate(frame))
        if capture is not None:
            capture.release()

    def status(self) -> dict:
        elapsed = max(0.001, time.monotonic() - self.started_monotonic)
        # Keep the actuator's own binding untouched. Mirroring the processor
        # session/target into this nested object would conceal a divergence
        # from backend fencing; top-level fields below describe the processor.
        conveyor_transport = getattr(self, "conveyor_transport", "direct")
        conveyor = (
            self.manager.cloud_conveyor.status(self.camera)
            if conveyor_transport == "cloud"
            else self.manager.conveyor.status(self.camera)
        )
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
                and self._last_frame_accounting_generation
                == self._accounting_generation
                and self._last_inference_accounting_generation
                == self._accounting_generation
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
                "session_id": self.session_id,
                "target_total": self.target_total,
                "conveyor_transport": conveyor_transport,
                "remaining": (
                    max(0, self.target_total - self.total)
                    if self.target_total is not None else None
                ),
                "goal_reached": self.goal_reached,
                "conveyor": conveyor,
                "detections": self._detection_overlay(),
                # Размер кадра, к которому относятся рамки. Сами координаты уже
                # нормализованы, но CRM держит и клиентов постарше, отдающих
                # пиксели, — по этому полю она приводит их к долям.
                "detection_frame": (
                    {"width": self.latest_frame_shape[1],
                     "height": self.latest_frame_shape[0]}
                    if self.latest_frame_shape else None
                ),
                "per_color": dict(self.per_color),
                "confidence_sums": {key: round(value, 3) for key, value in self.confidence_sums.items()},
                "last_frame_at": self.last_frame_at,
                "error": self._source_liveness_error or self.last_error or None,
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
        self._items: deque[
            tuple[CameraProcessor, Any, float, int, int, int | None]
        ] = deque()
        self._condition = threading.Condition()

    def put_latest(
        self, item: tuple[CameraProcessor, Any, float, int, int, int | None]
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

    def get(
        self, timeout: float,
    ) -> tuple[CameraProcessor, Any, float, int, int, int | None]:
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
        role_state_store=None,
        line_state_store=None,
        wagon_detector=None,
        conveyor_supervisor=None,
        cloud_conveyor_observer=None,
        event_journal=None,
    ):
        self.settings = settings
        self.model = model
        self.mediamtx = mediamtx
        self.encoder = encoder
        self.processor_factory = processor_factory
        self.publisher_factory = publisher_factory
        self.state_store = state_store
        self.role_state_store = role_state_store
        self.line_state_store = line_state_store
        self.wagon_detector = wagon_detector
        self.conveyor = (
            conveyor_supervisor
            if conveyor_supervisor is not None
            else ConveyorSupervisor(settings)
        )
        self.cloud_conveyor = (
            cloud_conveyor_observer
            if cloud_conveyor_observer is not None
            else CloudConveyorObserver(settings)
        )
        self.event_journal = (
            event_journal
            if event_journal is not None
            else CountEventJournal(settings.event_db_path)
        )
        classes = self.model.metadata().get("classes", [])
        self._class_ids = {
            str(class_name): class_id
            for class_id, class_name in enumerate(classes)
        }
        self.processors: dict[str, CameraProcessor] = {}
        self.always_on_cameras: set[str] = set()
        self.always_on_source = "sub"
        self.wagon_number_camera: str | None = None
        self.wagon_number_source = "main"
        self.queue = DroppingFrameQueue(settings.queue_size)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._inference_loop, name="inference", daemon=True)
        self._worker.start()

    def class_id_for(self, class_name: str) -> int:
        try:
            return self._class_ids[class_name]
        except KeyError as exc:
            raise RuntimeError(
                f"model class is not registered: {class_name}"
            ) from exc

    def record_count_event(self, event: CountEvent) -> int:
        # The callback must not return until this exact crossing is durable.
        # That keeps the track on its pre-crossing side while SQLite is
        # unavailable. CountEvent.event_key makes a retry safe even if COMMIT
        # succeeded but its acknowledgement was lost.
        while True:
            try:
                return self.event_journal.append(event)
            except JournalTransientError:
                time.sleep(0.1)

    def count_events(
        self,
        *,
        after_id: int,
        limit: int,
        cam: str | None = None,
    ) -> dict:
        return self.event_journal.page(
            after_id=after_id,
            limit=limit,
            cam=cam,
        )

    def event_journal_health(self) -> dict:
        return self.event_journal.health()

    def submit(
        self,
        processor: CameraProcessor,
        frame: Any,
        captured_at: float,
        source_generation: int = 0,
        accounting_generation: int | None = None,
        frame_progress_sequence: int | None = None,
    ) -> None:
        if accounting_generation is None:
            # Non-decoder callers submit the frame synchronously, so the
            # processor's current boundary is the frame's boundary.
            accounting_generation = getattr(
                processor, "_accounting_generation", 0,
            )
        dropped = self.queue.put_latest(
            (
                processor,
                frame,
                captured_at,
                source_generation,
                accounting_generation,
                frame_progress_sequence,
            )
        )
        if dropped is not None:
            dropped.mark_dropped()

    def _inference_loop(self) -> None:
        while not self._stop.is_set():
            try:
                (
                    processor,
                    frame,
                    captured_at,
                    source_generation,
                    accounting_generation,
                    frame_progress_sequence,
                ) = self.queue.get(timeout=0.2)
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
                    accounting_generation,
                    frame_progress_sequence,
                )
            except Exception as exc:  # noqa: BLE001 - persistent worker boundary
                processor.last_error = f"inference failed: {exc}"
                self.terminate_cloud_conveyor(
                    processor,
                    "stale_ai",
                    captured_at=captured_at,
                )

    def observe_conveyor_ai(
        self,
        camera: str,
        session_id: int,
        total: int,
        captured_at: float,
    ) -> bool:
        """Route a nonblocking AI observation to the frozen session transport."""

        try:
            processor = self.get(camera)
        except KeyError:
            return False
        if getattr(processor, "conveyor_transport", "direct") == "cloud":
            target_total = processor.target_total
            if target_total is None:
                return False
            return self.cloud_conveyor.observe(
                camera,
                session_id,
                target_total,
                total,
                captured_at,
            )
        return self.conveyor.observe_ai(camera, session_id, total, captured_at)

    def terminate_cloud_conveyor(
        self,
        processor: CameraProcessor,
        reason: str,
        *,
        captured_at: float | None = None,
    ) -> bool:
        if getattr(processor, "conveyor_transport", "direct") != "cloud":
            return False
        session_id, target_total, _goal_reached = processor.control_session()
        if session_id is None or target_total is None:
            return False
        return self.cloud_conveyor.terminate(
            processor.camera,
            session_id,
            target_total,
            processor.total,
            reason,
            captured_at=captured_at,
        )

    def _ensure(self, camera: str, options: ProcessorOptions) -> CameraProcessor:
        camera = parse_camera(camera)
        with self._lock:
            # Reading the durable line and publishing the processor happen in
            # the same critical section as save_counting_line. Otherwise a
            # concurrent PUT could save a new line, observe no processor, and
            # then let this method create one from its stale pre-save snapshot.
            options = self._resolved_options(camera, options)
            source_stream = self.settings.source_stream(camera, options.source)
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
        with self._lock:
            try:
                existing = self.get(camera)
            except KeyError:
                existing = None
            if existing is not None and existing.running and existing.mode == "session":
                return existing.status()
            processor = self._ensure(camera, options)
            # save_counting_line takes the same manager lock before touching
            # processor options, so it cannot apply a new line between reading
            # this argument and start_session.configure(old_options).
            processor.start_session(processor.options)
        return processor.status()

    def start_controlled_session(
        self, camera: str, options: ControlledSessionOptions,
    ) -> dict:
        camera = parse_camera(camera)
        transport = options.conveyor_transport
        direct_configured = self.conveyor.status(camera)["configured"]
        cloud_configured = self.cloud_conveyor.configured(camera)
        if transport == "cloud":
            if not cloud_configured:
                raise ConveyorConflictError(
                    f"no cloud conveyor observation configured for {camera}"
                )
            if direct_configured:
                raise ConveyorConflictError(
                    f"camera {camera} cannot use cloud and direct conveyor transports"
                )
        elif cloud_configured:
            raise ConveyorConflictError(
                f"camera {camera} requires conveyor_transport=cloud"
            )
        processor_options = ProcessorOptions(
            source=options.source,
            line=options.line,
            direction=options.direction,
        )
        with self._lock:
            try:
                existing = self.get(camera)
            except KeyError:
                existing = None
            if existing is not None and existing.running and existing.mode == "session":
                session_id, target_total, _goal_reached = existing.control_session()
                if session_id is None:
                    raise ConveyorConflictError(
                        "camera already has an uncontrolled counting session"
                    )
                if session_id != options.session_id:
                    raise ConveyorConflictError("another controlled session is active")
                if target_total != options.target_total:
                    raise ConveyorConflictError(
                        "target_total is immutable for a controlled session"
                    )
                if getattr(existing, "conveyor_transport", "direct") != transport:
                    raise ConveyorConflictError(
                        "conveyor_transport is immutable for a controlled session"
                    )
                processor = existing
                if transport == "cloud":
                    if not self.cloud_conveyor.begin_session(
                        camera, options.session_id, options.target_total,
                    ):
                        raise ConveyorConflictError(
                            "cloud conveyor observation session is terminal"
                        )
                elif direct_configured:
                    self.conveyor.arm(camera, options.session_id, options.target_total)
            else:
                processor = self._ensure(camera, processor_options)
                if transport == "cloud":
                    if not self.cloud_conveyor.begin_session(
                        camera, options.session_id, options.target_total,
                    ):
                        raise ConveyorConflictError(
                            "cloud conveyor observation session is unavailable"
                        )
                elif direct_configured:
                    self.conveyor.arm(camera, options.session_id, options.target_total)
                try:
                    processor.start_controlled_session(
                        processor.options,
                        session_id=options.session_id,
                        target_total=options.target_total,
                        conveyor_transport=transport,
                    )
                except Exception:
                    if transport == "cloud":
                        self.cloud_conveyor.terminate(
                            camera,
                            options.session_id,
                            options.target_total,
                            0,
                            "session_setup_failed",
                        )
                    elif direct_configured:
                        try:
                            self.conveyor.stop(
                                camera, options.session_id, reason="session_setup_failed",
                            )
                        except Exception:  # noqa: BLE001, S110 - original error is authoritative
                            pass
                    raise
        # The old prewarm timestamp is not proof that this new accounting
        # session has a working inference loop. Wait while the relay is OFF.
        try:
            processor.wait_for_session_ai(options.session_id)
        except Exception:
            if transport == "cloud":
                self.cloud_conveyor.terminate(
                    camera,
                    options.session_id,
                    options.target_total,
                    processor.total,
                    "session_setup_failed",
                )
            raise
        return processor.status()

    def start_conveyor(self, camera: str, session_id: int) -> dict:
        processor = self.get(camera)
        current_session, _target, goal_reached = processor.control_session()
        if current_session != session_id or not processor.running or processor.mode != "session":
            raise ConveyorConflictError("stale conveyor session_id")
        if goal_reached:
            raise ConveyorConflictError("target is already reached")
        if getattr(processor, "conveyor_transport", "direct") == "cloud":
            raise ConveyorConflictError(
                "cloud conveyor transport is observation-only on camera-PC"
            )
        if not self.conveyor.status(camera)["configured"]:
            raise ConveyorConflictError(f"no conveyor controller configured for {camera}")
        self.conveyor.run(camera, session_id)
        return processor.status()

    def stop_conveyor(self, camera: str, session_id: int) -> dict:
        processor = self.get(camera)
        current_session, target_total, _goal_reached = processor.control_session()
        if current_session != session_id:
            raise ConveyorConflictError("stale conveyor session_id")
        if getattr(processor, "conveyor_transport", "direct") == "cloud":
            assert target_total is not None
            self.cloud_conveyor.terminate(
                camera,
                session_id,
                target_total,
                processor.total,
                "manual_stop",
            )
            return processor.status()
        if not self.conveyor.status(camera)["configured"]:
            return processor.status()
        self.conveyor.stop(camera, session_id, reason="manual_stop")
        return processor.status()

    def emergency_stop_conveyor(self, camera: str, session_id: int) -> dict:
        """OFF-only recovery path; never requires or creates an AI processor."""
        camera = parse_camera(camera)
        if self.cloud_conveyor.configured(camera):
            try:
                processor = self.get(camera)
            except KeyError:
                processor = None
            if processor is not None:
                current_session, target_total, _goal_reached = (
                    processor.control_session()
                )
                if current_session != session_id:
                    raise ConveyorConflictError("stale conveyor session_id")
                if target_total is not None:
                    self.cloud_conveyor.terminate(
                        camera,
                        session_id,
                        target_total,
                        processor.total,
                        "manual_stop",
                    )
                return processor.status()
            # No in-memory target exists after an edge restart, so constructing
            # a cloud payload here would violate the signed session contract.
            # The backend lease still expires OFF without an observation.
            return {
                "cam": camera,
                "running": False,
                "mode": "idle",
                "session_id": None,
                "target_total": None,
                "remaining": None,
                "goal_reached": False,
                "conveyor_transport": "cloud",
                "conveyor": self.cloud_conveyor.status(camera),
            }
        self.conveyor.emergency_stop(
            camera,
            session_id,
            reason="emergency_stop",
        )
        try:
            processor = self.get(camera)
        except KeyError:
            return {
                "cam": camera,
                "running": False,
                "mode": "idle",
                "session_id": None,
                "target_total": None,
                "remaining": None,
                "goal_reached": False,
                "conveyor": self.conveyor.status(camera),
            }
        return processor.status()

    def reset(self, camera: str) -> dict:
        processor = self.get(camera)
        session_id, _target, _goal_reached = processor.control_session()
        if session_id is not None:
            raise ConveyorConflictError(
                "controlled sessions cannot be reset; stop and create a new session"
            )
        processor.reset()
        return processor.status()

    def idle(self, camera: str) -> dict:
        with self._lock:
            processor = self.get(camera)
            session_id, _target, _goal_reached = processor.control_session()
            conveyor_transport = getattr(
                processor, "conveyor_transport", "direct",
            )
            conveyor_configured = self.conveyor.status(camera)["configured"]
            if (
                session_id is not None
                and conveyor_transport == "cloud"
                and processor.target_total is not None
            ):
                self.cloud_conveyor.terminate(
                    camera,
                    session_id,
                    processor.target_total,
                    processor.total,
                    "processor_stopped",
                )
            elif session_id is not None and conveyor_configured:
                self.conveyor.stop(camera, session_id, reason="processor_idle")
                # Release also obtains a fresh OFF readback. Do it before the
                # processor handoff so a relay failure cannot be reported as
                # IDLE while the accounting worker has already been paused.
                self.conveyor.release(camera, session_id)
            if camera in self.always_on_cameras:
                processor.start_always_on(
                    self._resolved_options(
                        camera,
                        ProcessorOptions(source=self.always_on_source),
                    ),
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
            with self._lock:
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

    def configure_wagon_number(
        self,
        camera: str | None,
        source: str = "main",
        *,
        persist: bool = True,
    ) -> dict:
        normalized = parse_camera(camera) if camera is not None else None
        if source not in {"sub", "main"}:
            raise ValueError("source must be sub or main")
        if normalized is not None:
            self.mediamtx.validate_source(
                normalized, self.settings.source_stream(normalized, source)
            )
        if persist and self.role_state_store is not None:
            self.role_state_store.save_wagon_number(normalized, source)
        self.wagon_number_camera = normalized
        self.wagon_number_source = source
        return self.wagon_number_status()

    def restore_camera_roles(self) -> dict:
        if self.role_state_store is None:
            return self.wagon_number_status()
        camera, source = self.role_state_store.load_wagon_number()
        return self.configure_wagon_number(camera, source, persist=False)

    def wagon_number_status(self) -> dict:
        camera = self.wagon_number_camera
        return {
            "camera": camera,
            "source": self.wagon_number_source,
            "stream": (
                self.settings.source_stream(camera, self.wagon_number_source)
                if camera is not None else None
            ),
            "assigned": camera is not None,
            "mode": "wagon_number_24_7",
        }

    def get(self, camera: str) -> CameraProcessor:
        camera = parse_camera(camera)
        with self._lock:
            processor = self.processors.get(camera)
        if processor is None:
            raise KeyError(camera)
        return processor

    def _resolved_options(
        self, camera: str, options: ProcessorOptions,
    ) -> ProcessorOptions:
        if options.line is not None:
            return options
        config = (
            self.line_state_store.get(camera)
            if self.line_state_store is not None
            else None
        )
        if config is None:
            return options.model_copy(update={"line": self.settings.default_line})
        return options.model_copy(update={
            "line": config["line"],
            "direction": config["direction"],
        })

    @staticmethod
    def _line_response(camera: str, config: dict[str, str]) -> dict:
        spec = config["line"]
        return {
            "cam": camera,
            "configured": True,
            "coordinate_space": "normalized",
            "line": line_payload(spec),
            "line_spec": spec,
            "direction": config["direction"],
            "updated_at": config["updated_at"],
        }

    def counting_line(self, camera: str) -> dict:
        camera = parse_camera(camera)
        self.mediamtx.validate_source(camera, camera)
        config = (
            self.line_state_store.get(camera)
            if self.line_state_store is not None
            else None
        )
        if config is not None:
            return self._line_response(camera, config)
        spec = self.settings.default_line
        return {
            "cam": camera,
            "configured": False,
            "coordinate_space": "normalized",
            "line": line_payload(spec),
            "line_spec": spec,
            "direction": "any",
            "updated_at": None,
        }

    def counting_line_configs(self) -> dict[str, dict]:
        if self.line_state_store is None:
            return {}
        return {
            camera: self._line_response(camera, config)
            for camera, config in self.line_state_store.load().items()
        }

    def save_counting_line(
        self, camera: str, value, direction: str,
    ) -> tuple[int, dict]:
        camera = parse_camera(camera)
        self.mediamtx.validate_source(camera, camera)
        if self.line_state_store is None:
            raise RuntimeError("counting-line persistence is not configured")
        update_error = None
        with self._lock:
            # Keep the durable write, processor lookup and live application in
            # one manager transaction. _ensure takes the same lock while it
            # resolves a saved line, so a processor cannot appear with a stale
            # configuration between these steps.
            processor = self.processors.get(camera)
            if processor is not None:
                session_id, _target, _goal_reached = processor.control_session()
                if (
                    processor.running
                    and processor.mode == "session"
                    and session_id is not None
                ):
                    # The calibrated line is part of the frozen controlled
                    # accounting contract. Reject before the durable write and
                    # before touching the live processor; stopping the machine
                    # is a separate, explicit operation.
                    raise ConveyorConflictError(
                        "counting line cannot change while a controlled "
                        "session is active"
                    )
            config = self.line_state_store.save(camera, value, direction)
            if processor is not None:
                try:
                    processor.update_counting_line(
                        config["line"], config["direction"]
                    )
                except (RuntimeError, ValueError) as exc:
                    update_error = exc
        response = {
            "ok": True,
            "saved": True,
            "applied_to_processor": processor is not None and update_error is None,
            **self._line_response(camera, config),
        }
        if processor is None:
            return 200, response
        if update_error is not None:
            return 503, {
                **response,
                "ok": False,
                "detail": f"saved, live processor update failed: {update_error}",
            }
        return 200, response

    def detect_wagon_plate(self, data: bytes) -> dict:
        if not data.startswith(b"\xff\xd8\xff"):
            raise ValueError("request body must be a valid JPEG image")
        if self.wagon_detector is None:
            raise RuntimeError(
                "wagon plate detector is not installed in the canonical camera service"
            )
        frame = decode_jpeg(data)
        payload = self.wagon_detector.detect(frame)
        if not isinstance(payload, dict) or not isinstance(payload.get("detections"), list):
            raise RuntimeError(  # noqa: TRY004 - dependency failure maps to 503
                "wagon plate detector returned an invalid response"
            )
        if len(payload["detections"]) > 100:
            raise RuntimeError("wagon plate detector returned too many detections")
        if any(not isinstance(item, dict) for item in payload["detections"]):
            raise RuntimeError("wagon plate detector returned an invalid detection")
        result = dict(payload)
        result.setdefault(
            "detection_frame",
            {"width": frame.shape[1], "height": frame.shape[0]},
        )
        return result

    def wagon_plate_capability(self) -> dict:
        if self.wagon_detector is None:
            return {
                "available": False,
                "provider": None,
                "ocr": False,
            }
        metadata = self.wagon_detector.metadata()
        return {
            "available": True,
            "provider": str(metadata.get("provider") or "external"),
            "ocr": metadata.get("ocr") is True,
        }

    def statuses(self) -> list[dict]:
        with self._lock:
            processors = list(self.processors.values())
        return [processor.status() for processor in processors]

    def cameras(self) -> dict:
        inventory = self.mediamtx.camera_inventory()
        for camera, item in inventory.items():
            # Cached capability/readback only; camera inventory must never
            # perform Modbus I/O or require a processor to exist.
            item["conveyor"] = (
                self.cloud_conveyor.status(camera)
                if self.cloud_conveyor.configured(camera)
                else self.conveyor.status(camera)
            )
        for camera, processor in self.processors.items():
            inventory.setdefault(camera, {"cam": camera})["ai"] = processor.status()
        return {
            "devices": self.mediamtx.device_inventory(),
            "cameras": [inventory[key] for key in sorted(inventory)],
            "line_configs": self.counting_line_configs(),
        }

    def close(self) -> None:
        with self._lock:
            processors = list(self.processors.values())
        # Direct outputs must de-energize before any processor accounting lock:
        # a durable SQLite retry intentionally holds that lock fail-closed.
        self.conveyor.close()
        for processor in processors:
            if getattr(processor, "conveyor_transport", "direct") != "cloud":
                continue
            session_id, target_total, _goal_reached = processor.control_session()
            if (
                session_id is not None
                and target_total is not None
            ):
                self.cloud_conveyor.terminate(
                    processor.camera,
                    session_id,
                    target_total,
                    processor.total,
                    "shutdown",
                )
        # Cloud is observation-only. Drain terminal callbacks before stopping
        # inference; direct outputs were already de-energized and verified.
        self.cloud_conveyor.close()
        for processor in processors:
            processor.close()
        self._stop.set()
        # queue.get wakes within 0.2s. A running crossing may be blocked in
        # durable retry; wait for its commit before closing the journal.
        self._worker.join()
        self.event_journal.close()
