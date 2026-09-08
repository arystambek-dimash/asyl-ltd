from celery import shared_task

from . import orientation_dataset
from .weighing_photos import retry_due_photos


@shared_task(name="grain.retry_weighing_photos", ignore_result=True)
def retry_weighing_photos():
    return retry_due_photos(limit=3)


@shared_task(name="grain.export_orientation_samples", ignore_result=True)
def export_orientation_samples() -> dict:
    """Nightly: label the day's scale-camera frames and hand them to Camera-PC."""

    return orientation_dataset.run()
