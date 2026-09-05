import json

from django.core.management.base import BaseCommand

from apps.grain import orientation_dataset


class Command(BaseCommand):
    help = "Label scale-camera frames (front/rear) and send them to Camera-PC for retraining"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument(
            "--collect-only",
            action="store_true",
            help="only label frames into the sample table, do not contact Camera-PC",
        )

    def handle(self, *args, **options):
        if options["collect_only"]:
            result = orientation_dataset.collect(limit=options["limit"])
        else:
            result = orientation_dataset.run(limit=options["limit"])
        self.stdout.write(json.dumps(result, ensure_ascii=False))
