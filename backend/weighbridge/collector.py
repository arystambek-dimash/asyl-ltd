"""Hardware acquisition kept outside the application's Compose lifecycle.

Run: python -m weighbridge.collector. Only the local SQLite volume is required.
No database migrations, model imports, Redis locks or web server are involved.
"""

import fcntl
import math
import os
import re
import signal
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.weighbridge_settings")

from django.conf import settings
from rest_framework.exceptions import APIException
from apps.grain import scale
from apps.cameras import ai
from .outbox import Lane, Outbox, is_busy
from .writer import OutboxWriter

# A Camera-PC refusal (422 no_match, 503 camera_unavailable, ...) still says why:
# how many frames held a plate, the vote tally and the last OCR reads. Only this
# bounded subset travels with the event; frames or base64 never enter the outbox.
# The CRM applies its own model-side bounds again on import.
DIAGNOSTIC_STRINGS = (("status", 300), ("error", 300), ("error_code", 300))
DIAGNOSTIC_COUNTERS = ("fresh_frames_seen", "frames_scanned", "detected_frames", "ocr_candidates",
                       "accepted_reads", "ambiguous_frames", "confirmation_votes", "zoom_frames", "zoom_detected_frames")
DIAGNOSTIC_FLOATS = (("best_detector_confidence", 1), ("confirmation_window_seconds", 1e6))
READ_STRINGS = ("variant", "raw_text", "number")
READ_FLOATS = (("confidence", 1), ("detector_confidence", 1), ("bbox_w", 100_000), ("bbox_h", 100_000))
# The CRM keeps the same eight (vehicle_weight_capture.MAX_NO_MATCH_VOTES), so a
# competing number is never cut on import behind a lone plate's back.
MAX_DIAGNOSTIC_VOTES = 8
MAX_DIAGNOSTIC_READS = 8
MAX_READ_TEXT = 30


def _counter(value):
    return value if type(value) is int and value >= 0 else None


def _bounded(value, upper):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and 0 <= number <= upper else None


def _diagnostics(payload):
    """Plain dict/list subset of a failure answer; no Django models in this container."""
    if not isinstance(payload, dict):
        return {}
    safe = {}
    for field, limit in DIAGNOSTIC_STRINGS:
        if isinstance(payload.get(field), str):
            safe[field] = payload[field][:limit]
    for field in DIAGNOSTIC_COUNTERS:
        if _counter(payload.get(field)) is not None:
            safe[field] = payload[field]
    for field, upper in DIAGNOSTIC_FLOATS:
        number = _bounded(payload.get(field), upper)
        if number is not None:
            safe[field] = number
    votes = payload.get("votes")
    if isinstance(votes, dict):
        valid = [
            (number, count) for number, count in votes.items()
            if isinstance(number, str) and 1 <= len(number) <= MAX_READ_TEXT and _counter(count) is not None
        ]
        if valid:
            safe["votes"] = dict(valid[:MAX_DIAGNOSTIC_VOTES])
        if len(valid) > MAX_DIAGNOSTIC_VOTES:
            # A competitor may sit past the cut: the CRM must not single out a plate.
            safe["votes_truncated"] = True
    reads = payload.get("last_reads")
    if isinstance(reads, list):
        kept = []
        for read in reads[:MAX_DIAGNOSTIC_READS]:
            if not isinstance(read, dict):
                continue
            item = {}
            if _counter(read.get("frame")) is not None:
                item["frame"] = read["frame"]
            for field in READ_STRINGS:
                if isinstance(read.get(field), str):
                    item[field] = read[field][:MAX_READ_TEXT]
            for field, upper in READ_FLOATS:
                number = _bounded(read.get(field), upper)
                if number is not None:
                    item[field] = number
            if item:
                kept.append(item)
        if kept:
            safe["last_reads"] = kept
    return safe


def _recognition_error(payload):
    """The Camera-PC status as the event's error code: no_match, camera_unavailable, ..."""
    status = payload.get("status") if isinstance(payload, dict) else None
    code = re.sub(r"[^a-z_]", "", status.lower())[:64] if isinstance(status, str) else ""
    return code or "recognition_unavailable"


class Collector:
    def __init__(self, box):
        self.box = box
        self.lane = Lane(
            empty_max=settings.VEHICLE_PLATE_AUTO_SCALE_EMPTY_MAX_KG,
            tolerance=settings.VEHICLE_PLATE_AUTO_SCALE_STABLE_TOLERANCE_KG,
            clear_polls=settings.VEHICLE_PLATE_AUTO_SCALE_CLEAR_CONFIRM_POLLS,
            rearm_delta=settings.VEHICLE_PLATE_AUTO_SCALE_REARM_DELTA_KG,
        )
        self.current = None
        self.last_good = 0
        self.status = "starting"
        self.pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="evidence")
        self.futures = {}
        self.last_queue_error = 0
        self.writer = OutboxWriter(box)

    @property
    def pending_writes(self):
        return self.writer.pending_writes

    def enqueue_write(self, method, *args, **kwargs):
        # The event and evidence are immutable values owned by this capture.
        # Contention may delay persistence, never cause a replacement reading
        # or a delayed live camera request.
        self.writer.enqueue(method, *args, **kwargs)

    def start_writer(self):
        self.writer.start()

    def close(self):
        self.pool.shutdown(wait=True)
        # Each flush is bounded by SQLite's busy timeout; there is no infinite
        # worker retry loop holding process shutdown. Normal shutdown drains all
        # captured evidence before the writer exits.
        drained = self.writer.drain(timeout=10)
        self.writer.shutdown()
        if not drained:
            print("outbox_storage_unavailable_unflushed_evidence", flush=True)

    def same_episode(self, key):
        return self.current == key and time.monotonic() - self.last_good <= 5

    def snapshot(self, event):
        photo = None
        key = event["id"]
        error = "snapshot_after_departure"
        # Cold RTSP decoding and brief relay failures may consume the first
        # request. Retry only while this exact scale occupancy remains visible.
        for _ in range(2):
            if not self.same_episode(key):
                break
            try:
                src = urllib.parse.urlencode({"src": event["camera"] + "main"})
                request = urllib.request.Request(settings.GO2RTC_API_URL.rstrip("/") + "/api/frame.jpeg?" + src)
                # An isolated video relay, never the web deployment's relay.
                with scale._open_request(request, timeout=4) as response:
                    value = response.read(4 * 1024 * 1024 + 1)
                if self.same_episode(key) and len(value) <= 4 * 1024 * 1024 and value.startswith(b"\xff\xd8"):
                    photo = value
                    break
                error = "snapshot_invalid_or_late"
            except (OSError, ValueError):
                error = "snapshot_unavailable"
        self.enqueue_write("finish", key, "photo", photo=photo,
                        updates={"photo_error": "" if photo else error})

    def recognize(self, event):
        payload = None
        diagnostics = None
        error = "recognition_not_started"
        orientation = ""
        frame_bound = False
        try:
            if self.same_episode(event["id"]):
                payload = ai.recognize_vehicle_from_camera(event["camera"], event["id"],
                                                          stable_weight_at=event["stable_weight_at"])
                if not self.same_episode(event["id"]):
                    payload, error = None, "recognition_after_departure"
                else:
                    orientation, _ = ai.vehicle_orientation(payload)
                    frame_bound = True
                    error = ""
        except ai.AiError as exc:
            error = "recognition_unavailable"
            if self.same_episode(event["id"]):
                orientation, _ = ai.vehicle_orientation(exc.payload)
                # The camera replied to this UUID before the truck departed.
                # Its immutable frame can be downloaded later even if OCR failed,
                # and its refusal (no plate found vs two votes of three) is the
                # CRM's evidence; a late refusal is as untrusted as a late plate.
                frame_bound = True
                error = _recognition_error(exc.payload)
                diagnostics = _diagnostics(exc.payload)
        except (ai.AiUnavailable, ValueError):
            error = "recognition_unavailable"
        self.enqueue_write("finish", event["id"], "ocr", updates={
            "recognition": payload, "orientation": orientation, "recognition_error": error,
            "recognition_diagnostics": diagnostics, "recognition_frame_bound": frame_bound,
            "recognition_finished_at": datetime.now(timezone.utc).isoformat(),
        })

    def start_evidence(self, event):
        # One OCR slot and two photo slots: a previous truck's bounded snapshot
        # timeout must not delay the next truck's immediate frame. No waiting
        # jobs enter the executor queue; three slots are the hard I/O bound.
        self.futures = {key: f for key, f in self.futures.items() if not self._finished(f)}
        for part, worker in (("photo", self.snapshot), ("ocr", self.recognize)):
            key = f"photo:{event['id']}" if part == "photo" else "ocr"
            photo_slots = sum(name.startswith("photo:") for name in self.futures)
            busy = photo_slots >= 2 if part == "photo" else key in self.futures
            if busy:
                field = "photo_error" if part == "photo" else "recognition_error"
                self.enqueue_write("finish", event["id"], part, updates={field: "evidence_worker_busy"})
            else:
                self.futures[key] = self.pool.submit(worker, event)

    def poll(self):
        try:
            self._poll()
        except sqlite3.OperationalError as exc:
            if not is_busy(exc):
                raise
            # Do not restart/re-arm a parked truck because the importer briefly
            # owns a SQLite lock. The next tick retries with the same lane state.
            if time.monotonic() - self.last_queue_error > 30:
                print("outbox_busy_retry_preserving_occupancy", flush=True)
                self.last_queue_error = time.monotonic()

    def _poll(self):
        self.start_writer()
        self.futures = {part: f for part, f in self.futures.items() if not self._finished(f)}
        config = self.box.state("config") or {}
        self.lane.stable_seconds = max(2, min(30, int(config.get("stable_weight_seconds", 5))))
        now = time.monotonic()
        if self.lane.last_time is not None and now - self.lane.last_time > 5:
            self.current = None
            self.box.incident("observation_gap")
        try:
            observation = scale.read_truck_scale_observation("truck")
            trigger = self.lane.observe(observation, now)
            if self.lane.rearmed_by_change:
                # Trucks can queue through without an empty reading; keep a
                # trace of every re-arm that did not wait for a clear scale.
                self.box.incident(f"rearmed_by_weight_change:{self.lane.rearmed_by_change}")
                self.lane.rearmed_by_change = None
            if observation.state not in {"ready", "unstable"} or observation.weight_kg is None:
                # One failed read is not an outage. The status, its incident and
                # the CRM's degraded badge only flip once the scale has given no
                # usable reading for over 5 s; the lane itself restarts at once.
                if now - self.last_good > 5:
                    self.current = None
                    new_status = "running" if observation.state == "unstable" else "hardware_unavailable"
                else:
                    new_status = self.status
            else:
                # A fresh nonzero unstable reading is still the same vehicle.
                # Direction/frame work ends on empty weight or a real data gap.
                self.last_good = time.monotonic()
                new_status = "running"
                if observation.weight_kg <= self.lane.empty_max:
                    self.current = None
            if trigger and (self.box.directory / "enabled").is_file():
                # A second strict reading at the edge, not a later cached value.
                read_started = datetime.now(timezone.utc)
                reading = scale.read_truck_scale("truck")
                if abs(float(reading.weight_kg) - float(self.lane.weight)) > self.lane.tolerance:
                    self.lane.since = None
                else:
                    weight = int(reading.weight_kg)
                    if reading.weight_kg != weight or weight <= self.lane.empty_max:
                        raise ValueError("Invalid authoritative weight")
                    event = {
                        "version": 1, "id": str(uuid4()), "weight_kg": weight,
                        "camera": settings.VEHICLE_PLATE_WEIGHT_FIRST_CAMERA,
                        "stable_weight_at": (read_started - timedelta(seconds=float(reading.age_seconds))).isoformat(),
                        "scale_age_seconds": str(reading.age_seconds), "scale_updated_at": reading.updated_at,
                    }
                    # Keep the exact authoritative sample even if SQLite is
                    # briefly locked. Photo/OCR start now for this occupancy;
                    # the independent writer commits weight before evidence.
                    self.current = event["id"]
                    self.lane.captured()
                    self.enqueue_write("put", event)
                    self.start_evidence(event)
        except scale.TruckScaleNotReady:
            # A vehicle can move between the preview and strict read. Let it
            # stabilize again; this does not prove an observation gap/next truck.
            self.current = None
            self.lane.since = self.lane.weight = None
            new_status = "running"
        except (APIException, OSError, ValueError):
            self.lane.unavailable(time.monotonic())
            if time.monotonic() - self.last_good > 5:
                self.current = None
                new_status = "hardware_unavailable"
            else:
                new_status = self.status
        if new_status != self.status:
            self.box.incident(new_status)
            self.status = new_status
        self.box.state("heartbeat", {"updated_at": time.time(), "status": self.status,
                                     "armed": self.lane.armed, "current": self.current,
                                     "pending_writes": len(self.pending_writes),
                                     "clear": self.lane.clear_count >= self.lane.clear_polls})

    @staticmethod
    def _finished(future):
        if not future.done():
            return False
        future.result()  # Unexpected failures restart; pending weight remains durable.
        return True


def main():
    directory = Path(os.environ.get("WEIGHBRIDGE_OUTBOX_DIR", "/var/lib/weighbridge"))
    box = Outbox(directory)
    lock = (directory / "collector.lock").open("a")
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    box.recover()
    box.incident("collector_started_require_clear")
    collector = Collector(box)
    stopped = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stopped.set())
    try:
        while not stopped.is_set():
            started = time.monotonic()
            collector.poll()
            stopped.wait(max(0, 1 - (time.monotonic() - started)))
    finally:
        collector.close()


if __name__ == "__main__":
    main()
