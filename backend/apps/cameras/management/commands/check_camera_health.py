import json
from datetime import datetime, timezone

from django.core.management.base import BaseCommand
from django.core.serializers.json import DjangoJSONEncoder

from apps.cameras import health


class Command(BaseCommand):
    help = "Check the durable camera-monitor heartbeat (0 ok, 2 stale, 3 outage)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--max-age",
            type=int,
            default=health.STALE_SECONDS,
            help="Maximum heartbeat age in seconds",
        )
        parser.add_argument(
            "--require-since-epoch",
            type=float,
            default=None,
            help="Reject a heartbeat recorded before this Unix timestamp",
        )
        parser.add_argument(
            "--fail-on-degraded",
            action="store_true",
            help="Return exit 4 when at least one expected stream is unavailable",
        )

    def handle(self, *args, **options):
        required_since = (
            datetime.fromtimestamp(options["require_since_epoch"], tz=timezone.utc)
            if options["require_since_epoch"] is not None
            else None
        )
        payload = health.state_payload(
            max_age=max(1, options["max_age"]), required_since=required_since
        )
        self.stdout.write(json.dumps(payload, cls=DjangoJSONEncoder, sort_keys=True))
        code = health.exit_code(
            payload, fail_on_degraded=options["fail_on_degraded"]
        )
        if code:
            raise SystemExit(code)
