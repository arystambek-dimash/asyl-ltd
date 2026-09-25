"""Import the weighbridge collector's outbox and run the passage background lanes."""

from __future__ import annotations

import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections

from apps.grain import weighing_photos
from apps.grain import weighing_identity
from apps.grain import outbox_importer
from apps.grain import wagon_arch
from apps.common.daemon import (
    DEGRADED,
    DEPENDENCY_ERRORS,
    RUNNING,
    every,
    run_supervised_loop,
)

log = logging.getLogger(__name__)


def _background_call(function):
    close_old_connections()
    try:
        return function()
    finally:
        close_old_connections()


def _lane_state() -> str:
    # Without the activation marker no collector weighs trucks on this host.
    return outbox_importer.poll_once() if outbox_importer.enabled() else "disabled"


class Command(BaseCommand):
    help = "Import weighbridge collector captures and run passage background work"

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

        # Три фоновые дорожки: фото, идентичность и импортёр вагонной арки.
        pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="passage")
        futures: dict[str, Future] = {}
        next_identity_at = 0.0

        def identity_due() -> bool:
            nonlocal next_identity_at
            if time.monotonic() < next_identity_at:
                return False
            next_identity_at = time.monotonic() + 5
            return True

        # (дорожка, работа, запускать ли на этом круге при свободной дорожке).
        lanes = (
            ("photo", weighing_photos.retry_due_photos, lambda: True),
            ("identity", weighing_identity.process_once, identity_due),
            ("wagon", wagon_arch.poll_once, lambda: wagon_arch.enabled()),
        )

        def tick() -> str:
            status = RUNNING
            if once:
                state = _lane_state()
                if wagon_arch.enabled():
                    wagon_arch.poll_once()
            else:
                # Bounded workers: no unbounded in-memory job queue.
                # Any unfinished work remains discoverable in the DB.
                for name, future in list(futures.items()):
                    if not future.done():
                        continue
                    del futures[name]
                    try:
                        future.result()
                    except DEPENDENCY_ERRORS:
                        log.exception("Automatic passage background dependency failed")
                        status = DEGRADED
                state = _lane_state()
                for name, function, due in lanes:
                    if name not in futures and due():
                        futures[name] = pool.submit(_background_call, function)
            if state == "disabled":
                return "disabled"
            if state == "unavailable":
                return DEGRADED
            return status

        try:
            # Bounded dependency outages are retried. Programming and
            # invariant errors remain uncaught so Docker can restart a
            # broken process instead of hiding it in an infinite loop.
            run_supervised_loop(
                tick,
                once=once,
                heartbeat_file=settings.VEHICLE_PLATE_AUTO_SCALE_HEARTBEAT_FILE,
                label="Automatic passage scale monitor",
                pause=every(interval),
                initial_status=(
                    RUNNING if settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED else "disabled"
                ),
            )
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
