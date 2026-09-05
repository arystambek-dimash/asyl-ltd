from celery import shared_task

from . import orientation_dataset


@shared_task(name="grain.export_orientation_samples", ignore_result=True)
def export_orientation_samples() -> dict:
    """Nightly: label the day's scale-camera frames and hand them to Camera-PC."""

    return orientation_dataset.run()
