"""Separate durable count import, first-frame capture and slow number reading."""

import logging
import time
from concurrent.futures import ThreadPoolExecutor

from django.db import close_old_connections, connections

from . import ai, event_sync, shipping_segment_identity, shipping_segments
from .models import MonoblockCameraSettings

log = logging.getLogger(__name__)


def _thread_call(function, *args):
    close_old_connections()
    try:
        return function(*args)
    finally:
        connections.close_all()


def _sync_and_project(camera):
    failed = False
    # Another importer may already have committed the first bag. Publish its
    # segment before any network wait so the independent photo pool can act.
    shipping_segments.ingest_camera(camera)
    if ai.enabled():
        try:
            # Do not wait for later HTTP pages after the first bag was committed.
            # Backlog drains on following ticks; idle close requires caught-up.
            event_sync.sync_camera(camera, max_pages=1)
        except (ai.AiUnavailable, ai.AiError, event_sync.EventSyncError) as exc:
            # A network outage does not erase events already committed to CRM.
            event_sync.mark_sync_failure(camera, exc)
            failed = True
    shipping_segments.ingest_camera(camera)
    shipping_segments.close_idle(camera)
    return failed


class ShippingSessionScheduler:
    """Bounded independent pools; OCR cannot occupy the first-frame slots."""

    def __init__(self, interval=2, *, max_workers=4):
        if not 1 <= max_workers <= 32:
            raise ValueError("max_workers must be between 1 and 32")
        self.interval = interval
        self.max_workers = max_workers
        self._counts = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="shipping-counts")
        self._photos = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="shipping-photos")
        self._identity = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="shipping-identity")
        self._lanes = {}
        self._photo_jobs = []
        self._identity_jobs = []
        self._next_due = {}
        self._closed = False

    def _harvest(self, jobs, *, count_failures=False):
        pending, processed, errors = [], 0, 0
        for job in jobs:
            if not job.done():
                pending.append(job)
                continue
            processed += 1
            try:
                outcome = job.result()
                if count_failures:
                    errors += bool(outcome)
            except Exception:
                # Do not include responses, frame data or model credentials.
                log.exception("Shipping session background task failed")
                errors += 1
        return pending, processed, errors

    def tick(self):
        if self._closed:
            raise RuntimeError("Shipping session scheduler is closed")
        processed = errors = 0
        for camera, job in list(self._lanes.items()):
            if job.done():
                _, n, e = self._harvest([job], count_failures=True)
                processed += n
                errors += e
                del self._lanes[camera]
        self._photo_jobs, n, e = self._harvest(self._photo_jobs)
        processed += n
        errors += e
        self._identity_jobs, n, e = self._harvest(self._identity_jobs)
        processed += n
        errors += e
        now = time.monotonic()
        cameras = MonoblockCameraSettings.shipping_sources()
        self._next_due = {cam: due for cam, due in self._next_due.items() if cam in cameras}
        for camera in sorted(cameras, key=lambda cam: (self._next_due.get(cam, -1), cam)):
            if len(self._lanes) >= self.max_workers:
                break
            if camera in self._lanes or self._next_due.get(camera, -1) > now:
                continue
            self._lanes[camera] = self._counts.submit(_thread_call, _sync_and_project, camera)
            self._next_due[camera] = now + self.interval
        # Claims are durable and exclusive across processes. Capture is allowed
        # even while all identity workers are waiting for primary OCR or GPT.
        while len(self._photo_jobs) < self.max_workers:
            self._photo_jobs.append(self._photos.submit(_thread_call, shipping_segment_identity.capture_once))
        while len(self._identity_jobs) < self.max_workers:
            self._identity_jobs.append(self._identity.submit(_thread_call, shipping_segment_identity.process_once))
        return {"processed": processed, "errors": errors}

    def close(self):
        if self._closed:
            return
        self._closed = True
        for pool in (self._counts, self._photos, self._identity):
            pool.shutdown(wait=True, cancel_futures=True)


def poll_once():
    """Bounded diagnostic pass; the daemon uses independent schedulers."""
    errors = 0
    cameras = MonoblockCameraSettings.shipping_sources()
    for camera in cameras:
        errors += bool(_sync_and_project(camera))
    for _ in cameras:
        shipping_segment_identity.capture_once()
    for _ in cameras:
        shipping_segment_identity.process_once()
    return {"processed": len(cameras), "errors": errors}
