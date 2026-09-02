from django.core.management.base import BaseCommand

from apps.cameras.models import AiCountingSession


class Command(BaseCommand):
    help = "Fail unless no STARTING/ACTIVE camera counting sessions exist"

    def handle(self, *args, **options):
        sessions = list(
            AiCountingSession.objects.filter(
                status__in=AiCountingSession.OPEN_STATUSES,
            )
            .order_by("id")
            .values("id", "camera", "status")
        )
        if not sessions:
            self.stdout.write("Camera contour cutover is quiescent.")
            return

        labels = ", ".join(
            f"#{row['id']} {row['camera']} ({row['status']})" for row in sessions
        )
        self.stderr.write(
            "Нельзя переключить контуры камер: завершите активные отгрузки: "
            + labels
        )
        raise SystemExit(2)
