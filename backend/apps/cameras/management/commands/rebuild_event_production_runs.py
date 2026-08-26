import json
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder

from apps.cameras.production_repair import (
    ProductionRepairError,
    rebuild_event_production_runs,
)


class Command(BaseCommand):
    help = (
        "Dry-run or safely rebuild exact AI production colour periods from "
        "already-applied durable events"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--camera",
            required=True,
            help="Camera id in cam<N> format",
        )
        parser.add_argument(
            "--day",
            required=True,
            help="Plant-local calendar day in YYYY-MM-DD format",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Apply the guarded replacement (default is dry-run)",
        )

    def handle(self, *args, **options):
        try:
            local_day = date.fromisoformat(str(options["day"]))
        except ValueError as exc:
            raise CommandError("--day must use YYYY-MM-DD format") from exc

        try:
            result = rebuild_event_production_runs(
                camera=options["camera"],
                local_day=local_day,
                apply=bool(options["apply"]),
            )
        except ProductionRepairError as exc:
            raise CommandError(f"production repair aborted: {exc}") from exc

        if result.applied:
            status = "changed" if result.would_change else "unchanged"
        else:
            status = "would_change" if result.would_change else "unchanged"
        payload = {
            "mode": "apply" if result.applied else "dry-run",
            "status": status,
            "camera": result.camera,
            "local_day": result.local_day,
            "boundary_at": result.boundary_at,
            "last_event_at": result.last_event_at,
            "event_count": result.event_count,
            "existing_run_count": result.existing_run_count,
            "rebuilt_run_count": result.rebuilt_run_count,
            "per_color": result.per_color,
        }
        self.stdout.write(
            json.dumps(payload, cls=DjangoJSONEncoder, ensure_ascii=False, sort_keys=True)
        )
