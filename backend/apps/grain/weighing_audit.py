"""Read-only coverage report and bounded vision checks on saved photographs."""

from datetime import timedelta

from django.conf import settings
from django.db.models import Count, Exists, OuterRef, Q
from django.utils import timezone
from apps.eventlog.models import EventLog

from . import weighing_identity
from .models import (
    AutomaticPassageCapture,
    PassageScaleAutomationState,
    UnassignedWeighing,
    Wagon,
    WeighingIdentityCheck,
    WeighingPhotoDelivery,
    WeighingRecord,
)
from .weighing_photos import EMPTY_PHOTO


def _counts(rows, field="status"):
    return dict(rows.values_list(field).annotate(n=Count("pk")))


def snapshot(*, now=None, hours=24, sample_limit=3):
    now = now or timezone.now()
    lower = now - timedelta(hours=hours)
    captures = AutomaticPassageCapture.objects.filter(started_at__gte=lower)
    saved = captures.filter(weight_kg__isnull=False, stable_weight_at__isnull=False)
    unlinked = saved.exclude(status=AutomaticPassageCapture.PROCESSING).filter(
        wagon_id__isnull=True, unassigned_weighing__isnull=True
    )
    # Older/manual bookings retain the immutable photo request UUID even when
    # their direct capture FK was never populated. Never infer links by plate.
    frame_match = Q(photo_request_id=OuterRef("idempotency_key")) | Q(
        photo_request_id=OuterRef("attempt_request_id")
    )
    uncovered = unlinked.filter(
        ~Exists(WeighingRecord.objects.filter(frame_match)),
        ~Exists(UnassignedWeighing.objects.filter(frame_match)),
    )
    queue = UnassignedWeighing.objects.filter(status=UnassignedWeighing.OPEN)
    checks = WeighingIdentityCheck.objects.filter(weighing__status=UnassignedWeighing.OPEN)
    photos = WeighingPhotoDelivery.objects.filter(created_at__gte=lower)
    records = WeighingRecord.objects.filter(
        wagon__direction=Wagon.PASSAGE, source="scale", created_at__gte=lower
    )
    lanes = list(
        PassageScaleAutomationState.objects.values(
            "scale_number", "phase", "updated_at"
        )
    )
    for lane in lanes:
        lane["updated_at"] = lane["updated_at"].isoformat()
    report = {
        "at": now.isoformat(),
        "hours": hours,
        "scope": "saved_scale_observations; physical traffic count is not known",
        "config": {
            "automatic_scale_enabled": settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED,
            "openai_key_configured": bool(settings.OPENAI_API_KEY),
            "vision_enabled": weighing_identity.enabled(),
            "model": settings.WEIGHING_AI_MODEL,
        },
        "lane": lanes,
        "captures": _counts(captures),
        "saved_weight_count": saved.count(),
        "uncovered_saved_weight_count": uncovered.count(),
        "uncovered_saved_weight_ids": list(uncovered.values_list("pk", flat=True)[:50]),
        "uncovered_details": list(
            uncovered.values(
                "id",
                "action",
                "vehicle_number",
                "weight_kg",
                "scale_updated_at",
                "vehicle_plate_event_id",
                "error_code",
            )[:50]
        ),
        "deleted_visits": list(
            EventLog.objects.filter(
                event_type="grain_wagon_deleted", created_at__gte=lower
            ).values("id", "payload")[:100]
        ),
        "uncovered_weight_audit": list(
            EventLog.objects.filter(
                event_type="grain_weighing",
                payload__scale_updated_at__in=list(
                    uncovered.exclude(scale_updated_at="").values_list(
                        "scale_updated_at", flat=True
                    )
                ),
            ).values("id", "payload")[:100]
        ),
        "processing_over_10_minutes": list(
            saved.filter(
                status=AutomaticPassageCapture.PROCESSING, updated_at__lt=now - timedelta(minutes=10)
            ).values_list("pk", flat=True)[:50]
        ),
        "captures_without_stable_weight": captures.filter(
            weight_kg__isnull=True
        ).count(),
        "manual_queue_count": queue.count(),
        "manual_queue_reasons": _counts(queue, "reason"),
        "manual_queue_without_photo": queue.filter(EMPTY_PHOTO).count(),
        "recorded_weighings_without_photo": records.filter(EMPTY_PHOTO).count(),
        "identity_checks": _counts(checks),
        "verified_saved_exit_present": WeighingIdentityCheck.objects.filter(
            status=WeighingIdentityCheck.MATCHED, updated_at__gte=lower
        ).exists(),
        "identity_review_reasons": _counts(checks.filter(status=WeighingIdentityCheck.REVIEW), "reason"),
        "photo_delivery": _counts(photos),
    }
    # Saved frames only: never make a fresh camera request.
    samples = [
        (row, row.wagon.number)
        for row in records.exclude(EMPTY_PHOTO)
        .select_related("wagon")
        .order_by("-id")[:sample_limit]
    ]
    for row in queue.exclude(EMPTY_PHOTO)[: sample_limit - len(samples)]:
        samples.append((row, row.vehicle_number))
    return report, samples


def probe(samples):
    """Read plates/orientation only; never call the booking worker or save models."""
    results = []
    for item, reference in samples:
        result = {
            "weighing_id": item.pk,
            "source": "record" if isinstance(item, WeighingRecord) else "unassigned",
            "reference_ocr": reference,
            "reference_is_ground_truth": False,
        }
        try:
            verdict, _ = weighing_identity.request_verification(item)
            reading = verdict.get("exit", {})
            plate = weighing_identity.normalized_plate(reading.get("plate"))
            result.update(
                {
                    "plate": reading.get("plate"),
                    "normalized_plate": plate,
                    "plate_clear": reading.get("plate_clear"),
                    "orientation": reading.get("orientation"),
                    "agrees_with_reference": bool(reference) and plate == reference,
                }
            )
        except Exception as exc:
            # Do not expose network exception text, headers or image bytes.
            result["error_type"] = type(exc).__name__
        results.append(result)
    return results


def public_summary(report):
    """Allowlist health signals only: Actions logs may be publicly accessible."""
    return {
        "ok": report["ok"],
        "automatic_scale_enabled": bool(report["config"]["automatic_scale_enabled"]),
        "vision_enabled": bool(report["config"]["vision_enabled"]),
        "saved_weights_accounted_for": report["uncovered_saved_weight_count"] == 0,
        "processing_stalled": bool(report["processing_over_10_minutes"]),
        "manual_review_pending": bool(report["manual_queue_count"]),
        "missing_photos": bool(
            report["manual_queue_without_photo"]
            or report["recorded_weighings_without_photo"]
        ),
        "scale_ready": report.get("scale_probe", {}).get("state") == "ready",
        "scale_data_stale": report.get("scale_probe", {}).get("state") == "stale",
        "verified_saved_exit_present": bool(report["verified_saved_exit_present"]),
        "vision_samples_passed": bool(report["vision_samples"])
        and all("error_type" not in row for row in report["vision_samples"]),
    }
