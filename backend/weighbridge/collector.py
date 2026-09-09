"""Hardware acquisition kept outside the application's Compose lifecycle.

Run: python -m weighbridge.collector. Only the local SQLite volume is required.
No database migrations, model imports, Redis locks or web server are involved.
"""

import fcntl
import os
import signal
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
from .outbox import Lane, Outbox


class Collector:
    def __init__(self, box):
        self.box = box
        self.lane = Lane(
            empty_max=settings.VEHICLE_PLATE_AUTO_SCALE_EMPTY_MAX_KG,
            tolerance=settings.VEHICLE_PLATE_AUTO_SCALE_STABLE_TOLERANCE_KG,
            clear_polls=settings.VEHICLE_PLATE_AUTO_SCALE_CLEAR_CONFIRM_POLLS,
        )
        self.current = None
        self.last_good = 0
        self.status = "starting"
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="evidence")
        self.futures = []

    def same_episode(self, key):
        return self.current == key and time.monotonic() - self.last_good <= 5

    def snapshot(self, event):
        photo = None
        key = event["id"]
        try:
            if self.same_episode(key):
                src = urllib.parse.urlencode({"src": event["camera"] + "main"})
                request = urllib.request.Request(settings.GO2RTC_API_URL.rstrip("/") + "/api/frame.jpeg?" + src)
                # An isolated video relay, never the web deployment's relay.
                with scale._open_request(request, timeout=3) as response:
                    value = response.read(4 * 1024 * 1024 + 1)
                if self.same_episode(key) and len(value) <= 4 * 1024 * 1024 and value.startswith(b"\xff\xd8"):
                    photo = value
        except (OSError, ValueError):
            pass
        self.box.finish(key, "photo", photo=photo,
                        updates={"photo_error": "" if photo else "snapshot_unavailable_or_late"})

    def recognize(self, event):
        payload = None
        error = "recognition_not_started"
        orientation = ""
        try:
            if self.same_episode(event["id"]):
                payload = ai.recognize_vehicle_from_camera(event["camera"], event["id"],
                                                          stable_weight_at=event["stable_weight_at"])
                if not self.same_episode(event["id"]):
                    payload, error = None, "recognition_after_departure"
                else:
                    orientation, _ = ai.vehicle_orientation(payload)
                    error = ""
        except (ai.AiError, ai.AiUnavailable, ValueError) as exc:
            if self.same_episode(event["id"]):
                orientation, _ = ai.vehicle_orientation(getattr(exc, "payload", None))
            error = "recognition_unavailable"
        self.box.finish(event["id"], "ocr", updates={
            "recognition": payload, "orientation": orientation, "recognition_error": error,
        })

    def poll(self):
        self.futures = [f for f in self.futures if not self._finished(f)]
        config = self.box.state("config") or {}
        self.lane.stable_seconds = max(2, min(30, int(config.get("stable_weight_seconds", 5))))
        now = time.monotonic()
        if self.lane.last_time is not None and now - self.lane.last_time > 5:
            self.current = None
            self.box.incident("observation_gap")
        try:
            observation = scale.read_truck_scale_observation("truck")
            trigger = self.lane.observe(observation, now)
            if observation.state != "ready":
                self.current = None
                new_status = "running" if observation.state == "unstable" else "hardware_unavailable"
            else:
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
                    self.box.put(event)  # fsync BEFORE any camera request
                    self.current = event["id"]
                    self.lane.captured()
                    if self.futures:
                        # Preserve weight; never queue a live photo behind another truck.
                        self.box.finish(event["id"], "photo", updates={"photo_error": "evidence_worker_busy"})
                        self.box.finish(event["id"], "ocr", updates={"recognition_error": "evidence_worker_busy"})
                    else:
                        self.futures = [self.pool.submit(self.snapshot, event), self.pool.submit(self.recognize, event)]
        except scale.TruckScaleNotReady:
            # A vehicle can move between the preview and strict read. Let it
            # stabilize again; this does not prove an observation gap/next truck.
            self.current = None
            self.lane.since = self.lane.weight = None
            new_status = "running"
        except (APIException, OSError, ValueError):
            self.current = None
            self.lane.unavailable(time.monotonic())
            new_status = "hardware_unavailable"
        if new_status != self.status:
            self.box.incident(new_status)
            self.status = new_status
        self.box.state("heartbeat", {"updated_at": time.time(), "status": self.status,
                                     "armed": self.lane.armed, "current": self.current,
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
        collector.pool.shutdown(wait=True)


if __name__ == "__main__":
    main()
