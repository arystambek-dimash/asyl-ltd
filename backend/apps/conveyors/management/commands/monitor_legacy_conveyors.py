"""Run the single-leader compatibility monitor for legacy camera counters."""

from __future__ import annotations

import logging
import os
import signal
import threading
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.cameras import ai
from apps.conveyors.legacy_monitor import (
    DEFAULT_HEARTBEAT_FILE,
    DEFAULT_HEARTBEAT_MAX_AGE_SECONDS,
    AdvisoryLeader,
    LeadershipError,
    LegacyConveyorMonitor,
    check_heartbeat,
    write_heartbeat,
)

log = logging.getLogger(__name__)


def _heartbeat_file(option: str | None) -> str:
    return option or os.environ.get(
        "CONVEYOR_LEGACY_BRIDGE_HEARTBEAT_FILE",
        DEFAULT_HEARTBEAT_FILE,
    )


def _heartbeat_max_age(option: float | None) -> float:
    if option is not None:
        return max(0.1, option)
    raw = os.environ.get(
        "CONVEYOR_LEGACY_BRIDGE_HEARTBEAT_MAX_AGE_SECONDS"
    )
    if raw is None:
        return DEFAULT_HEARTBEAT_MAX_AGE_SECONDS
    try:
        return max(0.1, float(raw))
    except ValueError:
        return DEFAULT_HEARTBEAT_MAX_AGE_SECONDS


class Command(BaseCommand):
    help = "Poll legacy camera counters and feed fail-safe cloud conveyor leases"

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Run one polling cycle, then safely fence owned sessions OFF",
        )
        parser.add_argument(
            "--healthcheck",
            action="store_true",
            help="Validate the monitor heartbeat without touching PostgreSQL",
        )
        parser.add_argument("--heartbeat-file")
        parser.add_argument("--heartbeat-max-age", type=float)

    def handle(self, *args, **options):
        heartbeat_file = _heartbeat_file(options["heartbeat_file"])
        if options["healthcheck"]:
            healthy, message = check_heartbeat(
                heartbeat_file,
                max_age_seconds=_heartbeat_max_age(
                    options["heartbeat_max_age"]
                ),
            )
            if not healthy:
                raise CommandError(message)
            self.stdout.write(message)
            return

        configured = getattr(
            settings,
            "CONVEYOR_LEGACY_BRIDGE_CAMERAS",
            frozenset(),
        )
        if configured and not ai.enabled():
            raise CommandError(
                "legacy bridge cameras require AI_SERVICE_API_KEY"
            )

        try:
            leader = AdvisoryLeader.try_acquire()
        except LeadershipError as exc:
            raise CommandError(str(exc)) from exc
        if leader is None:
            raise CommandError("another legacy conveyor monitor is leader")

        monitor = LegacyConveyorMonitor()
        stop = threading.Event()
        previous_handlers: dict[int, object] = {}

        def request_stop(_signum, _frame):
            stop.set()

        try:
            if threading.current_thread() is threading.main_thread():
                for signum in (signal.SIGTERM, signal.SIGINT):
                    previous_handlers[signum] = signal.getsignal(signum)
                    signal.signal(signum, request_stop)
            write_heartbeat(
                heartbeat_file,
                state="starting",
                boot_id=monitor.boot_id,
                db_backend_pid=leader.backend_pid,
            )
            interval = float(
                getattr(settings, "CONVEYOR_LEGACY_BRIDGE_POLL_MS", 250)
            ) / 1000.0
            while not stop.is_set():
                started = time.monotonic()
                leader.ensure_current()
                stats = monitor.run_cycle()
                leader.ensure_current()
                write_heartbeat(
                    heartbeat_file,
                    state="ok",
                    boot_id=monitor.boot_id,
                    db_backend_pid=leader.backend_pid,
                )
                if options["once"]:
                    self.stdout.write(
                        "legacy-conveyor-monitor "
                        f"claimed={stats.claimed} fenced={stats.fenced} "
                        f"polled={stats.polled} observed={stats.observed} "
                        f"failed={stats.failed}"
                    )
                    break
                elapsed = time.monotonic() - started
                stop.wait(max(0.0, interval - elapsed))
        except LeadershipError as exc:
            raise CommandError(str(exc)) from exc
        finally:
            try:
                monitor.shutdown()
            except Exception:
                log.exception("Could not fence legacy sessions during shutdown")
            try:
                write_heartbeat(
                    heartbeat_file,
                    state="stopping",
                    boot_id=monitor.boot_id,
                    db_backend_pid=leader.backend_pid,
                )
            except OSError:
                log.exception("Could not mark legacy monitor heartbeat stopped")
            leader.release()
            for signum, previous in previous_handlers.items():
                signal.signal(signum, previous)
