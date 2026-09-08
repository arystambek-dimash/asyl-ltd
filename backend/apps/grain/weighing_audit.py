"""Read-only coverage report and bounded vision checks on saved photographs."""

from datetime import timedelta

from django.conf import settings
from django.db.models import Count, Q
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

HAS_PHOTO = ~Q(photo="") & Q(photo__isnull=False)


def _counts(rows, field="status"):
    return dict(rows.values_list(field).annotate(n=Count("pk")))


def snapshot(*, now=None, hours=24, sample_limit=3):
    now = now or timezone.now()
    lower = now - timedelta(hours=hours)
    captures = AutomaticPassageCapture.objects.filter(started_at__gte=lower)
    saved = captures.filter(weight_kg__isnull=False, stable_weight_at__isnull=False)
    uncovered = saved.exclude(status="processing").filter(
        wagon_id__isnull=True, unassigned_weighing__isnull=True
    )
    queue = UnassignedWeighing.objects.filter(status="open")
    checks = WeighingIdentityCheck.objects.filter(weighing__status="open")
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
                status="processing", updated_at__lt=now - timedelta(minutes=10)
            ).values_list("pk", flat=True)[:50]
        ),
        "captures_without_stable_weight": captures.filter(
            weight_kg__isnull=True
        ).count(),
        "manual_queue_count": queue.count(),
        "manual_queue_reasons": _counts(queue, "reason"),
        "manual_queue_without_photo": queue.exclude(HAS_PHOTO).count(),
        "recorded_weighings_without_photo": records.exclude(HAS_PHOTO).count(),
        "identity_checks": _counts(checks),
        "photo_delivery": _counts(photos),
    }
    # Prefer complete saved entry/exit pairs. Never make a fresh camera request.
    exits = list(records.filter(HAS_PHOTO, kind="tare").select_related("wagon")[:30])
    entries = {}
    for row in WeighingRecord.objects.filter(
        HAS_PHOTO,
        wagon_id__in=[row.wagon_id for row in exits],
        kind="gross",
        source="scale",
    ).order_by("-id"):
        entries.setdefault(row.wagon_id, row)
    samples = []
    for row in exits:
        if row.wagon_id not in entries:
            continue
        samples.append((row, entries[row.wagon_id], row.wagon.number))
        if len(samples) >= sample_limit:
            break
    # Supplement with unassigned photographs when no complete pair exists.
    for row in queue.filter(HAS_PHOTO)[:sample_limit]:
        if len(samples) >= sample_limit:
            break
        samples.append((row, None, row.vehicle_number))
    return report, samples


def probe(samples):
    """Read plates/appearance only; never call the booking worker or save models."""
    results = []
    for item, entry, reference in samples:
        result = {
            "exit_id": item.pk,
            "source": "record" if isinstance(item, WeighingRecord) else "unassigned",
            "reference_ocr": reference,
            "reference_is_ground_truth": False,
        }
        entries = (
            []
            if entry is None
            else [({"key": f"record:{entry.pk}", "number": reference}, entry)]
        )
        try:
            verdict, _ = weighing_identity.request_verification(item, entries)
            reading = verdict.get("exit", {})
            result.update(
                {
                    "plate": reading.get("plate"),
                    "normalized_plate": weighing_identity.normalized_plate(
                        reading.get("plate")
                    ),
                    "plate_clear": reading.get("plate_clear"),
                    "orientation": reading.get("orientation"),
                    "agrees_with_reference": bool(reference)
                    and weighing_identity.normalized_plate(reading.get("plate"))
                    == reference,
                    "pair_reading_matches": (
                        bool(weighing_identity.choose(verdict, entries, reference))
                        if entry is not None
                        else None
                    ),
                    "appearance": [
                        row.get("appearance") for row in verdict.get("entries", [])
                    ],
                }
            )
        except Exception as exc:
            # Do not expose network exception text, headers or image bytes.
            result["error_type"] = type(exc).__name__
        results.append(result)
    return results
