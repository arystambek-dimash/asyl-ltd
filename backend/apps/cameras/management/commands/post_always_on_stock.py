import logging
import os
import time

from django.core.management.base import BaseCommand

from apps.cameras import production

log = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Post completed AI 24/7 production shifts to warehouse stock"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Run exactly once")
        parser.add_argument(
            "--interval",
            type=int,
            default=int(os.environ.get("AI_STOCK_POST_INTERVAL_SECONDS") or 60),
            help="Seconds between posting checks",
        )

    def handle(self, *args, **options):
        interval = max(10, options["interval"])
        while True:
            started = time.monotonic()
            try:
                batches = production.post_due_stock()
                posted = sum(
                    row["status"] in ("posted", "empty") for row in batches
                )
                blocked = sum(row["status"] == "blocked" for row in batches)
                failed = sum(row["status"] == "failed" for row in batches)
                self.stdout.write(
                    f"ai-stock checked={len(batches)} posted={posted} "
                    f"blocked={blocked} failed={failed}"
                )
            except Exception:
                log.exception("AI 24/7 stock scheduler iteration failed")
                if options["once"]:
                    raise
            if options["once"]:
                return
            elapsed = time.monotonic() - started
            time.sleep(max(1, interval - elapsed))
