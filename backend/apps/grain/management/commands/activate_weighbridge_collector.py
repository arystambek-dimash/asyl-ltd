"""Single cutover while both observers confirm an empty, idle scale."""
import os
import time
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from apps.grain.models import AutomaticPassageCapture, PassageScaleAutomationState
from apps.grain.outbox_importer import directory
from weighbridge.outbox import Outbox


class Command(BaseCommand):
    def handle(self, **options):
        if (directory() / "enabled").is_file():
            self.stdout.write("Independent collector already active; no interruption.")
            return
        with transaction.atomic():
            lane = PassageScaleAutomationState.objects.select_for_update().get(scale_number="truck")
            if AutomaticPassageCapture.objects.filter(status="processing").exists():
                raise CommandError("Pending capture: cutover deferred")
            box = Outbox(directory())
            heartbeat = box.state("heartbeat") or {}
            if (time.time() - heartbeat.get("updated_at", 0) > 2
                    or not heartbeat.get("clear") or not heartbeat.get("armed")):
                raise CommandError("Scale must be freshly confirmed clear: cutover deferred")
            box.state("config", {"stable_weight_seconds": lane.stable_weight_seconds})
            # Commit the handoff marker on the same durable disk as the queue.
            marker = directory() / "enabled"
            with marker.open("w") as stream:
                stream.write("1\n")
                stream.flush()
                os.fsync(stream.fileno())
            parent = os.open(directory(), os.O_RDONLY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
            box.incident("collector_activated_on_clear_scale")
        self.stdout.write("Independent collector activated; durable outbox enabled.")
