"""Count-driven shipping monitor; does not acquire orders by repeated OCR."""

from __future__ import annotations

import os

from django.core.management.base import BaseCommand, CommandError

from apps.cameras import shipping_session_scheduler
from apps.common.daemon import DEGRADED, RUNNING, every, run_supervised_loop

DEFAULT_HEARTBEAT_FILE = "/tmp/shipping-transport-monitor/heartbeat.json"


class Command(BaseCommand):
    help = "Group durable shipping counts into numbered sessions and idle segments"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Run one iteration")
        parser.add_argument(
            "--interval",
            type=float,
            default=os.environ.get("SHIPPING_TRANSPORT_POLL_SECONDS") or "2",
            help="Seconds between poll starts (default: 2)",
        )

    def handle(self, *args, **options):
        interval = float(options["interval"])
        if not 0.5 <= interval <= 60:
            raise CommandError("--interval must be between 0.5 and 60 seconds")
        once = bool(options["once"])
        scheduler = (
            None if once else shipping_session_scheduler.ShippingSessionScheduler(interval)
        )

        def tick() -> str:
            result = shipping_session_scheduler.poll_once() if once else scheduler.tick()
            return DEGRADED if result.get("errors", 0) else RUNNING

        try:
            # SIGTERM finishes the current bounded iteration so an imported
            # count is not interrupted before its segment is persisted.
            run_supervised_loop(
                tick,
                once=once,
                heartbeat_file=(
                    os.environ.get("SHIPPING_TRANSPORT_HEARTBEAT_FILE")
                    or DEFAULT_HEARTBEAT_FILE
                ),
                label="Shipping transport monitor",
                pause=every(interval),
            )
        finally:
            if scheduler is not None:
                scheduler.close()
