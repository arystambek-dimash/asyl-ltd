"""Wagon-scale collector: one stop under the unloading arch = one intake wagon.

Run: python -m weighbridge.wagon_collector. Only the local SQLite volume and
network access to the wagon scale, the camera PC and the video relay are
needed; no database, Redis or web server.
"""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.weighbridge_settings")

from django.conf import settings
from rest_framework.exceptions import APIException
from apps.cameras import ai
from apps.grain import scale
from .runtime import CollectorProcess, fetch_frame, run
from .wagon_stop import StopTracker
from .writer import OutboxWriter


class WagonCollector(CollectorProcess):
    busy_message = "outbox_busy_retry_preserving_stop"

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
        self.stop_event = threading.Event()
        self.status = "starting"
        self.last_scale = 0.0
        self.restore_standing()

    # -- standing state ---------------------------------------------------

    def remember_standing(self):
        """Persist the stop under the arch so a restart does not lose it.

        ``Outbox.state`` reads when handed ``None``, so an empty arch is
        stored as an explicit empty payload rather than a null.
        """
        standing, last_stable = self.tracker.standing, self.tracker.last_stable
        self.box.state("standing", {} if standing is None else {
            "stop": standing,
            "last_stable": list(last_stable[:2]) if last_stable else None,
            # Wall-clock time: the monotonic clock of this process means
            # nothing to the next one.
            "last_stable_at": time.time() - (time.monotonic() - last_stable[2]) if last_stable else None,
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
        last_stable, stable_at = saved.get("last_stable"), saved.get("last_stable_at")
        if isinstance(last_stable, (list, tuple)) and len(last_stable) >= 2:
            # Unknown reading time (state saved before it was kept) counts as now.
            age = time.time() - stable_at if isinstance(stable_at, (int, float)) else 0.0
            self.tracker.last_stable = (last_stable[0], last_stable[1], time.monotonic() - max(0.0, age))
        # Nothing was watched while the process was down, so the wagon may
        # already have left: the eventual departure is a motion gap.
        self.tracker.motion_gap = True
        self.box.incident(f"standing_restored:{stop['id']}")

    # -- evidence ---------------------------------------------------------

    def _ocr_wait(self, seconds):
        """True when the process is stopping; False after the pause."""
        return self.stop_event.wait(seconds)

    def evidence(self, stop):
        key = stop["id"]
        photo, frame_failed = None, False
        number, source, error, recognition, attempts = "", "", "recognition_not_started", None, 0
        budget = int(settings.WAGON_ARCH_OCR_MAX_ATTEMPTS)
        try:
            # Both the frame and the OCR live inside this try: whatever fails,
            # the finally below finishes both parts, or the event stays
            # ready=0 and head-of-line-blocks every later event in the outbox.
            # Each cycle re-fetches the frame: a cold cam8main stream often
            # needs more than the two 4s attempts the first version allowed.
            for cycle in range(budget):
                try:
                    frame = fetch_frame(self.camera)
                except Exception as exc:
                    # urllib can raise http.client exceptions (IncompleteRead
                    # and friends) that are neither OSError nor ValueError; a
                    # failed frame is one more retry, never an escaping error.
                    frame = None
                    if not frame_failed:
                        # One line per stop, not one per retry.
                        frame_failed = True
                        print(f"wagon_frame_failed:{type(exc).__name__}", flush=True)
                if photo is None and frame is not None:
                    photo = frame
                if frame is not None:
                    attempts += 1
                    try:
                        # Parsing the reply lives inside the try: a malformed
                        # payload (None, a list, missing keys) is a failed
                        # attempt, never an escaping exception.
                        # Strict parsing: a wagon number counts only with the
                        # declared length and a valid checksum.
                        payload = ai.detect_number("wagon_number", frame)
                        number = ai.number_from_payload(payload, "wagon_number") or ""
                        recognition = {"detections": [
                            {k: v for k, v in (d.get("ocr") or {}).items()
                             if k in {"accepted", "digits", "length_valid", "checksum_valid", "confidence"}}
                            for d in payload.get("detections") or [] if isinstance(d, dict)
                        ]}
                        error = "" if number else ("number_unreadable" if payload.get("detections") else "plate_not_found")
                    except ai.AiProtocolError:
                        error = "recognition_failed"
                    except (ai.AiUnavailable, ai.AiError):
                        error = "recognition_unavailable"
                    except (TypeError, AttributeError, KeyError, ValueError):
                        error = "recognition_failed"
                    else:
                        if number:
                            source = "model"
                            break
                if cycle == budget - 1:
                    break   # no retry left: finish the row now, not after one more pause
                standing = self.tracker.standing
                if standing is None or standing["id"] != key or self._ocr_wait(settings.WAGON_ARCH_OCR_RETRY_SECONDS):
                    break
        finally:
            # Readiness may lag by up to (budget - 1) x retry seconds; both parts
            # always finish together, so the row never stays ready=0.
            self.writer.enqueue("finish", key, "photo", photo=photo,
                                updates={"photo_error": "" if photo else "snapshot_unavailable"})
            self.writer.enqueue("finish", key, "ocr", updates={
                "number": number, "number_source": source, "recognition": recognition,
                "recognition_error": error, "ocr_attempts": attempts,
                "recognition_finished_at": datetime.now(timezone.utc).isoformat(),
            })

    def start_evidence(self, stop):
        self.pool.submit(self.evidence, stop).add_done_callback(self._report_evidence)

    @staticmethod
    def _report_evidence(future):
        # A worker that died silently would otherwise only be noticed as a
        # never-ready outbox row.
        error = future.exception()
        if error is not None:
            print(f"wagon_evidence_failed:{type(error).__name__}: {error}", flush=True)

    # -- polling ----------------------------------------------------------

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
            observation = scale.read_truck_scale_observation(scale.WAGON_SCALE_KEY)
        except (APIException, OSError, ValueError):
            observation = scale.ScaleObservation("unavailable", None, False, False, True, None, None)
        previous_scale = self.last_scale
        if observation.state in scale.VALID_SCALE_STATES and observation.weight_kg is not None:
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
                reading = scale.read_truck_scale(scale.WAGON_SCALE_KEY)
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
            departed_at = datetime.now(timezone.utc)
            # The exit weight is the last stable reading, often long before
            # the wagon moved off: stamp it with that reading's own time.
            exit_reading = self.tracker.last_exit
            departure = {
                "version": 2, "kind": "wagon_departure", "id": str(uuid4()), "stop_id": stop["id"],
                "camera": self.camera, "weight_kg": exit_weight,
                "stable_weight_at": (
                    departed_at - timedelta(seconds=max(0.0, now - exit_reading[2]))
                ).isoformat() if exit_reading else None,
                "departed_at": departed_at.isoformat(),
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
        # Wakes an evidence worker pausing between OCR attempts.
        self.stop_event.set()
        super().close()


def main():
    run(WagonCollector, default_directory="/var/lib/weighbridge-wagon", started_incident="collector_started")


if __name__ == "__main__":
    main()
