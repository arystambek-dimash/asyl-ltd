from datetime import timedelta

from django.db import migrations
from django.db.models import Q
from django.utils import timezone


def queue_existing(apps, schema_editor):
    jobs = apps.get_model("grain", "WeighingPhotoDelivery").objects.using(
        schema_editor.connection.alias
    )
    cutoff = timezone.now() - timedelta(days=7)
    captures = apps.get_model("grain", "AutomaticPassageCapture").objects.using(
        schema_editor.connection.alias
    )
    for name, camera_field in (
        ("WeighingRecord", "photo_camera"),
        ("UnassignedWeighing", "camera"),
    ):
        rows = apps.get_model("grain", name).objects.using(
            schema_editor.connection.alias
        )
        for row in (
            rows.filter(photo_request_id__isnull=False)
            .exclude(**{camera_field: ""})
            .iterator(chunk_size=500)
        ):
            capture = captures.filter(
                Q(idempotency_key=row.photo_request_id)
                | Q(attempt_request_id=row.photo_request_id)
            ).first()
            job, _ = jobs.get_or_create(
                request_id=row.photo_request_id,
                defaults={
                    "camera": getattr(row, camera_field),
                    "capture_id": capture.pk if capture else None,
                    "photo": row.photo.name or "",
                    "snapshot_attempted": True,
                    "status": (
                        "saved"
                        if row.photo
                        else ("pending" if row.created_at >= cutoff else "unavailable")
                    ),
                    "error_code": "" if row.photo else "historical_photo_missing",
                },
            )
            if row.photo and not job.photo:
                job.photo = row.photo.name
                job.status = "saved"
                job.error_code = ""
                job.save(update_fields=["photo", "status", "error_code"])


class Migration(migrations.Migration):
    dependencies = [
        ("grain", "0014_automaticpassagecapture_departure_observed_at_and_more")
    ]
    operations = [migrations.RunPython(queue_existing, migrations.RunPython.noop)]
