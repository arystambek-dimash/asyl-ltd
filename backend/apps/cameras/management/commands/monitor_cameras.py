import logging
import os
import time

from django.core.management.base import BaseCommand

from apps.cameras import ai, continuous, health

log = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Continuously probe the end-to-end camera path and record incidents"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Run exactly one probe")
        parser.add_argument(
            "--interval",
            type=int,
            default=int(os.environ.get("CAMERA_MONITOR_INTERVAL_SECONDS") or 30),
            help="Seconds between probe starts",
        )

    def handle(self, *args, **options):
        interval = max(5, options["interval"])
        while True:
            started = time.monotonic()
            try:
                state = health.monitor_once()
                self.stdout.write(
                    f"camera-health status={state.status} observed={state.observed_status} "
                    f"online={state.online_count}/{state.expected_count} "
                    f"failures={state.failure_streak} recoveries={state.recovery_streak}"
                )
                if ai.enabled():
                    try:
                        always_on = continuous.reconcile()
                        self.stdout.write(
                            "camera-ai always-on="
                            + ",".join(always_on.get("cameras", []))
                        )
                    except Exception:
                        # Camera health and the durable desired configuration
                        # remain valid while a restarted Windows service comes
                        # back. Retry on the next monitor iteration.
                        log.exception("Always-on AI reconciliation failed")
            except Exception:
                # Let Docker restart a broken one-shot startup, while a long
                # running monitor survives transient DB failures and retries.
                log.exception("Camera monitor iteration failed")
                if options["once"]:
                    raise
            if options["once"]:
                return
            elapsed = time.monotonic() - started
            time.sleep(max(1, interval - elapsed))
