import json

from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder

from apps.cameras.manual_analytics_import import (
    ManualAnalyticsImportError,
    apply_manual_analytics_import,
    inspect_manual_analytics_import,
    load_manual_analytics_document,
)


class Command(BaseCommand):
    help = (
        "Validate and optionally apply a checksum-pinned, analytics-only "
        "best.pt recovery file"
    )

    def add_arguments(self, parser):
        parser.add_argument("path", help="Path to the recovery JSON file")
        parser.add_argument(
            "--expected-sha256",
            required=True,
            help="Expected SHA256 of the exact JSON file",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Commit the import (default is a read-only dry-run)",
        )

    def handle(self, *args, **options):
        try:
            document = load_manual_analytics_document(
                options["path"], expected_sha256=options["expected_sha256"]
            )
            result = (
                apply_manual_analytics_import(document)
                if options["apply"]
                else inspect_manual_analytics_import(document)
            )
        except ManualAnalyticsImportError as exc:
            raise CommandError(f"manual analytics import aborted: {exc}") from exc

        payload = {
            "mode": "apply" if options["apply"] else "dry-run",
            "status": result.status,
            "batch_id": result.batch_id,
            "file_sha256": document.file_sha256,
            "schema": document.schema_name,
            "model": {
                "id": document.model_id,
                "sha256": document.model_sha256,
            },
            "camera": document.camera,
            "source": document.source,
            "analytics_scope": document.analytics_scope,
            "event_count": len(document.events),
            "first_captured_at": document.first_captured_at,
            "last_captured_at": document.last_captured_at,
            "per_day": document.per_day,
        }
        self.stdout.write(
            json.dumps(payload, cls=DjangoJSONEncoder, ensure_ascii=False, sort_keys=True)
        )
