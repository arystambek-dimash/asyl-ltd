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
    VehicleOrientationDatasetState,
    VehicleOrientationSample,
    Wagon,
    WeighingRecord,
)
from django.core.files.base import ContentFile
from django.core.management import CommandError, call_command
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
    delivered = timezone.now()
    sample.sent_at = sample.delivered_at = delivered
    sample.save(update_fields=["sent_at", "delivered_at"])

    # The operator swapped the weights: this frame is now a completed trip's exit.
    Wagon.objects.filter(pk=trip.pk).update(status=st.COMPLETED, gross_weight_kg=3880, tare_weight_kg=8760)
    WeighingRecord.objects.filter(pk=record.pk).update(kind="tare")

    assert dataset.collect()["updated"] == 1
    sample.refresh_from_db()
    assert (sample.label, sample.label_source, sample.sent_at) == ("rear", "trip", None)
    # The PC still holds the old copy: a purge must ask it to forget the frame.
    assert sample.delivered_at == delivered


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
    assert VehicleOrientationSample.objects.filter(delivered_at__isnull=True).count() == 0

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
    delivered = timezone.now()
    sample.sent_at = sample.delivered_at = delivered
    sample.save(update_fields=["sent_at", "delivered_at"])

    dataset.set_manual_label(sample, "rear", None)

    sample.refresh_from_db()
    assert (sample.label, sample.label_source, sample.sent_at) == ("rear", "manual", None)
    assert sample.delivered_at == delivered  # the PC still holds a copy
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
    sent.sent_at = sent.delivered_at = timezone.now()
    sent.save(update_fields=["sent_at", "delivered_at"])

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
    assert (sent.removal_pending, sent.sent_at, sent.delivered_at) == (False, None, None)

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


def test_dataset_clients_accept_only_2xx():
    """urllib hands a redirect back as a status tuple: the frame was not touched."""
    with patch.object(camera_ai, "_request", return_value=(307, {})):
        with pytest.raises(camera_ai.AiError) as delete_error:
            camera_ai.delete_orientation_sample("weighing-1")
        with pytest.raises(camera_ai.AiError) as clear_error:
            camera_ai.clear_orientation_samples()
        with pytest.raises(camera_ai.AiError) as post_error:
            camera_ai.post_orientation_sample(sample_id="weighing-1", label="front", jpeg=JPEG)
    assert (delete_error.value.status, clear_error.value.status, post_error.value.status) == (307, 307, 307)
    assert "307" in str(delete_error.value)
    with patch.object(camera_ai, "_request", return_value=(204, {})):
        assert camera_ai.delete_orientation_sample("weighing-1") is True
        assert camera_ai.clear_orientation_samples() == 0


# --- Очистка датасета -------------------------------------------------------


def _row(record_id, *, kind=VehicleOrientationSample.WEIGHING, label="front", source="trip", **fields):
    """Строка датасета напрямую: для очистки исходное взвешивание не нужно."""
    return VehicleOrientationSample.objects.create(
        record_kind=kind,
        record_id=record_id,
        label=label,
        label_source=source,
        weight_kg=fields.pop("weight_kg", 4000),
        captured_at=fields.pop("captured_at", None) or timezone.now(),
        **fields,
    )


def _on_pc(moment=None) -> dict:
    """Поля строки, чью копию Camera-PC держит: доставлена и не удалена."""
    moment = moment or timezone.now()
    return {"sent_at": moment, "delivered_at": moment}


def _watermark():
    return VehicleOrientationDatasetState.objects.filter(pk=1).values_list(
        "collect_since", flat=True
    ).first()


def test_purge_samples_deletes_rows_and_asks_camera_pc_only_about_delivered_frames():
    now = timezone.now()
    sent = _row(1, **_on_pc(now))
    gone_on_pc = _row(2, **_on_pc(now))
    pending = _row(3, excluded=True, removal_pending=True)
    _row(4)  # never reached Camera-PC
    # Relabelled while the PC keeps the old copy: sent_at is reset, delivered_at is not.
    relabelled = _row(5, source="manual", delivered_at=now)

    def fake_delete(sample_id):
        return sample_id != gone_on_pc.sample_id  # 404: the PC already forgot it

    with patch.object(camera_ai, "delete_orientation_sample", side_effect=fake_delete) as delete:
        result = dataset.purge_samples(VehicleOrientationSample.objects.all())

    assert result == {"deleted": 5, "removed_from_pc": 4, "pc_unavailable": False, "remaining": 0}
    assert [c.args[0] for c in delete.call_args_list] == [
        sent.sample_id, gone_on_pc.sample_id, pending.sample_id, relabelled.sample_id
    ]
    assert VehicleOrientationSample.objects.count() == 0
    assert _watermark() is None  # no cutoff given: the collector's window is untouched


def test_purge_samples_keeps_frames_camera_pc_rejected_or_could_not_reach():
    now = timezone.now()
    rejected = _row(1, conflict=True, **_on_pc(now))
    forgotten = _row(2, **_on_pc(now))
    at_outage = _row(3, **_on_pc(now))
    fresh = _row(4)
    after_outage = _row(5, **_on_pc(now))
    answers = [camera_ai.AiError(500, "disk full", {}), True, camera_ai.AiUnavailable("timed out")]

    with patch.object(camera_ai, "delete_orientation_sample", side_effect=answers) as delete:
        result = dataset.purge_samples(VehicleOrientationSample.objects.all())

    # Kept rows were looked at: they are not "remaining" for this batch.
    assert result == {"deleted": 2, "removed_from_pc": 1, "pc_unavailable": True, "remaining": 0}
    assert delete.call_count == 3  # nothing after the transport failure
    assert not VehicleOrientationSample.objects.filter(pk__in=[forgotten.pk, fresh.pk]).exists()
    kept = {row.pk: row for row in VehicleOrientationSample.objects.all()}
    assert set(kept) == {rejected.pk, at_outage.pk, after_outage.pk}
    for row in kept.values():
        assert (row.excluded, row.removal_pending, row.conflict) == (True, True, False)
        assert row.delivered_at is not None
    assert kept[rejected.pk].last_error == "disk full"
    assert kept[at_outage.pk].last_error == "timed out"
    assert kept[after_outage.pk].last_error == ""
    # The nightly removal finishes the job once the PC answers again.
    with patch.object(camera_ai, "delete_orientation_sample", return_value=True):
        assert dataset.export_removals(limit=10)["removed"] == 3
    assert not VehicleOrientationSample.objects.filter(delivered_at__isnull=False).exists()


def test_purge_samples_can_leave_camera_pc_alone():
    _row(1, **_on_pc())
    _row(2)

    with patch.object(camera_ai, "delete_orientation_sample") as delete:
        result = dataset.purge_samples(VehicleOrientationSample.objects.all(), remove_from_pc=False)

    assert result == {"deleted": 2, "removed_from_pc": 0, "pc_unavailable": False, "remaining": 0}
    delete.assert_not_called()
    assert VehicleOrientationSample.objects.count() == 0


def test_purge_samples_works_in_batches_and_reports_the_rest():
    now = timezone.now()
    rows = [_row(record_id, captured_at=now, **_on_pc(now)) for record_id in (1, 2, 3)]
    untouched = _row(9, captured_at=now + timedelta(hours=1))  # outside the filter
    queryset = VehicleOrientationSample.objects.filter(captured_at__lte=now)

    with patch.object(camera_ai, "delete_orientation_sample", return_value=True) as delete:
        first = dataset.purge_samples(queryset, limit=2)
        second = dataset.purge_samples(queryset, limit=2)
        third = dataset.purge_samples(queryset, limit=2)

    assert first == {"deleted": 2, "removed_from_pc": 2, "pc_unavailable": False, "remaining": 1}
    assert second == {"deleted": 1, "removed_from_pc": 1, "pc_unavailable": False, "remaining": 0}
    assert third == {"deleted": 0, "removed_from_pc": 0, "pc_unavailable": False, "remaining": 0}
    # Oldest ids first, so a caller looping on ``remaining`` never skips a row.
    assert [c.args[0] for c in delete.call_args_list] == [row.sample_id for row in rows]
    assert list(VehicleOrientationSample.objects.values_list("pk", flat=True)) == [untouched.pk]
    assert dataset.PURGE_BATCH == 100


def test_purge_all_clears_camera_pc_in_one_call_or_falls_back_to_frames():
    now = timezone.now()
    _row(1, **_on_pc(now))
    _row(2)
    with (
        patch.object(camera_ai, "clear_orientation_samples", return_value=1) as clear,
        patch.object(camera_ai, "delete_orientation_sample") as delete,
    ):
        assert dataset.purge_all() == {
            "deleted": 2, "removed_from_pc": 1, "pc_unavailable": False, "remaining": 0
        }
    clear.assert_called_once_with()
    delete.assert_not_called()
    assert VehicleOrientationSample.objects.count() == 0

    # An older Camera-PC without the bulk route: frames go one by one, in batches.
    sent = _row(3, **_on_pc(now))
    _row(4)
    with (
        patch.object(
            camera_ai, "clear_orientation_samples", side_effect=camera_ai.AiError(404, "no route", {})
        ),
        patch.object(camera_ai, "delete_orientation_sample", return_value=True) as delete,
        patch.object(dataset, "PURGE_BATCH", 1),
    ):
        assert dataset.purge_all() == {
            "deleted": 1, "removed_from_pc": 1, "pc_unavailable": False, "remaining": 1
        }
        assert dataset.purge_all() == {
            "deleted": 1, "removed_from_pc": 0, "pc_unavailable": False, "remaining": 0
        }
    delete.assert_called_once_with(sent.sample_id)
    assert VehicleOrientationSample.objects.count() == 0

    # The PC is down: delivered frames wait for the nightly removal, the rest go now.
    kept = _row(5, **_on_pc(now))
    _row(6)
    with (
        patch.object(
            camera_ai, "clear_orientation_samples", side_effect=camera_ai.AiUnavailable("down")
        ),
        patch.object(
            camera_ai, "delete_orientation_sample", side_effect=camera_ai.AiUnavailable("down")
        ),
    ):
        assert dataset.purge_all() == {
            "deleted": 1, "removed_from_pc": 0, "pc_unavailable": True, "remaining": 0
        }
    kept.refresh_from_db()
    assert (kept.excluded, kept.removal_pending) == (True, True)

    with (
        patch.object(camera_ai, "clear_orientation_samples") as clear,
        patch.object(camera_ai, "delete_orientation_sample") as delete,
    ):
        assert dataset.purge_all(remove_from_pc=False) == {
            "deleted": 1, "removed_from_pc": 0, "pc_unavailable": False, "remaining": 0
        }
    clear.assert_not_called()
    delete.assert_not_called()
    assert VehicleOrientationSample.objects.count() == 0


def test_purge_all_moves_the_watermark_so_the_nightly_collect_does_not_resurrect_rows():
    trip = _trip(status=st.COMPLETED, gross=3880, tare=8760)
    _record(trip, "gross", 3880)
    _record(trip, "tare", 8760)
    assert dataset.collect()["created"] == 2
    assert _watermark() is None

    before = timezone.now()
    with patch.object(camera_ai, "clear_orientation_samples", return_value=2):
        assert dataset.purge_all()["deleted"] == 2
    assert before <= _watermark() <= timezone.now()

    # Both frames are far younger than the 60-day window, yet they stay gone.
    with patch.object(camera_ai, "post_orientation_sample") as post:
        assert dataset.run()["created"] == 0
    post.assert_not_called()
    assert VehicleOrientationSample.objects.count() == 0

    # A frame weighed after the purge is collected as usual.
    later = _trip("676VEA13", status=st.COMPLETED, gross=3900, tare=8800)
    _record(later, "gross", 3900)
    _unassigned(3950)
    assert dataset.collect()["created"] == 2
    assert VehicleOrientationSample.objects.count() == 2

    # The watermark never moves back, even with a purge of an older period.
    mark = _watermark()
    dataset.purge_samples(VehicleOrientationSample.objects.none(), cutoff=mark - timedelta(days=1))
    assert _watermark() == mark


def test_older_than_purge_moves_the_watermark_to_its_cutoff():
    now = timezone.now()
    trip = _trip(status=st.COMPLETED, gross=3880, tare=8760)
    old = _record(trip, "gross", 3880)
    fresh = _record(trip, "tare", 8760)
    stale_item = _unassigned(3900)
    WeighingRecord.objects.filter(pk=old.pk).update(created_at=now - timedelta(days=40))
    UnassignedWeighing.objects.filter(pk=stale_item.pk).update(created_at=now - timedelta(days=35))
    assert dataset.collect()["created"] == 3
    # An unassigned sample is stamped with stable_weight_at, not created_at.
    VehicleOrientationSample.objects.filter(record_kind="unassigned").update(
        captured_at=now - timedelta(days=35)
    )
    cutoff = now - timedelta(days=30)

    with patch.object(camera_ai, "delete_orientation_sample") as delete:
        result = dataset.purge_samples(
            VehicleOrientationSample.objects.filter(captured_at__lt=cutoff), cutoff=cutoff
        )

    assert result["deleted"] == 2
    delete.assert_not_called()
    assert _watermark() == cutoff
    # The purged period is closed for good: only the fresh frame is still tracked.
    assert dataset.collect() == {"created": 0, "updated": 0, "unchanged": 1, "unlabelled": 0}
    assert [row.record_id for row in VehicleOrientationSample.objects.all()] == [fresh.pk]
    # A wider window on the next call cannot reopen it either.
    assert _watermark() == cutoff
    dataset.purge_samples(VehicleOrientationSample.objects.none(), cutoff=now - timedelta(days=45))
    assert _watermark() == cutoff


def test_relabelled_frame_is_still_removed_from_camera_pc_on_purge():
    trip = _trip(status=st.COMPLETED, gross=3880, tare=8760)
    _record(trip, "gross", 3880)
    dataset.collect()
    sample = VehicleOrientationSample.objects.get()
    with patch.object(camera_ai, "post_orientation_sample", return_value={"ok": True}):
        assert dataset.export_pending(limit=10)["sent"] == 1
    sample.refresh_from_db()
    delivered = sample.delivered_at
    assert delivered is not None

    dataset.set_manual_label(sample, "rear", None)
    sample.refresh_from_db()
    assert (sample.sent_at, sample.delivered_at) == (None, delivered)
    # The re-send fails: sent_at stays empty, yet the PC keeps the old copy.
    with patch.object(
        camera_ai, "post_orientation_sample", side_effect=camera_ai.AiError(500, "disk full", {})
    ):
        assert dataset.export_pending(limit=10)["failed"] == 1
    sample.refresh_from_db()
    assert (sample.sent_at, sample.delivered_at) == (None, delivered)

    # Excluding it queues the removal; purging it asks the PC before deleting.
    dataset.exclude_sample(sample, None)
    sample.refresh_from_db()
    assert sample.removal_pending is True
    with patch.object(camera_ai, "delete_orientation_sample", return_value=True) as delete:
        result = dataset.purge_samples(VehicleOrientationSample.objects.all())
    assert result["removed_from_pc"] == 1
    delete.assert_called_once_with(sample.sample_id)
    assert VehicleOrientationSample.objects.count() == 0


def test_purge_command_reports_totals_and_needs_a_scope():
    now = timezone.now()
    old = _row(1, captured_at=now - timedelta(days=10), **_on_pc(now))
    fresh = _row(2, captured_at=now - timedelta(days=1), **_on_pc(now))

    out = StringIO()
    with patch.object(camera_ai, "delete_orientation_sample", return_value=True) as delete:
        call_command("purge_orientation_samples", "--older-than-days", "5", stdout=out)
    assert json.loads(out.getvalue()) == {
        "deleted": 1, "removed_from_pc": 1, "pc_unavailable": False, "remaining": 0
    }
    delete.assert_called_once_with(old.sample_id)
    assert list(VehicleOrientationSample.objects.values_list("pk", flat=True)) == [fresh.pk]
    assert now - timedelta(days=5) <= _watermark() <= timezone.now() - timedelta(days=5)

    out = StringIO()
    with (
        patch.object(camera_ai, "clear_orientation_samples") as clear,
        patch.object(camera_ai, "delete_orientation_sample") as delete,
    ):
        call_command("purge_orientation_samples", "--all", "--keep-pc", stdout=out)
    assert json.loads(out.getvalue()) == {
        "deleted": 1, "removed_from_pc": 0, "pc_unavailable": False, "remaining": 0
    }
    clear.assert_not_called()
    delete.assert_not_called()
    assert _watermark() >= now

    _row(3, **_on_pc(now))
    out = StringIO()
    with patch.object(camera_ai, "clear_orientation_samples", return_value=1) as clear:
        call_command("purge_orientation_samples", "--all", stdout=out)
    assert json.loads(out.getvalue()) == {
        "deleted": 1, "removed_from_pc": 1, "pc_unavailable": False, "remaining": 0
    }
    clear.assert_called_once_with()

    for argv in ([], ["--all", "--older-than-days", "3"], ["--older-than-days", "0"]):
        with pytest.raises(CommandError):
            call_command("purge_orientation_samples", *argv)


def test_purge_command_loops_over_batches_and_stops_when_camera_pc_is_down():
    now = timezone.now()
    for record_id in range(1, 6):
        _row(record_id, captured_at=now - timedelta(days=10), **_on_pc(now))

    out = StringIO()
    with (
        patch.object(camera_ai, "delete_orientation_sample", return_value=True) as delete,
        patch.object(dataset, "PURGE_BATCH", 2),
    ):
        call_command("purge_orientation_samples", "--older-than-days", "5", stdout=out)
    assert json.loads(out.getvalue()) == {
        "deleted": 5, "removed_from_pc": 5, "pc_unavailable": False, "remaining": 0
    }
    assert delete.call_count == 5
    assert VehicleOrientationSample.objects.count() == 0

    # --all on an old PC firmware: bulk clear fails every time, batches still finish.
    for record_id in range(6, 9):
        _row(record_id, **_on_pc(now))
    out = StringIO()
    with (
        patch.object(
            camera_ai, "clear_orientation_samples", side_effect=camera_ai.AiError(404, "no route", {})
        ) as clear,
        patch.object(camera_ai, "delete_orientation_sample", return_value=True) as delete,
        patch.object(dataset, "PURGE_BATCH", 2),
    ):
        call_command("purge_orientation_samples", "--all", stdout=out)
    assert json.loads(out.getvalue()) == {
        "deleted": 3, "removed_from_pc": 3, "pc_unavailable": False, "remaining": 0
    }
    assert (clear.call_count, delete.call_count) == (2, 3)

    # The PC is down: one batch, then stop — the next batch would meet the same rows.
    kept = [
        _row(record_id, captured_at=now - timedelta(days=10), **_on_pc(now))
        for record_id in range(9, 13)
    ]
    out = StringIO()
    with (
        patch.object(
            camera_ai, "delete_orientation_sample", side_effect=camera_ai.AiUnavailable("down")
        ) as delete,
        patch.object(dataset, "PURGE_BATCH", 2),
    ):
        call_command("purge_orientation_samples", "--older-than-days", "1", stdout=out)
    assert json.loads(out.getvalue()) == {
        "deleted": 0, "removed_from_pc": 0, "pc_unavailable": True, "remaining": 2
    }
    assert delete.call_count == 1
    assert VehicleOrientationSample.objects.count() == 4
    assert (
        VehicleOrientationSample.objects.filter(
            pk__in=[row.pk for row in kept[:2]], removal_pending=True
        ).count()
        == 2
    )


def test_clear_client_wipes_the_dataset_in_one_request():
    with patch.object(camera_ai, "_request", return_value=(200, {"removed": 120})) as request:
        assert camera_ai.clear_orientation_samples() == 120
    assert request.call_args.args[:2] == ("DELETE", "/vehicle-orientation/samples")
    assert request.call_args.kwargs["timeout_seconds"] == camera_ai.ORIENTATION_SAMPLE_TIMEOUT
    with patch.object(camera_ai, "_request", return_value=(200, {})):
        assert camera_ai.clear_orientation_samples() == 0
    with patch.object(camera_ai, "_request", return_value=(200, {"removed": "many"})):
        assert camera_ai.clear_orientation_samples() == 0
    with patch.object(camera_ai, "_request", return_value=(404, {"error": "not found"})):
        with pytest.raises(camera_ai.AiError) as excinfo:
            camera_ai.clear_orientation_samples()
    assert excinfo.value.status == 404
