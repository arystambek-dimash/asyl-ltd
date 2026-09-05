"""The orientation dataset collects itself from trips and weights."""

import json
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch
from uuid import uuid4

import pytest
from apps.cameras import ai as camera_ai
from apps.grain import orientation_dataset as dataset
from apps.grain import statuses as st
from apps.grain.models import (
    UnassignedWeighing,
    VehicleOrientationSample,
    Wagon,
    WeighingRecord,
)
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.utils import timezone

pytestmark = pytest.mark.django_db

JPEG = b"\xff\xd8\xff\xe0" + b"1" * 32


@pytest.fixture(autouse=True)
def dataset_settings(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.VEHICLE_ORIENTATION_DATASET_ENABLED = True
    settings.VEHICLE_ORIENTATION_EMPTY_MAX_KG = 5000
    settings.VEHICLE_ORIENTATION_LOADED_MIN_KG = 6000
    settings.VEHICLE_ORIENTATION_EXPORT_BATCH = 100
    settings.VEHICLE_ORIENTATION_SAMPLE_MAX_AGE_DAYS = 60


def _trip(number="854ANB13", *, status=st.AT_SILO, gross=None, tare=None, direction=Wagon.PASSAGE):
    return Wagon.objects.create(
        number=number,
        direction=direction,
        workflow="simple",
        cargo_name="Отруби",
        status=status,
        arrived_at=timezone.now() - timedelta(hours=1),
        gross_weight_kg=gross,
        tare_weight_kg=tare,
        number_source="camera",
    )


def _record(wagon, kind, weight, *, orientation="", photo=True):
    record = WeighingRecord.objects.create(
        wagon=wagon, kind=kind, weight_kg=weight, source="scale", orientation=orientation
    )
    if photo:
        record.photo.save(f"{uuid4()}.jpg", ContentFile(JPEG), save=True)
    return record


def _unassigned(weight, *, status=UnassignedWeighing.OPEN, action="", wagon=None, orientation=""):
    item = UnassignedWeighing.objects.create(
        weight_kg=weight,
        stable_weight_at=timezone.now() - timedelta(minutes=30),
        scale_number="truck",
        scale_age_seconds=Decimal("0.2"),
        camera="cam1",
        photo_request_id=uuid4(),
        status=status,
        action=action,
        wagon=wagon,
        orientation=orientation,
    )
    item.photo.save(f"{item.photo_request_id}.jpg", ContentFile(JPEG), save=True)
    return item


@pytest.mark.parametrize(
    ("weight", "expected"),
    [(4999, "front"), (5000, None), (5500, None), (6000, None), (6001, "rear")],
)
def test_weight_rule_has_a_dead_zone(weight, expected):
    label = dataset.label_for_weight(weight)
    assert (label.value if label else None) == expected
    if label:
        assert label.source == "weight"


def test_completed_trip_labels_by_role_even_for_a_heavy_empty_truck():
    kamaz = _trip(status=st.COMPLETED, gross=8500, tare=20000)
    entry = _record(kamaz, "gross", 8500)
    exit_record = _record(kamaz, "tare", 20000)

    assert dataset.label_weighing(entry) == dataset.Label("front", "trip")
    assert dataset.label_weighing(exit_record) == dataset.Label("rear", "trip")


def test_open_and_cancelled_trips_fall_back_to_weight_or_nothing():
    open_trip = _trip(status=st.AT_SILO, gross=3880)
    cancelled = _trip("676VEA13", status=st.CANCELLED, gross=8320)

    assert dataset.label_weighing(_record(open_trip, "gross", 3880)) == dataset.Label("front", "weight")
    assert dataset.label_weighing(_record(cancelled, "gross", 8320)) is None
    intake = _trip("111AAA01", direction=Wagon.INTAKE, gross=30000)
    assert dataset.label_weighing(_record(intake, "gross", 30000)) is None


def test_unassigned_labels_follow_the_trip_once_assigned():
    done = _trip(status=st.COMPLETED, gross=3880, tare=8760)
    assigned_exit = _unassigned(8760, status=UnassignedWeighing.ASSIGNED, action="exit", wagon=done)
    parked = _unassigned(3900)
    discarded = _unassigned(9000, status=UnassignedWeighing.DISCARDED)

    assert dataset.label_unassigned(assigned_exit) == dataset.Label("rear", "trip")
    assert dataset.label_unassigned(parked) == dataset.Label("front", "weight")
    assert dataset.label_unassigned(discarded) is None


def test_collect_creates_rows_and_holds_back_model_conflicts():
    trip = _trip(status=st.COMPLETED, gross=3880, tare=8760)
    entry = _record(trip, "gross", 3880, orientation="front")
    contradicted = _record(trip, "tare", 8760, orientation="front")  # model said front for the exit
    _record(trip, "tare", 8760, photo=False)  # no photo: nothing to learn from
    parked = _unassigned(3900)

    counters = dataset.collect()

    assert counters == {"created": 3, "updated": 0, "unchanged": 0, "unlabelled": 0}
    rows = {row.sample_id: row for row in VehicleOrientationSample.objects.all()}
    assert rows[f"weighing-{entry.pk}"].conflict is False
    assert rows[f"weighing-{contradicted.pk}"].conflict is True
    assert rows[f"weighing-{contradicted.pk}"].model_orientation == "front"
    assert rows[f"unassigned-{parked.pk}"].label == "front"
    assert dataset.collect()["unchanged"] == 3


def test_a_corrected_trip_relabels_and_resends_the_frame():
    trip = _trip(status=st.AT_SILO, gross=8760)  # booked as entry by mistake
    record = _record(trip, "gross", 8760)
    dataset.collect()
    sample = VehicleOrientationSample.objects.get()
    assert (sample.label, sample.label_source) == ("rear", "weight")
    sample.sent_at = timezone.now()
    sample.save(update_fields=["sent_at"])

    # The operator swapped the weights: this frame is now a completed trip's exit.
    Wagon.objects.filter(pk=trip.pk).update(status=st.COMPLETED, gross_weight_kg=3880, tare_weight_kg=8760)
    WeighingRecord.objects.filter(pk=record.pk).update(kind="tare")

    assert dataset.collect()["updated"] == 1
    sample.refresh_from_db()
    assert (sample.label, sample.label_source, sample.sent_at) == ("rear", "trip", None)


def test_export_posts_frames_and_stops_when_camera_pc_is_down():
    trip = _trip(status=st.COMPLETED, gross=3880, tare=8760)
    entry = _record(trip, "gross", 3880)
    exit_record = _record(trip, "tare", 8760)
    dataset.collect()

    with patch.object(camera_ai, "post_orientation_sample", return_value={"ok": True}) as post:
        counters = dataset.export_pending(limit=10)

    assert counters == {"sent": 2, "failed": 0, "missing": 0, "unavailable": 0}
    sent = {call.kwargs["sample_id"]: call.kwargs for call in post.call_args_list}
    assert sent[f"weighing-{entry.pk}"]["label"] == "front"
    assert sent[f"weighing-{entry.pk}"]["jpeg"] == JPEG
    assert sent[f"weighing-{entry.pk}"]["weight_kg"] == 3880
    assert sent[f"weighing-{exit_record.pk}"]["label"] == "rear"
    assert VehicleOrientationSample.objects.filter(sent_at__isnull=True).count() == 0

    # Nothing pending: a second export is a no-op.
    with patch.object(camera_ai, "post_orientation_sample") as post:
        assert dataset.export_pending(limit=10)["sent"] == 0
    post.assert_not_called()

    VehicleOrientationSample.objects.update(sent_at=None)
    with patch.object(
        camera_ai,
        "post_orientation_sample",
        side_effect=camera_ai.AiUnavailable("timed out"),
    ) as post:
        counters = dataset.export_pending(limit=10)
    assert counters["unavailable"] == 1
    assert post.call_count == 1  # stop at the first transport failure
    assert VehicleOrientationSample.objects.filter(sent_at__isnull=True).count() == 2


def test_export_records_rejections_and_skips_frames_without_a_file():
    trip = _trip(status=st.COMPLETED, gross=3880, tare=8760)
    rejected = _record(trip, "gross", 3880)
    lost = _record(trip, "tare", 8760)
    dataset.collect()
    lost.photo.delete(save=False)  # file gone, reference kept
    WeighingRecord.objects.filter(pk=lost.pk).update(photo="grain/missing.jpg")

    with patch.object(
        camera_ai,
        "post_orientation_sample",
        side_effect=camera_ai.AiError(400, "label must be front or rear", {}),
    ):
        counters = dataset.export_pending(limit=10)

    assert counters == {"sent": 0, "failed": 1, "missing": 1, "unavailable": 0}
    rows = {row.record_id: row for row in VehicleOrientationSample.objects.all()}
    assert "label must be" in rows[rejected.pk].last_error
    assert rows[lost.pk].last_error == "photo_missing"
    with patch.object(camera_ai, "post_orientation_sample", return_value={"ok": True}) as post:
        dataset.export_pending(limit=10)
    assert [call.kwargs["sample_id"] for call in post.call_args_list] == [f"weighing-{rejected.pk}"]


def test_run_can_be_disabled_and_the_command_reports_json(settings):
    settings.VEHICLE_ORIENTATION_DATASET_ENABLED = False
    assert dataset.run() == {"enabled": False}

    settings.VEHICLE_ORIENTATION_DATASET_ENABLED = True
    trip = _trip(status=st.COMPLETED, gross=3880, tare=8760)
    _record(trip, "gross", 3880)
    out = StringIO()
    with patch.object(camera_ai, "post_orientation_sample", return_value={"ok": True}):
        call_command("export_orientation_samples", stdout=out)
    summary = json.loads(out.getvalue())
    assert summary["enabled"] is True
    assert summary["created"] == 1
    assert summary["sent"] == 1
    assert summary["conflicts"] == 0


def test_client_sends_the_frame_with_metadata_headers():
    captured = {}

    def fake_request(method, path, body=None, **kwargs):
        captured.update(method=method, path=path, body=body, **kwargs)
        return 200, {"ok": True, "created": True}

    with patch.object(camera_ai, "_request", side_effect=fake_request):
        payload = camera_ai.post_orientation_sample(
            sample_id="weighing-5",
            label="rear",
            jpeg=JPEG,
            weight_kg=8760,
            captured_at="2026-09-05T07:00:00+00:00",
        )

    assert payload["created"] is True
    assert (captured["method"], captured["path"]) == ("POST", "/vehicle-orientation/samples")
    assert captured["raw_body"] == JPEG
    assert captured["content_type"] == "image/jpeg"
    assert captured["extra_headers"] == {
        "X-Sample-Id": "weighing-5",
        "X-Sample-Label": "rear",
        "X-Sample-Source": "crm",
        "X-Sample-Weight-Kg": "8760",
        "X-Sample-Captured-At": "2026-09-05T07:00:00+00:00",
    }
    with pytest.raises(ValueError):
        camera_ai.post_orientation_sample(sample_id="x", label="side", jpeg=JPEG)


def test_manual_label_wins_over_automatic_rules_and_is_resent():
    trip = _trip(status=st.COMPLETED, gross=3880, tare=8760)
    record = _record(trip, "gross", 3880)
    dataset.collect()
    sample = VehicleOrientationSample.objects.get()
    sample.sent_at = timezone.now()
    sample.save(update_fields=["sent_at"])

    dataset.set_manual_label(sample, "rear", None)

    sample.refresh_from_db()
    assert (sample.label, sample.label_source, sample.sent_at) == ("rear", "manual", None)
    assert sample.reviewed_at is not None
    # The automatic rule still says "front" for a completed trip's entry, but a
    # human decided: collect() leaves the manual label alone.
    assert dataset.collect()["unchanged"] == 1
    sample.refresh_from_db()
    assert sample.label == "rear"
    with pytest.raises(ValueError):
        dataset.set_manual_label(sample, "side", None)
    assert record.pk == sample.record_id


def test_excluding_a_sent_frame_removes_it_from_camera_pc():
    trip = _trip(status=st.COMPLETED, gross=3880, tare=8760)
    _record(trip, "gross", 3880)
    never_sent = _record(trip, "tare", 8760)
    dataset.collect()
    sent, fresh = VehicleOrientationSample.objects.order_by("record_id")
    assert fresh.record_id == never_sent.pk
    sent.sent_at = timezone.now()
    sent.save(update_fields=["sent_at"])

    dataset.exclude_sample(sent, None)
    dataset.exclude_sample(fresh, None)

    sent.refresh_from_db()
    fresh.refresh_from_db()
    assert (sent.excluded, sent.removal_pending) == (True, True)
    assert (fresh.excluded, fresh.removal_pending) == (True, False)

    with (
        patch.object(camera_ai, "delete_orientation_sample", return_value=True) as delete,
        patch.object(camera_ai, "post_orientation_sample") as post,
    ):
        summary = dataset.run()

    delete.assert_called_once_with(sent.sample_id)
    post.assert_not_called()  # excluded frames are never uploaded
    assert summary["removed"] == 1
    assert summary["sent"] == 0
    sent.refresh_from_db()
    assert (sent.removal_pending, sent.sent_at) == (False, None)

    # Labelling it again puts it back into the dataset.
    dataset.set_manual_label(sent, "front", None)
    with patch.object(camera_ai, "post_orientation_sample", return_value={"ok": True}) as post:
        assert dataset.export_pending(limit=10)["sent"] == 1
    assert post.call_args.kwargs["sample_id"] == sent.sample_id


def test_delete_client_treats_404_as_already_gone():
    with patch.object(camera_ai, "_request", return_value=(404, {"error": "not found"})):
        assert camera_ai.delete_orientation_sample("weighing-1") is False
    with patch.object(camera_ai, "_request", return_value=(200, {"removed": True})) as request:
        assert camera_ai.delete_orientation_sample("weighing-1") is True
    assert request.call_args.args[:2] == ("DELETE", "/vehicle-orientation/samples/weighing-1")
    with patch.object(camera_ai, "_request", return_value=(503, {"error": "busy"})):
        with pytest.raises(camera_ai.AiError):
            camera_ai.delete_orientation_sample("weighing-1")
