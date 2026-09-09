"""Run automatic shipping order acquisition independently of browser sessions."""

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

from django.core.management.base import BaseCommand, CommandError
from django.db import InterfaceError, OperationalError, close_old_connections

from apps.cameras import shipping_automation
from apps.cameras.shipping_transport_scheduler import ShippingTransportScheduler

log = logging.getLogger(__name__)
DEFAULT_HEARTBEAT_FILE = "/tmp/shipping-transport-monitor/heartbeat.json"


def _write_heartbeat(path_value: str, status: str, *, now: float | None = None) -> None:
    """Publish loop liveness atomically without a database write on every poll."""
    path = Path(path_value)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"status": status, "updated_at": time.time() if now is None else now},
        separators=(",", ":"),
    ).encode("utf-8")
    temporary_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{path.name}.", dir=path.parent, delete=False
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass


class Command(BaseCommand):
    help = "Continuously match shipping transport cameras to loading orders"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Run one iteration")
        parser.add_argument(
            "--interval",
            type=float,
            default=os.environ.get("SHIPPING_TRANSPORT_POLL_SECONDS") or "2",
            help="Seconds between poll starts (default: 2)",
        )

    def handle(self, *args, **options):
        return self.run_monitor(
            *args, scheduler_class=ShippingTransportScheduler,
            once_callback=shipping_automation.poll_once, **options,
        )

    def run_monitor(self, *args, scheduler_class, once_callback, **options):
        interval = float(options["interval"])
        if not 0.5 <= interval <= 60:
            raise CommandError("--interval must be between 0.5 and 60 seconds")
        once = bool(options["once"])
        stopped = threading.Event()

        def request_stop(_signum, _frame):
            # Finish the current bounded iteration so an acquired order is not
            # interrupted between the camera acknowledgement and persistence.
            stopped.set()

        previous_handlers: dict[int, Any] = {}
        if not once and threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGTERM, signal.SIGINT):
                previous_handlers[signum] = signal.signal(signum, request_stop)

        heartbeat = (
            os.environ.get("SHIPPING_TRANSPORT_HEARTBEAT_FILE")
            or DEFAULT_HEARTBEAT_FILE
        )
        scheduler = None if once else scheduler_class(interval)
        try:
            _write_heartbeat(heartbeat, "running")
            last_status = "running"
            while not stopped.is_set():
                started = time.monotonic()
                status = "running"
                try:
                    close_old_connections()
                    result = (
                        once_callback() if once else scheduler.tick()
                    )
                    if result.get("errors", 0):
                        status = "degraded"
                except (OSError, TimeoutError, OperationalError, InterfaceError):
                    # Retry dependency outages. Unexpected programming errors
                    # escape so the service supervisor can restart the worker.
                    log.exception("Shipping transport monitor dependency failed")
                    status = "degraded"
                    if once:
                        raise
                finally:
                    close_old_connections()
                    _write_heartbeat(heartbeat, status)

                if status != last_status:
                    log.info("Shipping transport monitor status=%s", status)
                    last_status = status
                if once:
                    return
                stopped.wait(max(0.0, interval - (time.monotonic() - started)))
        finally:
            if scheduler is not None:
                scheduler.close()
            close_old_connections()
            for restore_signum, handler in previous_handlers.items():
                signal.signal(restore_signum, handler)
