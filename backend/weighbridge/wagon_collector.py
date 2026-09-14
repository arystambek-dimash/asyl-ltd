"""Wagon-scale collector: one stop under the unloading arch = one intake wagon.

Run: python -m weighbridge.wagon_collector. Only the local SQLite volume and
network access to the wagon scale, the camera PC and the video relay are
needed; no database, Redis or web server.
"""

import fcntl
import os
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
from apps.cameras import ai
from apps.grain import scale
from .outbox import Outbox, is_busy
from .wagon_stop import StopTracker
from .writer import OutboxWriter

SCALE_KEY = "wagon"


class WagonCollector:
    def __init__(self, box):
        self.box = box
        self.camera = settings.WAGON_ARCH_CAMERA
        self.tracker = StopTracker(
            still_seconds=settings.WAGON_ARCH_STILL_SECONDS,
            stable_seconds=settings.WAGON_ARCH_STABLE_SECONDS,
            tolerance=settings.WAGON_ARCH_STABLE_TOLERANCE_KG,
            empty_max=settings.WAGON_ARCH_EMPTY_MAX_KG,
            rise_kg=settings.WAGON_ARCH_NEXT_WAGON_RISE_KG,
            motion_max_age=settings.WAGON_ARCH_MOTION_MAX_AGE_SECONDS,
        )
        self.writer = OutboxWriter(box)
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wagon-evidence")
        self.evidence_future = None
        self.stop_event = threading.Event()
        self.status = "starting"
        self.last_scale = 0.0
        self.last_queue_error = 0.0
        self.restore_standing()

    # -- standing state ---------------------------------------------------

    def remember_standing(self):
        """Persist the stop under the arch so a restart does not lose it.

        ``Outbox.state`` reads when handed ``None``, so an empty arch is
        stored as an explicit empty payload rather than a null.
        """
        standing = self.tracker.standing
        self.box.state("standing", {} if standing is None else {
            "stop": standing,
            "last_stable": list(self.tracker.last_stable) if self.tracker.last_stable else None,
        })

    def restore_standing(self):
        """Re-adopt the wagon that stood under the arch before this restart.

        Without this the tracker starts empty and the wagon that is still
        being unloaded arrives a second time with its mid-unloading weight,
        while the real stop never gets a departure.
        """
        saved = self.box.state("standing")
        stop = (saved or {}).get("stop")
        if not isinstance(stop, dict) or not stop.get("id"):
            return
        self.tracker.arrived(stop, time.monotonic())
        last_stable = (saved or {}).get("last_stable")
        if isinstance(last_stable, (list, tuple)) and len(last_stable) == 3:
            self.tracker.last_stable = tuple(last_stable)
        # Nothing was watched while the process was down, so the wagon may
        # already have left: the eventual departure is a motion gap.
        self.tracker.motion_gap = True
        self.box.incident(f"standing_restored:{stop['id']}")

    # -- evidence ---------------------------------------------------------

    def frame(self, reported=None):
        """One JPEG from the relay, or None. Never raises: evidence is best effort.

        urllib can raise http.client exceptions (IncompleteRead and friends)
        that are neither OSError nor ValueError; letting one escape would
        leave the outbox row unfinished, so every failure is swallowed here.
        """
        src = urllib.parse.urlencode({"src": self.camera + "main"})
        request = urllib.request.Request(settings.GO2RTC_API_URL.rstrip("/") + "/api/frame.jpeg?" + src)
        try:
            with scale._open_request(request, timeout=4) as response:
                value = response.read(4 * 1024 * 1024 + 1)
        except Exception as exc:
            if reported is not None and not reported:
                # One line per stop, not one per retry.
                reported.add(True)
                print(f"wagon_frame_failed:{type(exc).__name__}", flush=True)
            return None
        if len(value) > 4 * 1024 * 1024 or not value.startswith(b"\xff\xd8"):
            return None
        return value

    def _ocr_wait(self, seconds):
        """True when the process is stopping; False after the pause."""
        return self.stop_event.wait(seconds)

    def evidence(self, stop):
        key = stop["id"]
        photo, reported = None, set()
        number, source, error, recognition, attempts = "", "", "recognition_not_started", None, 0
        cycles, budget = 0, int(settings.WAGON_ARCH_OCR_MAX_ATTEMPTS)
        try:
            # Both the frame and the OCR live inside this try: whatever fails,
            # the finally below finishes both parts, or the event stays
            # ready=0 and head-of-line-blocks every later event in the outbox.
            # Each cycle re-fetches the frame: a cold cam8main stream often
            # needs more than the two 4s attempts the first version allowed.
            while cycles < budget:
                cycles += 1
                frame = self.frame(reported)
                if photo is None and frame is not None:
                    photo = frame
                if frame is not None:
                    attempts += 1
                    try:
                        # Parsing the reply lives inside the try: a malformed
                        # payload (None, a list, missing keys) is a failed
                        # attempt, never an escaping exception.
                        payload = ai.detect_wagon_plate(frame)
                        number = ai.accepted_plate_number(payload)
                        recognition = {"detections": [
                            {k: v for k, v in (d.get("ocr") or {}).items() if k in {"accepted", "number", "confidence"}}
                            for d in payload.get("detections") or [] if isinstance(d, dict)
                        ]}
                        error = "" if number else ("number_unreadable" if payload.get("detections") else "plate_not_found")
                    except (ai.AiUnavailable, ai.AiError):
                        error = "recognition_unavailable"
                    except (TypeError, AttributeError, KeyError, ValueError):
                        error = "recognition_failed"
                    else:
                        if number:
                            source = "model"
                            break
                if attempts >= budget:
                    break
                standing = self.tracker.standing
                if standing is None or standing["id"] != key or self._ocr_wait(settings.WAGON_ARCH_OCR_RETRY_SECONDS):
                    break
        finally:
            # Readiness may lag by up to budget x retry seconds; both parts
            # always finish together, so the row never stays ready=0.
            self.writer.enqueue("finish", key, "photo", photo=photo,
                                updates={"photo_error": "" if photo else "snapshot_unavailable"})
            self.writer.enqueue("finish", key, "ocr", updates={
                "number": number, "number_source": source, "recognition": recognition,
                "recognition_error": error, "ocr_attempts": attempts,
                "recognition_finished_at": datetime.now(timezone.utc).isoformat(),
            })

    def start_evidence(self, stop):
        self.evidence_future = self.pool.submit(self.evidence, stop)
        self.evidence_future.add_done_callback(self._report_evidence)

    @staticmethod
    def _report_evidence(future):
        # A worker that died silently would otherwise only be noticed as a
        # never-ready outbox row.
        error = future.exception()
        if error is not None:
            print(f"wagon_evidence_failed:{type(error).__name__}: {error}", flush=True)

    # -- polling ----------------------------------------------------------

    def poll(self):
        try:
            self._poll()
        except sqlite3.OperationalError as exc:
            if not is_busy(exc):
                raise
            if time.monotonic() - self.last_queue_error > 30:
                print("outbox_busy_retry_preserving_stop", flush=True)
                self.last_queue_error = time.monotonic()

    def _observe_motion(self):
        try:
            value = ai.arch_motion(self.camera)
        except (ai.AiUnavailable, ai.AiError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    def _poll(self):
        self.writer.start()
        now = time.monotonic()
        new_status = "running"
        try:
            observation = scale.read_truck_scale_observation(SCALE_KEY)
        except (APIException, OSError, ValueError):
            observation = scale.ScaleObservation("unavailable", None, False, False, True, None, None)
        previous_scale = self.last_scale
        if observation.state in {"ready", "unstable"} and observation.weight_kg is not None:
            self.last_scale = now
        elif now - self.last_scale > 5:
            new_status = "hardware_unavailable"
        motion = self._observe_motion()
        if motion is None and new_status == "running":
            new_status = "camera_unavailable"
        last_stable_before = self.tracker.last_stable
        result = self.tracker.observe(observation, motion, now)
        if self.tracker.standing is not None and self.tracker.last_stable is not last_stable_before:
            # A new post-arrival stable reading: the exit weight that a
            # restart would otherwise forget.
            self.remember_standing()
        if result is not None and result[0] == "arrival":
            try:
                reading = scale.read_truck_scale(SCALE_KEY)
            except scale.TruckScaleNotReady:
                # The wagon moved between the preview and the strict read.
                self.tracker.arrival_rejected()
                reading = None
            except (APIException, OSError, ValueError):
                # An unavailable or malformed strict read must never kill the
                # poll loop. The authoritative read is what the scale link is
                # judged on, so this poll earns no freshness credit and counts
                # towards the same outage window as a failed observation.
                self.tracker.arrival_rejected()
                reading = None
                self.last_scale = previous_scale
                if now - self.last_scale > 5:
                    new_status = "hardware_unavailable"
            if reading is not None:
                if abs(float(reading.weight_kg) - result[1]) > self.tracker.tolerance:
                    self.tracker.arrival_rejected()
                else:
                    weight = int(reading.weight_kg)
                    read_started = datetime.now(timezone.utc)
                    stop = {
                        "version": 2, "kind": "wagon_stop", "id": str(uuid4()), "camera": self.camera,
                        "weight_kg": weight,
                        "stable_weight_at": (read_started - timedelta(seconds=float(reading.age_seconds))).isoformat(),
                        "scale_age_seconds": str(reading.age_seconds), "scale_updated_at": reading.updated_at,
                        "still_seconds": float((motion or {}).get("still_seconds") or 0.0),
                    }
                    self.tracker.arrived(stop, now)
                    self.remember_standing()
                    self.writer.enqueue("put", stop)
                    self.start_evidence(stop)
                    self.box.incident(f"wagon_arrived:{weight}")
        elif result is not None and result[0] == "departure":
            _, stop, exit_weight, gap = result
            last_exit = self.tracker.last_exit
            departure = {
                "version": 2, "kind": "wagon_departure", "id": str(uuid4()), "stop_id": stop["id"],
                "camera": self.camera, "weight_kg": exit_weight,
                "stable_weight_at": datetime.now(timezone.utc).isoformat() if exit_weight is not None else None,
                "scale_updated_at": last_exit[1] if last_exit else None,
                "departed_at": datetime.now(timezone.utc).isoformat(),
                "motion_gap": bool(gap),
            }
            self.writer.enqueue("put", departure)
            self.writer.enqueue("finish", departure["id"], "photo", updates={"photo_error": ""})
            self.writer.enqueue("finish", departure["id"], "ocr", updates={})
            # Only after the departure is queued: a crash in between would
            # otherwise forget the stop without having recorded its exit.
            self.remember_standing()
            label = "wagon_departed_unseen" if gap else "wagon_departed"
            self.box.incident(f"{label}:{exit_weight if exit_weight is not None else 'none'}")
        if new_status != self.status:
            self.box.incident(new_status)
            if new_status == "hardware_unavailable" and self.last_scale:
                # Once per outage, on the transition — not on every poll for
                # its whole duration (~3600 incident rows per hour otherwise).
                self.box.incident("observation_gap")
            self.status = new_status
        standing = self.tracker.standing
        self.box.state("heartbeat", {
            "updated_at": time.time(), "status": self.status,
            "standing": standing["id"] if standing else None,
            "motion": self.tracker.motion_state, "pending_writes": self.writer.pending(),
        })

    def close(self):
        self.stop_event.set()
        self.pool.shutdown(wait=True)
        drained = self.writer.drain(timeout=10)
        self.writer.shutdown()
        if not drained:
            print("outbox_storage_unavailable_unflushed_evidence", flush=True)


def main():
    directory = Path(os.environ.get("WEIGHBRIDGE_OUTBOX_DIR", "/var/lib/weighbridge-wagon"))
    box = Outbox(directory)
    lock = (directory / "collector.lock").open("a")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("collector_already_running", flush=True)
        raise SystemExit(1)
    box.recover()
    box.incident("collector_started")
    collector = WagonCollector(box)
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
