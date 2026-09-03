"""Run the dedicated automatic passage scale polling loop."""

from __future__ import annotations

import json
import logging
import os
import signal
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import InterfaceError, OperationalError, close_old_connections

from apps.grain import passage_scale_automation

log = logging.getLogger(__name__)


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
        passage_scale_automation.prepare_monitor_start()
        heartbeat = settings.VEHICLE_PLATE_AUTO_SCALE_HEARTBEAT_FILE
        initial_status = (
            "running" if settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED else "disabled"
        )
        # Publish process liveness before the first bounded hardware/network
        # iteration.  The healthcheck's max-age contract will still fail a
        # worker that gets stuck after this point.
        _write_heartbeat(heartbeat, initial_status)
        last_status = initial_status
        try:
            while not stopped.is_set():
                started = time.monotonic()
                close_old_connections()
                status = "running"
                try:
                    result = passage_scale_automation.monitor_once()
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
            close_old_connections()
            for restore_signum, handler in previous_handlers.items():
                signal.signal(restore_signum, handler)
