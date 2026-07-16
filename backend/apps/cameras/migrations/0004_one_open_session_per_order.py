from django.db import migrations, models
from django.db.models import Count
from django.utils import timezone


def close_duplicate_order_sessions(apps, schema_editor):
    Session = apps.get_model("cameras", "AiCountingSession")
    duplicates = (
        Session.objects.filter(status__in=["starting", "active"])
        .values("order_id")
        .annotate(total=Count("id"))
        .filter(total__gt=1)
    )
    for row in duplicates:
        sessions = Session.objects.filter(
            order_id=row["order_id"], status__in=["starting", "active"]
        ).order_by("-started_at")
        keep = sessions.first()
        sessions.exclude(pk=keep.pk).update(
            status="failed",
            ended_at=timezone.now(),
            error="Closed while enforcing one active camera per order",
        )


class Migration(migrations.Migration):

    dependencies = [
        ("cameras", "0003_remove_aicountingsession_cameras_one_open_ai_counting_session_and_more"),
    ]

    operations = [
        migrations.RunPython(close_duplicate_order_sessions, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="aicountingsession",
            constraint=models.UniqueConstraint(
                condition=models.Q(status__in=["starting", "active"]),
                fields=("order",),
                name="cameras_one_open_session_per_order",
            ),
        ),
    ]
