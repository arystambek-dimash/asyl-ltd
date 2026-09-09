"""Run the dedicated automatic passage scale polling loop."""

from __future__ import annotations

import json
import logging
import os
import signal
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import InterfaceError, OperationalError, close_old_connections

from apps.grain import passage_scale_automation, passage_monitor, weighing_photos
from apps.grain import weighing_identity
from apps.grain import outbox_importer

log = logging.getLogger(__name__)


def _background_call(function):
    close_old_connections()
    try:
        return function()
    finally:
        close_old_connections()


def _write_heartbeat(path_value: str, status: str, *, now: float | None = None) -> None:
    """Atomically publish loop liveness without writing PostgreSQL every second."""

    path = Path(path_value)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"status": status, "updated_at": time.time() if now is None else now},
        separators=(",", ":"),
    ).encode("utf-8")
    temporary_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.",
            dir=path.parent,
            delete=False,
        ) as temporary:
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_name = temporary.name
        os.replace(temporary_name, path)
    finally:
        if temporary_name:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass


class Command(BaseCommand):
    help = "Poll truck scales and run durable automatic passage captures"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Run one iteration")
        parser.add_argument(
            "--interval",
            type=float,
            default=settings.VEHICLE_PLATE_AUTO_SCALE_POLL_SECONDS,
            help="Seconds between poll starts",
        )

    def handle(self, *args, **options):
        interval = float(options["interval"])
        if not 0.5 <= interval <= 10:
            raise ValueError("--interval must be between 0.5 and 10 seconds")
        once = bool(options["once"])
        stopped = threading.Event()

        def request_stop(_signum, _frame):
            stopped.set()

        previous_handlers: dict[int, Any] = {}
        if not once and threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGTERM, signal.SIGINT):
                previous_handlers[signum] = signal.signal(signum, request_stop)

        # A process gap can hide an empty->occupied edge. Preserve durable
        # processing/failure state, but require a fresh confirmed clear before
        # any idle lane may trigger after this worker starts.
        if outbox_importer.enabled():
            pass  # The independent collector did not restart with this process.
        elif once:
            passage_scale_automation.prepare_monitor_start()
        else:
            passage_monitor.prepare_start()
        heartbeat = settings.VEHICLE_PLATE_AUTO_SCALE_HEARTBEAT_FILE
        initial_status = (
            "running" if settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED else "disabled"
        )
        # Publish process liveness before the first bounded hardware/network
        # iteration.  The healthcheck's max-age contract will still fail a
        # worker that gets stuck after this point.
        _write_heartbeat(heartbeat, initial_status)
        last_status = initial_status
        pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="passage")
        recognition_future = photo_future = identity_future = None
        next_identity_at = 0.0
        try:
            while not stopped.is_set():
                started = time.monotonic()
                close_old_connections()
                status = "running"
                try:
                    if once:
                        result = (
                            outbox_importer.poll_once()
                            if outbox_importer.enabled()
                            else passage_scale_automation.monitor_once()
                        )
                    else:
                        # Bounded workers: no unbounded in-memory job queue.
                        # Any unfinished work remains discoverable in the DB.
                        finished = []
                        if recognition_future is not None and recognition_future.done():
                            finished.append(recognition_future)
                            recognition_future = None
                        if photo_future is not None and photo_future.done():
                            finished.append(photo_future)
                            photo_future = None
                        if identity_future is not None and identity_future.done():
                            finished.append(identity_future)
                            identity_future = None
                        for future in finished:
                            try:
                                future.result()
                            except (OSError, TimeoutError, OperationalError, InterfaceError):
                                log.exception("Automatic passage background dependency failed")
                                status = "degraded"
                        durable_collector = outbox_importer.enabled()
                        result = outbox_importer.poll_once() if durable_collector else passage_monitor.poll_once()
                        if not durable_collector and (recognition_future is None or recognition_future.done()):
                            recognition_future = pool.submit(
                                _background_call, passage_monitor.process_once
                            )
                        if photo_future is None or photo_future.done():
                            photo_future = pool.submit(
                                _background_call, weighing_photos.retry_due_photos
                            )
                        if identity_future is None and time.monotonic() >= next_identity_at:
                            next_identity_at = time.monotonic() + 5
                            identity_future = pool.submit(
                                _background_call, weighing_identity.process_once
                            )
                    if result.state == "disabled":
                        status = "disabled"
                    elif result.state == "unavailable":
                        status = "degraded"
                except (OSError, TimeoutError, OperationalError, InterfaceError):
                    # Bounded dependency outages are retried. Programming and
                    # invariant errors remain uncaught so Docker can restart a
                    # broken process instead of hiding it in an infinite loop.
                    log.exception("Automatic passage scale dependency failed")
                    status = "degraded"
                    if once:
                        raise
                finally:
                    close_old_connections()
                    _write_heartbeat(heartbeat, status)

                if status != last_status:
                    log.info("Automatic passage scale monitor status=%s", status)
                    last_status = status
                if once:
                    return
                remaining = max(0.0, interval - (time.monotonic() - started))
                stopped.wait(remaining)
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
            close_old_connections()
            for restore_signum, handler in previous_handlers.items():
                signal.signal(restore_signum, handler)
