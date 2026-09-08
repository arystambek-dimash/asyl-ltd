from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.core.files.base import ContentFile
from django.utils import timezone

from apps.grain import statuses as st, weighing_audit as audit
from apps.grain.models import (
    AutomaticPassageCapture as Capture,
    UnassignedWeighing,
    Wagon,
    WeighingRecord,
)

pytestmark = pytest.mark.django_db


def capture(**extra):
    return Capture.objects.create(
        idempotency_key=uuid4(),
        camera="cam1",
        weight_kg=4000,
        stable_weight_at=timezone.now(),
        **extra,
    )


def test_audit_distinguishes_saved_untracked_and_processing_weights():
    missing = capture(status="completed")
    parked = capture(status="failed")
    UnassignedWeighing.objects.create(
        capture=parked, weight_kg=4000, stable_weight_at=timezone.now()
    )
    busy = capture()
    Capture.objects.filter(pk=busy.pk).update(
        updated_at=timezone.now() - timedelta(minutes=11)
    )
    Capture.objects.create(idempotency_key=uuid4(), camera="cam1", status="failed")
    report, _ = audit.snapshot()
    assert report["saved_weight_count"] == 3
    assert report["uncovered_saved_weight_ids"] == [missing.pk]
    assert report["processing_over_10_minutes"] == [busy.pk]
    assert report["captures_without_stable_weight"] == 1
    assert report["manual_queue_count"] == report["manual_queue_without_photo"] == 1
    assert Capture.objects.get(pk=missing.pk).status == "completed"
    assert UnassignedWeighing.objects.get(capture=parked).status == "open"


def test_probes_saved_pair_without_mutating_trip_or_exposing_key(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.OPENAI_API_KEY = "private-test-secret"
    wagon = Wagon.objects.create(
        number="934PPB13", direction="passage", status=st.COMPLETED
    )
    entry = WeighingRecord.objects.create(
        wagon=wagon, kind="gross", source="scale", weight_kg=4000
    )
    exit = WeighingRecord.objects.create(
        wagon=wagon, kind="tare", source="scale", weight_kg=8000
    )
    for row in (entry, exit):
        row.photo.save("frame.jpg", ContentFile(b"saved-image"))
    report, samples = audit.snapshot()
    verdict = {
        "exit": {"plate": "934PPB13", "plate_clear": True, "orientation": "rear"},
        "entries": [
            {
                "key": f"record:{entry.pk}",
                "plate": "934PPB13",
                "plate_clear": True,
                "orientation": "front",
                "appearance": "same",
                "visual_evidence": "Совпадают повреждения",
            }
        ],
    }
    with patch.object(
        audit.weighing_identity, "request_verification", return_value=(verdict, "resp")
    ):
        results = audit.probe(samples)
    assert results[0]["pair_reading_matches"] is True
    assert results[0]["reference_is_ground_truth"] is False
    assert "private-test-secret" not in str(report) + str(results)
    wagon.refresh_from_db()
    assert wagon.status == st.COMPLETED
    assert WeighingRecord.objects.count() == 2


def test_network_error_does_not_expose_exception_message():
    item = UnassignedWeighing(id=5, vehicle_number="934PPB13")
    with patch.object(
        audit.weighing_identity,
        "request_verification",
        side_effect=OSError("private header"),
    ):
        results = audit.probe([(item, None, item.vehicle_number)])
    assert results[0]["error_type"] == "OSError"
    assert "private header" not in str(results)


def test_legacy_uuid_links_are_covered_but_similar_plates_are_not():
    booked = capture(status="completed", attempt_request_id=uuid4())
    queued = capture(status="completed")
    missing = capture(status="completed", vehicle_number="934PPB13")
    wagon = Wagon.objects.create(number="934PPB13", direction="passage")
    WeighingRecord.objects.create(
        wagon=wagon,
        kind="tare",
        weight_kg=4000,
        photo_request_id=booked.attempt_request_id,
    )
    UnassignedWeighing.objects.create(
        weight_kg=4000,
        stable_weight_at=timezone.now(),
        photo_request_id=queued.idempotency_key,
        status="discarded",
    )
    report, _ = audit.snapshot()
    assert report["legacy_photo_link_count"] == 2
    assert report["uncovered_saved_weight_ids"] == [missing.pk]
