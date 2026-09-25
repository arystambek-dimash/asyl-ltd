"""Ответы ПК камер для тестов отгрузки: распознанные номера и подсчитанные мешки."""

from datetime import timedelta

from apps.cameras.models import ANALYTICS_SCOPE_SHIPPING, AlwaysOnCounterCursor, AlwaysOnImportedEvent


def vehicle_plate(number="123ABC02", *, accepted=True):
    return {
        "class_name": "license_plate",
        "confidence": 0.91,
        "number": number if accepted else None,
        "ocr": {
            "number": number if accepted else "",
            "accepted": accepted,
            "confidence": 0.96,
        },
    }


def wagon_plate(number="00123455", *, accepted=True, length_valid=True, checksum_valid=True):
    return {
        "class_name": "wagon_plate",
        "confidence": 0.91,
        "number": number if accepted else None,
        "ocr": {
            "digits": number,
            "accepted": accepted,
            "length_valid": length_valid,
            "checksum_valid": checksum_valid,
            "confidence": 0.96,
        },
    }


def recognition_payload(model, detections):
    return {
        "ok": True,
        "ocr": True,
        "task": "wagon_number_recognition"
        if model == "wagon_number"
        else "vehicle_plate_recognition",
        "detections": detections,
        "number": next(
            (item["number"] for item in detections if item.get("number")), None
        ),
    }


def add_events(start, seconds, *, camera="cam2", scope=ANALYTICS_SCOPE_SHIPPING, applied=True):
    existing = AlwaysOnImportedEvent.objects.filter(camera=camera).order_by("-upstream_event_id").first()
    previous = existing.upstream_event_id if existing else 0
    rows = AlwaysOnImportedEvent.objects.bulk_create([
        AlwaysOnImportedEvent(camera=camera, upstream_event_id=previous+i+1, occurred_at=start+timedelta(seconds=second), source="sub", mode="always_on", analytics_scope=scope, applied_to_analytics=applied)
        for i, second in enumerate(seconds)
    ])
    AlwaysOnCounterCursor.objects.update_or_create(camera=camera, defaults={
        "last_event_id": rows[-1].upstream_event_id, "last_total": previous+len(rows),
        "event_sync_supported": True, "event_boundary_validated": True,
        "event_caught_up_at": start+timedelta(seconds=max(seconds)), "event_sync_error": "", "event_sync_failed_at": None,
    })
    return rows
