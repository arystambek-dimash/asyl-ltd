import logging
import os

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.cameras import ai, continuous, health
from apps.common.daemon import RUNNING, every, run_supervised_loop

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

    @staticmethod
    def wagon_plate_poll_enabled() -> bool:
        # The arch stops are the arrival sensor once the wagon collector runs.
        return not bool(settings.WAGON_ARCH_AUTOMATION_ENABLED)

    def handle(self, *args, **options):
        interval = max(5, options["interval"])
        run_supervised_loop(
            self.tick,
            once=bool(options["once"]),
            heartbeat_file=settings.CAMERA_MONITOR_HEARTBEAT_FILE,
            label="Camera monitor",
            pause=every(interval),
            # Let Docker restart a broken one-shot startup, while a long
            # running monitor survives transient failures and retries: every
            # iteration reopens a connection broken by a PostgreSQL restart.
            retry_errors=(Exception,),
        )

    def tick(self) -> str:
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
                    + ",".join(always_on.get("camera_sources") or [])
                )
            except Exception:
                # A control-plane failure must not disable the separate
                # wagon OCR path for this monitor iteration.
                log.exception("Always-on AI reconciliation failed")
            if self.wagon_plate_poll_enabled():
                try:
                    # Камера вместо датчика прибытия: увидела табличку —
                    # приход открывается сам. Камеру берёт из настройки CRM
                    # (MonoblockCameraSettings), свой период опроса внутри.
                    plate = continuous.poll_wagon_plate()
                    if plate.get("created"):
                        self.stdout.write(
                            f"camera-ai wagon-arrival=#{plate['created']}"
                        )
                except Exception:
                    log.exception("Wagon plate polling failed")
        return RUNNING
