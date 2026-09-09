"""Production single-frame fallback and fully automatic measured-tare routing."""
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.core.files.base import ContentFile
from django.utils import timezone

from apps.grain import automatic_routing, historical_tare, statuses as st, weighing_identity as identity
from apps.grain.models import UnassignedWeighing, VehicleTareMemory, Wagon, WeighingIdentityCheck, WeighingRecord

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def config(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.OPENAI_API_KEY = "test-key"
    settings.WEIGHING_AI_ENABLED = True
    settings.WEIGHING_AI_MAX_DAILY_REQUESTS = 100


def event(*, number="", orientation="", weight=4000, minutes=30, photo=True):
    item = UnassignedWeighing.objects.create(
        vehicle_number=number, orientation=orientation, weight_kg=weight,
        stable_weight_at=timezone.now()-timedelta(minutes=minutes),
        camera="cam1", scale_number="truck", photo_request_id=uuid4(),
    )
    if photo:
        item.photo.save("frame.jpg", ContentFile(b"\xff\xd8\xff\xe0" + b"a"*32))
    return item


def answer(plate="123ABC13", orientation="front"):
    return {"exit": {"plate": plate, "plate_clear": True, "orientation": orientation}, "entries": []}


def process_gpt(plate="123ABC13", orientation="front"):
    with patch.object(identity, "request_verification", return_value=(answer(plate, orientation), "response-test")) as request:
        identity.process_once()
    assert request.call_count == 1
    assert request.call_args.args[1] == []  # no candidate numbers or multiple images
    return request


def test_gpt_entry_without_any_open_visit_creates_trip_and_memory():
    item = event()
    process_gpt()
    item.refresh_from_db()
    assert item.status == "assigned" and item.action == "entry"
    assert item.vehicle_number == "123ABC13" and item.orientation == "front"
    assert item.wagon.status == st.AT_SILO
    assert item.wagon.gross_weight_kg == 4000
    record = item.wagon.weighings.get(kind="gross")
    assert record.photo.name == item.photo.name and record.created_at == item.stable_weight_at
    assert VehicleTareMemory.objects.get(number="123ABC13").record_id == record.pk


def test_real_cycles_finish_without_operator_and_one_frame_check_per_exit():
    for number in ["123ABC13", "234BCD13", "345CDE13"]:
        entry = event(number=number, orientation="front")
        departure = event(number=number, orientation="rear", weight=8500, minutes=1)
        with patch.object(identity, "request_verification", return_value=(answer(number, "rear"), "response-test")) as request:
            identity.process_once()
            identity.process_once()
        assert request.call_count == 1
        departure.refresh_from_db()
        assert departure.wagon.status == st.COMPLETED
        assert departure.wagon.net_weight_kg == 4500
        assert departure.resolved_by_id is None
    assert UnassignedWeighing.objects.filter(status="open").count() == 0
    assert VehicleTareMemory.objects.count() == 3


def test_ocr_mismatch_falls_back_and_closes_exact_verified_plate():
    entry = event(number="423ABC13", orientation="front")
    identity.process_once()
    departure = event(number="123ABC13", orientation="rear", weight=8500, minutes=1)
    process_gpt("423ABC13", "rear")
    departure.refresh_from_db()
    assert departure.wagon_id == UnassignedWeighing.objects.get(pk=entry.pk).wagon_id
    assert departure.vehicle_number == "423ABC13"
    assert departure.identity_check.evidence["original_number"] == "123ABC13"
    assert departure.wagon.status == st.COMPLETED


def test_exit_without_open_visit_uses_latest_real_tare_and_keeps_history():
    entry = event(number="123ABC13", orientation="front", minutes=90)
    identity.process_once()
    old_exit = event(number="123ABC13", orientation="rear", weight=8000, minutes=60)
    process_gpt("123ABC13", "rear")
    old_exit.refresh_from_db()
    old_trip = old_exit.wagon
    source = old_trip.weighings.get(kind="gross")
    departure = event(number="123ABC13", orientation="rear", weight=8600, minutes=1)
    process_gpt("123ABC13", "rear")
    departure.refresh_from_db()
    assert departure.wagon_id != old_trip.pk
    assert departure.wagon.status == st.COMPLETED and departure.wagon.net_weight_kg == 4600
    saved = departure.wagon.weighings.get(kind="gross")
    assert saved.source == "historical" and saved.reference_record_id == source.pk
    assert departure.wagon.silo_arrived_at is None
    old_trip.refresh_from_db()
    assert old_trip.net_weight_kg == 4000 and old_trip.weighings.count() == 2
    assert VehicleTareMemory.objects.get(number="123ABC13").record_id == source.pk
    # Replay of exactly this event never books a second visit/weight.
    assert automatic_routing.book(departure, "123ABC13", "rear").wagon_id == departure.wagon_id
    identity.process_once()
    assert Wagon.objects.count() == 2 and WeighingRecord.objects.count() == 4


def test_new_front_weighing_refreshes_single_tare_reference():
    first = event(number="123ABC13", orientation="front", weight=4000, minutes=40)
    second = event(number="123ABC13", orientation="front", weight=4200, minutes=30)
    identity.process_once()
    identity.process_once()
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.wagon_id == second.wagon_id
    assert second.wagon.gross_weight_kg == 4200
    memory = VehicleTareMemory.objects.get(number="123ABC13")
    assert memory.record.weight_kg == 4200 and VehicleTareMemory.objects.count() == 1
    departure = event(number="123ABC13", orientation="rear", weight=8500, minutes=1)
    process_gpt("123ABC13", "rear")
    departure.refresh_from_db()
    assert departure.wagon.net_weight_kg == 4300


def test_latest_heavier_tare_never_replaced_by_older_lighter_one():
    first = event(number="123ABC13", orientation="front", weight=4000, minutes=40)
    second = event(number="123ABC13", orientation="front", weight=9000, minutes=30)
    identity.process_once()
    identity.process_once()
    first.refresh_from_db()
    Wagon.objects.filter(pk=first.wagon_id).update(status=st.COMPLETED)
    departure = event(number="123ABC13", orientation="rear", weight=8500, minutes=1)
    process_gpt("123ABC13", "rear")
    departure.refresh_from_db()
    assert departure.status == "open"
    assert departure.identity_check.reason == "exit_weight_not_greater"
    assert Wagon.objects.count() == 1


def test_no_saved_tare_still_calls_gpt_then_remains_explicit_exception():
    item = event(orientation="rear", weight=8500)
    process_gpt("123ABC13", "rear")
    item.refresh_from_db()
    assert item.status == "open" and Wagon.objects.count() == 0
    assert item.identity_check.reason == "saved_tare_missing"


def test_legacy_reviewed_front_is_requeued_without_candidates():
    item = event(orientation="front")
    WeighingIdentityCheck.objects.create(weighing=item, status="review", reason="entry_evidence_pending", attempts=3)
    process_gpt()
    item.refresh_from_db()
    assert item.status == "assigned"
    assert item.identity_check.attempts == 4


def test_known_identity_can_book_when_frame_delivery_failed():
    item = event(number="123ABC13", orientation="front", photo=False)
    with patch.object(identity, "request_verification") as request:
        identity.process_once()
    request.assert_not_called()
    item.refresh_from_db()
    assert item.status == "assigned" and item.wagon.gross_weight_kg == 4000


def test_operator_change_during_gpt_request_wins():
    item = event()
    def respond(*args):
        UnassignedWeighing.objects.filter(pk=item.pk).update(status="discarded")
        return answer(), "response-test"
    with patch.object(identity, "request_verification", side_effect=respond):
        identity.process_once()
    assert Wagon.objects.count() == 0
    item.refresh_from_db()
    assert item.status == "discarded" and item.identity_check.reason == "weighing_changed"


def test_old_replayed_entry_cannot_overwrite_current_tare():
    current = event(number="123ABC13", orientation="front", weight=4200, minutes=10)
    identity.process_once()
    older = event(number="123ABC13", orientation="front", weight=3900, minutes=30)
    process_gpt("123ABC13", "front")
    older.refresh_from_db()
    assert older.status == "open" and older.identity_check.reason == "passage_time_conflict"
    assert VehicleTareMemory.objects.get(number="123ABC13").record.weight_kg == 4200


def test_delayed_entry_prevents_premature_historical_tare_reuse():
    # A previous completed visit provides a tempting but out-of-date tare.
    old_entry = event(number="123ABC13", orientation="front", weight=3900, minutes=180)
    identity.process_once()
    old_exit = event(number="123ABC13", orientation="rear", weight=8000, minutes=150)
    process_gpt("123ABC13", "rear")
    entry = event(orientation="front", weight=4200, minutes=30, photo=False)
    departure = event(number="123ABC13", orientation="rear", weight=8500, minutes=1)
    with patch("apps.grain.weighing_photos.photo_delivery_status", return_value="retrying"):
        process_gpt("123ABC13", "rear")
    departure.refresh_from_db()
    assert departure.status == "open" and departure.identity_check.reason == "earlier_entry_pending"
    entry.photo.save("recovered.jpg", ContentFile(b"\xff\xd8\xff\xe0" + b"a"*32))
    process_gpt("123ABC13", "front")
    WeighingIdentityCheck.objects.filter(weighing=departure).update(next_attempt_at=timezone.now()-timedelta(seconds=1))
    with patch.object(identity, "request_verification") as request:
        identity.process_once()
    request.assert_not_called()
    departure.refresh_from_db()
    assert departure.wagon.gross_weight_kg == 4200 and departure.wagon.net_weight_kg == 4300
    assert not departure.wagon.weighings.filter(source="historical").exists()


def test_verified_exit_rechecks_late_tare_without_second_gpt_request():
    departure = event(weight=8500, minutes=1)
    process_gpt("123ABC13", "rear")
    departure.refresh_from_db()
    assert departure.vehicle_number == "123ABC13" and departure.orientation == "rear"
    assert departure.identity_check.reason == "saved_tare_missing"
    entry = event(number="123ABC13", orientation="front", weight=4200, minutes=30)
    identity.process_once()
    WeighingIdentityCheck.objects.filter(weighing=departure).update(next_attempt_at=timezone.now()-timedelta(seconds=1))
    with patch.object(identity, "request_verification") as request:
        identity.process_once()
    request.assert_not_called()
    departure.refresh_from_db()
    assert departure.wagon.net_weight_kg == 4300
    assert departure.identity_check.attempts == 1


def test_late_correction_keeps_physical_entry_time_and_newer_tare():
    from apps.grain import services
    now = timezone.now()
    # A wrong old front booking actually represented a loaded exit.
    old = Wagon.objects.create(number="123ABC13", direction=Wagon.PASSAGE, workflow="simple", cargo_name="Test", status=st.AT_SILO, gross_weight_kg=8000, arrived_at=now-timedelta(hours=3), silo_arrived_at=now-timedelta(hours=3))
    WeighingRecord.objects.create(wagon=old, kind="gross", source="scale", orientation="rear", weight_kg=8000)
    correct_entry = event(number="123ABC13", orientation="front", weight=3900, minutes=240)
    # More recent genuine measurement from another completed visit.
    newer = Wagon.objects.create(number="123ABC13", direction=Wagon.PASSAGE, workflow="simple", cargo_name="Test", status=st.COMPLETED)
    measured = WeighingRecord.objects.create(wagon=newer, kind="gross", source="scale", orientation="front", weight_kg=4300)
    WeighingRecord.objects.filter(pk=measured.pk).update(created_at=now-timedelta(hours=1))
    measured.refresh_from_db()
    historical_tare.remember(measured, newer.number)
    services.assign_unassigned_weighing(correct_entry, old, None)
    corrected = old.weighings.get(kind="gross")
    assert corrected.created_at == correct_entry.stable_weight_at
    assert VehicleTareMemory.objects.get(number="123ABC13").record_id == measured.pk


def test_rear_classifier_error_is_rechecked_even_with_known_tare():
    old = event(number="123ABC13", orientation="front", weight=3900, minutes=180)
    identity.process_once()
    event(number="123ABC13", orientation="rear", weight=8000, minutes=150)
    process_gpt("123ABC13", "rear")
    # Primary detector labels this genuine new front entry as rear.
    entry = event(number="123ABC13", orientation="rear", weight=4300, minutes=1)
    process_gpt("123ABC13", "front")
    entry.refresh_from_db()
    assert entry.action == "entry" and entry.wagon.status == st.AT_SILO
    assert entry.wagon.gross_weight_kg == 4300
    assert entry.wagon.tare_weight_kg is None


def test_one_character_collision_rechecks_front_ocr_with_both_plates_known():
    for number in ("123ABC13", "423ABC13"):
        trip = Wagon.objects.create(number=number, direction=Wagon.PASSAGE, workflow="simple", cargo_name="Test", status=st.COMPLETED)
        record = WeighingRecord.objects.create(wagon=trip, kind="gross", source="scale", orientation="front", weight_kg=3900)
        historical_tare.remember(record, number)
    entry = event(number="123ABC13", orientation="front", weight=4300, minutes=1)
    process_gpt("423ABC13", "front")
    entry.refresh_from_db()
    assert entry.wagon.number == "423ABC13"
    assert not Wagon.objects.filter(number="123ABC13", status=st.AT_SILO).exists()
