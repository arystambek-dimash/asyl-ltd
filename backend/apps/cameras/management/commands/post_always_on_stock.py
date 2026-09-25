import os

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.cameras import production
from apps.common.daemon import RUNNING, every, run_supervised_loop


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
        run_supervised_loop(
            self.tick,
            once=bool(options["once"]),
            heartbeat_file=settings.AI_STOCK_MONITOR_HEARTBEAT_FILE,
            label="AI 24/7 stock scheduler",
            pause=every(interval),
            # Сбой одного круга (в т.ч. рестарт PostgreSQL) повторяется на
            # следующем: соединение открывается заново.
            retry_errors=(Exception,),
        )

    def tick(self) -> str:
        batches = production.post_due_stock()
        posted = sum(row["status"] in ("posted", "empty") for row in batches)
        blocked = sum(row["status"] == "blocked" for row in batches)
        failed = sum(row["status"] == "failed" for row in batches)
        self.stdout.write(
            f"ai-stock checked={len(batches)} posted={posted} "
            f"blocked={blocked} failed={failed}"
        )
        return RUNNING
