"""Durable evidence delivery independent of OCR success and trip assignment.

Only UUID-bound frames are retried. A live snapshot is allowed once, immediately
after a new sample, and fenced against observed departure. Never photograph a
later truck to fill an earlier truck's missing photo.
"""

from datetime import timedelta
from uuid import UUID

from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q, Case, When, Value, IntegerField
from django.utils import timezone

from apps.cameras import ai as camera_ai

from .models import (
    PassageScaleAutomationState,
    UnassignedWeighing,
    WeighingPhotoDelivery,
    WeighingRecord,
)

EMPTY_PHOTO = Q(photo="") | Q(photo__isnull=True)


def queue_photo(camera, request_id, *, capture=None):
    if not request_id or not camera:
        return None
    try:
        key = UUID(str(request_id))
    except (ValueError, TypeError, AttributeError):
        return None
    job, _ = WeighingPhotoDelivery.objects.get_or_create(
        request_id=key,
        defaults={"camera": camera, "capture": capture},
    )
    return job


def _link_photo(job):
    if not job.photo:
        return
    # Assignment may have happened during network I/O. Update only evidence.
    for model in (WeighingRecord, UnassignedWeighing):
        model.objects.filter(EMPTY_PHOTO, photo_request_id=job.request_id).update(
            photo=job.photo.name,
        )


@transaction.atomic
def _claim(job_id, now):
    job = WeighingPhotoDelivery.objects.select_for_update().get(pk=job_id)
    if job.photo:
        _link_photo(job)
        return None
    if job.status == "unavailable" or job.next_attempt_at > now:
        return None
    if job.lease_until and job.lease_until > now:
        return None
    job.lease_until = now + timedelta(seconds=60)
    job.attempts += 1
    job.save(update_fields=["lease_until", "attempts", "updated_at"])
    return job


def _snapshot_allowed(capture, now):
    return (
        capture is not None
        and capture.departure_observed_at is None
        and capture.cleared_at is None
        and capture.recognition_valid_until is None
        and capture.stable_weight_at is not None
        and 0 <= (now - capture.stable_weight_at).total_seconds() <= 5
        and PassageScaleAutomationState.objects.filter(current_capture=capture).exists()
    )


def deliver_photo(job_id, *, now=None):
    now = now or timezone.now()
    job = _claim(job_id, now)
    if job is None:
        return False
    frame = None
    error = "frame_not_available"
    capture = job.capture
    if not job.snapshot_attempted:
        WeighingPhotoDelivery.objects.filter(pk=job.pk).update(snapshot_attempted=True)
        if _snapshot_allowed(capture, timezone.now()):
            frame = camera_ai.camera_frame_jpeg(f"{job.camera}main")
            capture.refresh_from_db()
            if not _snapshot_allowed(capture, timezone.now()):
                frame = None
    if frame is None:
        try:
            frame = camera_ai.fetch_vehicle_recognition_frame(
                job.camera, str(job.request_id)
            )
        except (camera_ai.AiUnavailable, camera_ai.AiError, ValueError):
            error = "camera_unavailable"
        if frame and capture:
            capture.refresh_from_db()
            cutoffs = [
                value
                for value in (
                    capture.departure_observed_at,
                    capture.recognition_valid_until,
                )
                if value
            ]
            if cutoffs and (
                capture.recognized_at is None or capture.recognized_at >= min(cutoffs)
            ):
                frame = None
                error = "frame_after_occupancy_gap"
    fallback = None
    if not frame and job.capture_id:
        fallback = (
            WeighingPhotoDelivery.objects.filter(
                capture_id=job.capture_id,
                status="saved",
            )
            .exclude(EMPTY_PHOTO)
            .order_by("id")
            .first()
        )
    with transaction.atomic():
        locked = WeighingPhotoDelivery.objects.select_for_update().get(pk=job.pk)
        if locked.lease_until != job.lease_until:
            return False
        if frame:
            try:
                locked.photo.save(
                    f"{job.request_id}.jpg", ContentFile(frame), save=False
                )
            except OSError:
                error = "storage_unavailable"
                locked.photo = None
        elif fallback:
            locked.photo = fallback.photo.name
        if locked.photo:
            locked.status = "saved"
            locked.error_code = ""
        else:
            locked.status = (
                "unavailable"
                if now - locked.created_at >= timedelta(days=7)
                else "retrying"
            )
            locked.error_code = error
            delays = (5, 15, 60, 300, 1800)
            locked.next_attempt_at = now + timedelta(
                seconds=delays[min(locked.attempts - 1, 4)]
            )
        locked.lease_until = None
        locked.save()
        _link_photo(locked)
        return bool(locked.photo)


def retry_due_photos(*, limit=1):
    now = timezone.now()
    ids = list(
        WeighingPhotoDelivery.objects.filter(
            status__in=["pending", "retrying"],
            next_attempt_at__lte=now,
        )
        .filter(Q(lease_until__isnull=True) | Q(lease_until__lte=now))
        .annotate(
            priority=Case(
                When(snapshot_attempted=False, then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            )
        )
        .order_by("priority", "next_attempt_at", "id")
        .values_list("id", flat=True)[:limit]
    )
    return sum(deliver_photo(pk) for pk in ids)


def attach_photo(camera, request_id):
    job = queue_photo(camera, request_id)
    if job is None:
        return False
    if job.photo:
        _link_photo(job)
        return True
    return deliver_photo(job.pk)


def link_available_photo(camera, request_id):
    job = queue_photo(camera, request_id)
    if job is not None:
        _link_photo(job)


def photo_delivery_status(record):
    if record.photo:
        return "saved"
    if not record.photo_request_id:
        return "unavailable"
    job = WeighingPhotoDelivery.objects.filter(
        request_id=record.photo_request_id
    ).first()
    return job.status if job else "pending"
