"""Production single-frame fallback and fully automatic measured-tare routing."""
import logging
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.core.files.base import ContentFile
from django.utils import timezone

from apps.eventlog.models import EventLog
from apps.grain import automatic_routing, historical_tare, statuses as st, weighing_identity as identity
from apps.grain.models import (
    AutomaticPassageCapture, UnassignedWeighing, VehicleTareMemory, Wagon, WeighingIdentityCheck, WeighingRecord,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def config(settings, tmp_path, monkeypatch):
    settings.MEDIA_ROOT = tmp_path
    settings.OPENAI_API_KEY = "test-key"
    settings.WEIGHING_AI_ENABLED = True
    settings.WEIGHING_AI_MAX_DAILY_REQUESTS = 100
    # As on a monitor start: the first pass reconciles stale visits, later passes once per interval.
    monkeypatch.setattr(identity, "_next_reconcile_at", 0.0, raising=False)


def event(*, number="", orientation="", weight=4000, minutes=30, photo=True, capture=None):
    item = UnassignedWeighing.objects.create(
        vehicle_number=number, orientation=orientation, weight_kg=weight,
        stable_weight_at=timezone.now()-timedelta(minutes=minutes),
        camera="cam1", scale_number="truck", photo_request_id=uuid4(), capture=capture,
    )
    if photo:
        item.photo.save("frame.jpg", ContentFile(b"\xff\xd8\xff\xe0" + b"a"*32))
    return item


def weak_event(*, number, orientation, weight, minutes):
    """A weighing whose plate the camera voted for short of confirmation (two votes of three)."""
    capture = AutomaticPassageCapture.objects.create(
        idempotency_key=uuid4(), camera="cam1", orientation=orientation, weight_kg=weight,
        stable_weight_at=timezone.now()-timedelta(minutes=minutes), vehicle_number=number,
        confirmation_votes=2, ai_payload_json={"weak_plate": True, "votes": {number: 2}},
    )
    return event(number=number, orientation=orientation, weight=weight, minutes=minutes, capture=capture)


def _pending_unread(weight, minutes, orientation="rear"):
    """A weighing without a plate whose frame has not arrived yet: its check has no verdict."""
    item = event(number="", orientation=orientation, weight=weight, minutes=minutes, photo=False)
    WeighingIdentityCheck.objects.create(
        weighing=item, status="retrying", reason="photo_pending", next_attempt_at=timezone.now()+timedelta(seconds=15),
    )
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


def test_real_cycles_finish_without_operator_or_a_frame_check_when_ocr_matches_the_open_visit():
    for number in ["123ABC13", "234BCD13", "345CDE13"]:
        entry = event(number=number, orientation="front")
        departure = event(number=number, orientation="rear", weight=8500, minutes=1)
        with patch.object(identity, "request_verification", return_value=(answer(number, "rear"), "response-test")) as request:
            identity.process_once()
            identity.process_once()
        assert request.call_count == 0  # the model is a fallback: OCR named the truck that is on site
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
    identity.process_once()
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
    identity.process_once()
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
    identity.process_once()
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
    identity.process_once()
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


def _open_visit(number, weight, minutes):
    item = event(number=number, orientation="front", weight=weight, minutes=minutes)
    # A similar plate already on site makes the entry re-read its frame; answer with the OCR plate.
    with patch.object(identity, "request_verification", return_value=(answer(number, "front"), "response-test")):
        identity.process_once()
    item.refresh_from_db()
    assert item.action == "entry"
    return item.wagon


def test_reentry_after_misread_exit_closes_first_visit_and_opens_second():
    first = _open_visit("065CUA13", 5340, 130)
    misread = event(number="", orientation="rear", weight=10860, minutes=80)
    process_gpt("165CUA17", "rear")
    misread.refresh_from_db()
    assert misread.status == "open" and misread.identity_check.reason == "similar_visit_open"
    again = event(number="065CUA13", orientation="front", weight=5320, minutes=20)
    identity.process_once()
    again.refresh_from_db()
    first.refresh_from_db()
    misread.refresh_from_db()
    assert first.status == st.COMPLETED and first.gross_weight_kg == 5340
    assert first.tare_weight_kg == 10860 and first.net_weight_kg == 5520
    assert misread.status == "assigned" and misread.action == "exit" and misread.wagon_id == first.pk
    assert misread.identity_check.status == "matched"
    second = again.wagon
    assert second.pk != first.pk and second.status == st.AT_SILO and second.gross_weight_kg == 5320
    departure = event(number="065CUA13", orientation="rear", weight=10380, minutes=1)
    identity.process_once()
    departure.refresh_from_db()
    assert departure.wagon_id == second.pk and departure.wagon.net_weight_kg == 5060


def test_reentry_long_after_entry_takes_the_single_unread_loaded_exit_as_the_missed_one():
    # The rear camera found no plate at all on the way out (the common case);
    # the one loaded weighing between entry and re-entry is that truck's exit.
    first = _open_visit("065CUA13", 5340, 130)
    unread = event(number="", orientation="rear", weight=10860, minutes=80)
    process_gpt("", "rear")
    unread.refresh_from_db()
    assert unread.identity_check.reason == "plate_unreadable"
    again = event(number="065CUA13", orientation="front", weight=5320, minutes=20)
    with patch.object(identity, "request_verification") as request:
        identity.process_once()
    request.assert_not_called()
    again.refresh_from_db()
    first.refresh_from_db()
    unread.refresh_from_db()
    assert first.status == st.COMPLETED and first.gross_weight_kg == 5340
    assert first.tare_weight_kg == 10860 and first.net_weight_kg == 5520
    assert unread.status == "assigned" and unread.action == "exit" and unread.wagon_id == first.pk
    assert unread.identity_check.status == "matched" and unread.identity_check.reason == "automatic_exit"
    recovered = EventLog.objects.get(event_type="grain_automatic_binding", payload__unassigned_id=unread.pk)
    assert "выезд восстановлен" in recovered.message and "не прочитан" in recovered.message
    second = again.wagon
    assert second is not None and second.pk != first.pk and second.status == st.AT_SILO and second.gross_weight_kg == 5320
    departure = event(number="065CUA13", orientation="rear", weight=10380, minutes=1)
    identity.process_once()
    departure.refresh_from_db()
    assert departure.wagon_id == second.pk and departure.wagon.net_weight_kg == 5060
    assert UnassignedWeighing.objects.filter(status="open").count() == 0


def test_reentry_with_two_unread_loaded_exits_waits_for_the_operator():
    first = _open_visit("065CUA13", 5340, 130)
    for minutes in (90, 60):
        event(number="", orientation="rear", weight=10800, minutes=minutes)
        process_gpt("", "rear")
    again = event(number="065CUA13", orientation="front", weight=5320, minutes=20)
    process_gpt("065CUA13", "front")
    again.refresh_from_db()
    first.refresh_from_db()
    assert again.status == "open" and again.identity_check.reason == "previous_exit_missing"
    assert first.status == st.AT_SILO and first.tare_weight_kg is None and first.gross_weight_kg == 5340
    assert UnassignedWeighing.objects.filter(orientation="rear", status="open").count() == 2
    assert Wagon.objects.filter(number="065CUA13").count() == 1


def test_unread_exit_lighter_than_entry_plus_load_is_not_the_missed_exit():
    first = _open_visit("065CUA13", 5340, 130)
    light = event(number="", orientation="rear", weight=6000, minutes=80)
    process_gpt("", "rear")
    again = event(number="065CUA13", orientation="front", weight=5320, minutes=20)
    process_gpt("065CUA13", "front")
    again.refresh_from_db()
    first.refresh_from_db()
    light.refresh_from_db()
    assert again.status == "open" and again.identity_check.reason == "previous_exit_missing"
    assert first.status == st.AT_SILO and first.tare_weight_kg is None
    assert light.status == "open"


def test_unread_exit_that_the_model_read_as_another_truck_is_not_the_missed_exit():
    first = _open_visit("065CUA13", 5340, 130)
    other = event(number="", orientation="rear", weight=10800, minutes=80)
    process_gpt("777XYZ01", "rear")
    other.refresh_from_db()
    assert other.status == "open" and other.vehicle_number == "777XYZ01"
    again = event(number="065CUA13", orientation="front", weight=5320, minutes=20)
    process_gpt("065CUA13", "front")
    again.refresh_from_db()
    first.refresh_from_db()
    other.refresh_from_db()
    assert again.status == "open" and again.identity_check.reason == "previous_exit_missing"
    assert first.status == st.AT_SILO and first.tare_weight_kg is None
    assert other.status == "open"


def test_reentry_after_the_longest_trip_closes_the_stale_visit_without_an_exit():
    first = _open_visit("065CUA13", 5340, 13 * 60)
    again = event(number="065CUA13", orientation="front", weight=5320, minutes=20)
    with patch.object(identity, "request_verification") as request:
        identity.process_once()
    request.assert_not_called()
    again.refresh_from_db()
    first.refresh_from_db()
    assert first.status == st.CANCELLED and first.tare_weight_kg is None
    assert first.exit_note == "Выезд не зафиксирован: рейс закрыт автоматически при новом заезде"
    closed = EventLog.objects.get(event_type="grain_status", payload__new_status=st.CANCELLED, payload__wagon_id=first.pk)
    assert closed.payload["auto"] is True and closed.payload["unassigned_id"] == again.pk
    assert "рейс закрыт без выезда" in closed.message
    second = again.wagon
    assert second is not None and second.pk != first.pk and second.status == st.AT_SILO and second.gross_weight_kg == 5320
    assert Wagon.objects.filter(number="065CUA13", status__in=st.ON_SITE_STATUSES).count() == 1


def test_unread_exit_later_than_the_longest_trip_is_not_taken_for_the_stale_visit():
    first = _open_visit("065CUA13", 5340, 14 * 60)
    late = event(number="", orientation="rear", weight=10860, minutes=90)  # 12.5 h after entry
    process_gpt("", "rear")
    again = event(number="065CUA13", orientation="front", weight=5320, minutes=20)
    with patch.object(identity, "request_verification") as request:
        identity.process_once()
    request.assert_not_called()
    again.refresh_from_db()
    first.refresh_from_db()
    late.refresh_from_db()
    assert first.status == st.CANCELLED and first.tare_weight_kg is None
    assert late.status == "open"
    assert again.wagon is not None and again.wagon.pk != first.pk and again.wagon.gross_weight_kg == 5320


def test_reentry_waits_while_the_unread_exit_has_no_verdict_yet():
    # The frame of the loaded exit has not arrived, so nobody could read its
    # plate yet. Until that check ends the re-entry neither takes the exit as
    # the missed one nor gives up on the visit: it waits in the review queue.
    first = _open_visit("065CUA13", 5340, 130)
    unread = _pending_unread(10860, 80)
    again = event(number="065CUA13", orientation="front", weight=5320, minutes=20)
    process_gpt("065CUA13", "front")
    again.refresh_from_db()
    first.refresh_from_db()
    assert again.status == "open" and again.identity_check.reason == "previous_exit_missing"
    assert first.status == st.AT_SILO and first.tare_weight_kg is None
    assert Wagon.objects.filter(number="065CUA13").count() == 1
    # The frame arrives and the model finds no plate: the exit is settled as unread.
    unread.photo.save("late.jpg", ContentFile(b"\xff\xd8\xff\xe0" + b"a"*32))
    WeighingIdentityCheck.objects.filter(weighing=unread).update(next_attempt_at=timezone.now()-timedelta(seconds=1))
    process_gpt("", "rear")
    unread.refresh_from_db()
    assert unread.identity_check.status == "review" and unread.identity_check.reason == "plate_unreadable"
    WeighingIdentityCheck.objects.filter(weighing=again).update(next_attempt_at=timezone.now()-timedelta(seconds=1))
    with patch.object(identity, "request_verification") as request:
        identity.process_once()
    request.assert_not_called()
    again.refresh_from_db()
    first.refresh_from_db()
    unread.refresh_from_db()
    assert first.status == st.COMPLETED and first.tare_weight_kg == 10860 and first.net_weight_kg == 5520
    assert unread.status == "assigned" and unread.action == "exit" and unread.wagon_id == first.pk
    assert again.wagon is not None and again.wagon.pk != first.pk and again.wagon.gross_weight_kg == 5320


def test_short_reentry_with_an_unread_exit_still_being_checked_refreshes_the_same_visit():
    first = _open_visit("065CUA13", 5340, 25)
    pending = _pending_unread(9000, 15)
    again = event(number="065CUA13", orientation="front", weight=5320, minutes=10)
    identity.process_once()
    again.refresh_from_db()
    first.refresh_from_db()
    pending.refresh_from_db()
    assert again.wagon_id == first.pk and first.gross_weight_kg == 5320 and first.status == st.AT_SILO
    assert pending.status == "open"
    assert Wagon.objects.filter(number="065CUA13").count() == 1


def test_stale_visit_is_not_abandoned_while_an_unread_exit_in_its_window_has_no_verdict():
    first = _open_visit("065CUA13", 5340, 13 * 60)
    pending = _pending_unread(10860, 90)  # 11.5 h after entry, inside the visit's window
    again = event(number="065CUA13", orientation="front", weight=5320, minutes=20)
    process_gpt("065CUA13", "front")
    again.refresh_from_db()
    first.refresh_from_db()
    pending.refresh_from_db()
    assert again.status == "open" and again.identity_check.reason == "previous_exit_missing"
    assert first.status == st.AT_SILO and first.tare_weight_kg is None and first.exit_note == ""
    assert pending.status == "open"
    assert Wagon.objects.filter(number="065CUA13").count() == 1


def test_two_unread_exits_in_the_window_of_a_stale_visit_wait_for_the_operator_instead_of_ending_it():
    # Two candidates name nobody; that is ambiguity, not a visit nobody closed.
    first = _open_visit("065CUA13", 5340, 13 * 60)
    for minutes in (180, 120):
        event(number="", orientation="rear", weight=10800, minutes=minutes)
        process_gpt("", "rear")
    again = event(number="065CUA13", orientation="front", weight=5320, minutes=20)
    process_gpt("065CUA13", "front")
    again.refresh_from_db()
    first.refresh_from_db()
    assert again.status == "open" and again.identity_check.reason == "previous_exit_missing"
    assert first.status == st.AT_SILO and first.tare_weight_kg is None and first.exit_note == ""
    assert UnassignedWeighing.objects.filter(orientation="rear", status="open").count() == 2
    assert Wagon.objects.filter(number="065CUA13").count() == 1


def test_plate_core_drops_the_region_of_a_kz_plate_and_the_leading_letter_of_the_old_form():
    assert automatic_routing._plate_core("123ABC13") == "123ABC"
    assert automatic_routing._plate_core("934PB13") == "934PB"
    assert automatic_routing._plate_core("E065CUA") == "065CUA"
    assert automatic_routing._plate_core("X472OZN") == "472OZN"
    assert automatic_routing._plate_core("T765CUA13") == "T765CUA13"  # no known layout: nothing to drop


def _misread_exit(plate, weight, minutes):
    """A loaded exit whose only reading is the model's raw text in no valid layout."""
    item = event(number="", orientation="rear", weight=weight, minutes=minutes)
    process_gpt(plate, "rear")
    item.refresh_from_db()
    assert item.status == "open" and item.vehicle_number == "" and item.identity_check.reason == "plate_unreadable"
    return item


def test_reentry_under_the_right_plate_closes_the_visit_opened_under_a_misread_one():
    # The front camera kept the plate's core but dropped the region and put a
    # letter in front (E065CUA for 065CUA13); the exit's raw model text is
    # this plate two edits off. The truck's re-entry settles that visit.
    phantom = _open_visit("E065CUA", 5380, 130)
    departure = _misread_exit("T765CUA13", 10640, 80)
    again = event(number="065CUA13", orientation="front", weight=5340, minutes=20)
    with patch.object(identity, "request_verification") as request:
        identity.process_once()
    request.assert_not_called()
    again.refresh_from_db()
    phantom.refresh_from_db()
    departure.refresh_from_db()
    assert phantom.status == st.COMPLETED and phantom.tare_weight_kg == 10640 and phantom.net_weight_kg == 5260
    assert departure.status == "assigned" and departure.wagon_id == phantom.pk and departure.identity_check.status == "matched"
    recovered = EventLog.objects.get(event_type="grain_automatic_binding", payload__unassigned_id=departure.pk)
    assert "E065CUA" in recovered.message and "065CUA13" in recovered.message
    assert again.wagon is not None and again.wagon.number == "065CUA13" and again.wagon.gross_weight_kg == 5340
    assert Wagon.objects.filter(status__in=st.ON_SITE_STATUSES).count() == 1


def test_reentry_under_the_right_plate_cancels_a_misread_visit_older_than_the_longest_trip():
    phantom = _open_visit("E065CUA", 5380, 13 * 60)
    again = event(number="065CUA13", orientation="front", weight=5340, minutes=20)
    with patch.object(identity, "request_verification") as request:
        identity.process_once()
    request.assert_not_called()
    again.refresh_from_db()
    phantom.refresh_from_db()
    assert phantom.status == st.CANCELLED and phantom.exit_note == "Выезд не зафиксирован: рейс закрыт автоматически при новом заезде"
    closed = EventLog.objects.get(event_type="grain_status", payload__new_status=st.CANCELLED, payload__wagon_id=phantom.pk)
    assert "E065CUA" in closed.message and "065CUA13" in closed.message
    assert again.wagon is not None and again.wagon.number == "065CUA13" and again.wagon.status == st.AT_SILO
    assert Wagon.objects.filter(status__in=st.ON_SITE_STATUSES).count() == 1


def test_reentry_under_the_right_plate_still_opens_when_the_misread_visit_cannot_be_settled():
    phantom = _open_visit("E065CUA", 5380, 130)
    again = event(number="065CUA13", orientation="front", weight=5340, minutes=20)
    with patch.object(identity, "request_verification") as request:
        identity.process_once()
    request.assert_not_called()
    again.refresh_from_db()
    phantom.refresh_from_db()
    assert phantom.status == st.AT_SILO and phantom.tare_weight_kg is None
    assert again.status == "assigned" and again.wagon.number == "065CUA13" and again.wagon.gross_weight_kg == 5340


def test_reentry_does_not_close_the_misread_visit_with_an_exit_nobody_read():
    # A visit under another spelling is settled only by an exit that reads as
    # this plate; an unread exit may belong to any truck on site.
    phantom = _open_visit("E065CUA", 5380, 130)
    unread = event(number="", orientation="rear", weight=10640, minutes=80)
    process_gpt("", "rear")
    again = event(number="065CUA13", orientation="front", weight=5340, minutes=20)
    with patch.object(identity, "request_verification") as request:
        identity.process_once()
    request.assert_not_called()
    again.refresh_from_db()
    phantom.refresh_from_db()
    unread.refresh_from_db()
    assert phantom.status == st.AT_SILO and phantom.tare_weight_kg is None
    assert unread.status == "open"
    assert again.status == "assigned" and again.wagon.number == "065CUA13" and again.wagon.gross_weight_kg == 5340


def test_reentry_weighing_unlike_the_misread_visits_entry_leaves_that_visit_alone():
    # Same core plate, but 620 kg apart empty: not the same truck. Neither the
    # readable exit nor the visit's age settles it.
    phantom = _open_visit("E065CUA", 5380, 13 * 60)
    departure = _misread_exit("T765CUA13", 10640, 80)
    again = event(number="065CUA13", orientation="front", weight=6000, minutes=20)
    with patch.object(identity, "request_verification") as request:
        identity.process_once()
    request.assert_not_called()
    again.refresh_from_db()
    phantom.refresh_from_db()
    departure.refresh_from_db()
    assert phantom.status == st.AT_SILO and phantom.tare_weight_kg is None and phantom.exit_note == ""
    assert departure.status == "open"
    assert again.status == "assigned" and again.wagon.number == "065CUA13" and again.wagon.gross_weight_kg == 6000


def test_reentry_of_a_neighbouring_plate_does_not_close_the_neighbours_visit_with_an_unread_exit():
    # 261BBF13 and 411BBF13 are two trucks that are on site together. Two
    # edits and an exit nobody read are no evidence that the neighbour left.
    neighbour = _open_visit("411BBF13", 3960, 130)
    unread = event(number="", orientation="rear", weight=8860, minutes=80)
    process_gpt("", "rear")
    entry = event(number="261BBF13", orientation="front", weight=3940, minutes=20)
    with patch.object(identity, "request_verification") as request:
        identity.process_once()
    request.assert_not_called()
    entry.refresh_from_db()
    neighbour.refresh_from_db()
    unread.refresh_from_db()
    assert neighbour.status == st.AT_SILO and neighbour.tare_weight_kg is None
    assert unread.status == "open"
    assert entry.status == "assigned" and entry.wagon.number == "261BBF13" and entry.wagon.gross_weight_kg == 3940
    assert Wagon.objects.filter(status__in=st.ON_SITE_STATUSES).count() == 2


def test_exit_read_one_edit_off_waits_for_the_parked_entry_of_the_same_truck():
    first = _open_visit("065CUA13", 5340, 130)
    for minutes in (90, 60):
        event(number="", orientation="rear", weight=10800, minutes=minutes)
        process_gpt("", "rear")
    again = event(number="065CUA13", orientation="front", weight=5320, minutes=20)
    process_gpt("065CUA13", "front")
    again.refresh_from_db()
    assert again.status == "open" and again.identity_check.reason == "previous_exit_missing"
    # The rear plate came back one character off; without the parked entry it
    # would close the old visit instead of waiting for its own.
    departure = event(number="165CUA13", orientation="rear", weight=10380, minutes=1)
    process_gpt("165CUA13", "rear")
    departure.refresh_from_db()
    first.refresh_from_db()
    assert departure.status == "open" and departure.identity_check.reason == "earlier_entry_pending"
    assert first.status == st.AT_SILO and first.tare_weight_kg is None


def test_short_reentry_with_unrelated_unread_exit_still_refreshes_the_same_visit():
    first = _open_visit("065CUA13", 5340, 25)
    other = event(number="", orientation="rear", weight=9000, minutes=15)
    process_gpt("", "rear")
    again = event(number="065CUA13", orientation="front", weight=5320, minutes=10)
    identity.process_once()
    again.refresh_from_db()
    first.refresh_from_db()
    other.refresh_from_db()
    assert again.wagon_id == first.pk and first.gross_weight_kg == 5320 and first.status == st.AT_SILO
    assert other.status == "open"
    assert Wagon.objects.filter(number="065CUA13").count() == 1


def test_reentry_with_two_near_plate_exits_does_not_guess():
    first = _open_visit("065CUA13", 5340, 130)
    # Two edits away each: neither binds on its own, and together they name nobody.
    for plate, minutes in (("165CUA17", 90), ("265CUA18", 60)):
        event(number="", orientation="rear", weight=10800, minutes=minutes)
        process_gpt(plate, "rear")
    again = event(number="065CUA13", orientation="front", weight=5320, minutes=20)
    process_gpt("065CUA13", "front")
    again.refresh_from_db()
    first.refresh_from_db()
    assert again.status == "open" and again.identity_check.reason == "previous_exit_missing"
    assert first.status == st.AT_SILO and first.tare_weight_kg is None and first.gross_weight_kg == 5340
    assert UnassignedWeighing.objects.filter(orientation="rear", status="open").count() == 2
    assert Wagon.objects.filter(number="065CUA13").count() == 1


def _remember_history(number, weight=3940, minutes=None):
    trip = Wagon.objects.create(number=number, direction=Wagon.PASSAGE, workflow="simple", cargo_name="Test", status=st.COMPLETED)
    record = WeighingRecord.objects.create(wagon=trip, kind="gross", source="scale", orientation="front", weight_kg=weight)
    if minutes is not None:
        # A tare measured before today's events, so historical reuse can pick it.
        WeighingRecord.objects.filter(pk=record.pk).update(created_at=timezone.now()-timedelta(minutes=minutes))
        record.refresh_from_db()
    historical_tare.remember(record, number)
    return trip


def test_exit_with_ocr_plate_of_a_truck_on_site_books_without_gpt():
    _open_visit("123ABC13", 4000, 60)
    departure = event(number="123ABC13", orientation="rear", weight=8500, minutes=1)
    with patch.object(identity, "request_verification") as request:
        identity.process_once()
    request.assert_not_called()
    departure.refresh_from_db()
    assert departure.status == "assigned" and departure.action == "exit"
    assert departure.wagon.status == st.COMPLETED and departure.wagon.net_weight_kg == 4500
    assert departure.identity_check.status == "matched" and departure.identity_check.evidence["identity_source"] == "ocr"


def test_exit_ocr_plate_still_asks_gpt_when_a_similar_truck_is_on_site():
    _open_visit("123ABC13", 4000, 60)
    _open_visit("423ABC13", 4100, 50)
    departure = event(number="123ABC13", orientation="rear", weight=8500, minutes=1)
    process_gpt("423ABC13", "rear")
    departure.refresh_from_db()
    assert departure.wagon.number == "423ABC13" and departure.wagon.status == st.COMPLETED
    assert Wagon.objects.get(number="123ABC13").status == st.AT_SILO


def test_exit_barely_heavier_than_entry_asks_gpt_but_keeps_the_plate_of_the_truck_on_site():
    first = _open_visit("724LCA13", 3740, 60)
    departure = event(number="724LCA13", orientation="rear", weight=4500, minutes=1)
    process_gpt("724LCA43", "rear")
    departure.refresh_from_db()
    first.refresh_from_db()
    assert departure.wagon_id == first.pk and first.status == st.COMPLETED and first.net_weight_kg == 760
    assert departure.vehicle_number == "724LCA13"
    assert departure.identity_check.evidence["model_number"] == "724LCA43"
    assert not Wagon.objects.filter(number="724LCA43").exists()


def test_misread_exit_binds_to_the_single_on_site_truck_one_edit_away_instead_of_a_phantom_trip():
    _remember_history("934PPB13")
    visit = _open_visit("934PB13", 3920, 90)
    departure = event(number="", orientation="rear", weight=8360, minutes=1)
    process_gpt("934PPB13", "rear")
    departure.refresh_from_db()
    visit.refresh_from_db()
    assert departure.wagon_id == visit.pk and visit.status == st.COMPLETED and visit.net_weight_kg == 4440
    assert Wagon.objects.filter(number="934PPB13").count() == 1  # only the historical trip, no phantom
    assert departure.identity_check.status == "matched"


def test_misread_exit_with_two_similar_trucks_on_site_waits_for_the_operator():
    _remember_history("261BBF13")
    _open_visit("261BB13", 3920, 90)
    _open_visit("261BBF18", 3960, 80)
    departure = event(number="", orientation="rear", weight=8860, minutes=1)
    process_gpt("261BBF13", "rear")
    departure.refresh_from_db()
    assert departure.status == "open" and departure.identity_check.reason == "similar_visit_open"
    assert Wagon.objects.filter(number="261BBF13").count() == 1
    assert Wagon.objects.filter(status=st.AT_SILO).count() == 2


def test_exit_two_edits_from_an_on_site_truck_is_not_completed_from_history():
    _remember_history("165CUA17", 5300)
    visit = _open_visit("065CUA13", 5340, 90)
    departure = event(number="", orientation="rear", weight=10860, minutes=1)
    process_gpt("165CUA17", "rear")
    departure.refresh_from_db()
    visit.refresh_from_db()
    assert departure.status == "open" and departure.identity_check.reason == "similar_visit_open"
    assert visit.status == st.AT_SILO and Wagon.objects.filter(number="165CUA17").count() == 1


def _unread_front(weight, minutes):
    item = event(number="", orientation="front", weight=weight, minutes=minutes)
    process_gpt("", "front")
    item.refresh_from_db()
    assert item.status == "open" and item.identity_check.reason == "plate_unreadable"
    return item


def test_exit_without_open_visit_recovers_the_single_unread_entry_matching_the_remembered_tare():
    # The front camera missed the plate on entry; the empty weight sits in the
    # review queue. The exit with a plate belongs to that entry, not to a tare
    # copied from history at the moment of leaving.
    _remember_history("084ABC13", 3760, minutes=180)
    entry = _unread_front(3760, 45)
    departure = event(number="084ABC13", orientation="rear", weight=8500, minutes=1)
    process_gpt("084ABC13", "rear")
    departure.refresh_from_db()
    entry.refresh_from_db()
    trip = departure.wagon
    assert trip.status == st.COMPLETED and trip.net_weight_kg == 4740
    assert trip.silo_arrived_at == entry.stable_weight_at and trip.arrived_at == entry.stable_weight_at
    assert entry.status == "assigned" and entry.action == "entry" and entry.wagon_id == trip.pk
    assert entry.identity_check.status == "matched" and entry.identity_check.reason == "automatic_entry"
    gross = trip.weighings.get(kind="gross")
    assert gross.source == "scale" and gross.photo.name == entry.photo.name and gross.created_at == entry.stable_weight_at
    assert not trip.weighings.filter(source="historical").exists()
    recovered = EventLog.objects.get(event_type="grain_automatic_binding", payload__unassigned_id=entry.pk)
    assert "заезд восстановлен" in recovered.message and recovered.payload["auto"] is True
    assert recovered.payload["status"] == st.AT_SILO
    assert UnassignedWeighing.objects.filter(status="open").count() == 0


def test_exit_without_open_visit_keeps_historical_tare_when_two_unread_entries_fit():
    _remember_history("084ABC13", 3760, minutes=180)
    first = _unread_front(3760, 45)
    second = _unread_front(3800, 30)
    departure = event(number="084ABC13", orientation="rear", weight=8500, minutes=1)
    process_gpt("084ABC13", "rear")
    departure.refresh_from_db()
    first.refresh_from_db()
    second.refresh_from_db()
    assert departure.wagon.status == st.COMPLETED and departure.wagon.silo_arrived_at is None
    assert departure.wagon.weighings.get(kind="gross").source == "historical"
    assert first.status == "open" and second.status == "open"


def test_exit_without_open_visit_ignores_an_unread_entry_far_from_the_remembered_tare():
    _remember_history("084ABC13", 3760, minutes=180)
    other = _unread_front(4500, 45)
    departure = event(number="084ABC13", orientation="rear", weight=8500, minutes=1)
    process_gpt("084ABC13", "rear")
    departure.refresh_from_db()
    other.refresh_from_db()
    assert departure.wagon.status == st.COMPLETED and departure.wagon.net_weight_kg == 4740
    assert departure.wagon.weighings.get(kind="gross").source == "historical"
    assert other.status == "open"


def test_exit_without_open_visit_ignores_an_unread_entry_the_model_read_as_another_truck():
    _remember_history("084ABC13", 3760, minutes=180)
    other = event(number="", orientation="front", weight=3760, minutes=45)
    process_gpt("777XYZ", "front")  # region cut off: not a valid plate, the weighing stays unread
    other.refresh_from_db()
    assert other.status == "open" and other.vehicle_number == ""
    departure = event(number="084ABC13", orientation="rear", weight=8500, minutes=1)
    process_gpt("084ABC13", "rear")
    departure.refresh_from_db()
    other.refresh_from_db()
    assert departure.wagon.weighings.get(kind="gross").source == "historical"
    assert other.status == "open"


def test_exit_of_a_never_seen_plate_does_not_take_an_unread_entry_without_a_remembered_tare():
    # Nothing says what this truck weighs empty, so no unread weighing can be
    # recognised as its entry; the exit waits for a tare as before.
    entry = _unread_front(3760, 45)
    departure = event(number="084ABC13", orientation="rear", weight=8500, minutes=1)
    process_gpt("084ABC13", "rear")
    departure.refresh_from_db()
    entry.refresh_from_db()
    assert departure.status == "open" and departure.identity_check.reason == "saved_tare_missing"
    assert entry.status == "open" and Wagon.objects.count() == 0


def test_parked_entry_needs_a_finished_check_and_a_remembered_tare():
    _remember_history("084ABC13", 3760, minutes=180)
    entry = event(number="", orientation="front", weight=3760, minutes=45)
    departure = event(number="084ABC13", orientation="rear", weight=8500, minutes=1)
    assert automatic_routing._parked_entry(departure, "084ABC13") is None  # no verdict on the entry yet
    check = WeighingIdentityCheck.objects.create(weighing=entry, status="retrying", reason="photo_pending")
    assert automatic_routing._parked_entry(departure, "084ABC13") is None
    WeighingIdentityCheck.objects.filter(pk=check.pk).update(status="review", reason="plate_unreadable")
    assert automatic_routing._parked_entry(departure, "084ABC13").pk == entry.pk
    VehicleTareMemory.objects.filter(number="084ABC13").delete()
    assert automatic_routing._parked_entry(departure, "084ABC13") is None


def test_exit_recovers_the_entry_whose_weak_front_plate_the_frame_did_not_confirm():
    # The camera voted twice for 084ABC13 at the front, short of confirmation,
    # and the model found no readable plate in the frame. The weighing is what
    # this truck weighs empty; its exit under that plate takes it as the entry.
    _remember_history("084ABC13", 3760, minutes=180)
    entry = weak_event(number="084ABC13", orientation="front", weight=3760, minutes=45)
    process_gpt("", "front")
    entry.refresh_from_db()
    assert entry.status == "open" and entry.vehicle_number == "084ABC13" and entry.identity_check.reason == "plate_unreadable"
    departure = event(number="084ABC13", orientation="rear", weight=8500, minutes=1)
    process_gpt("084ABC13", "rear")
    departure.refresh_from_db()
    entry.refresh_from_db()
    trip = departure.wagon
    assert trip.status == st.COMPLETED and trip.net_weight_kg == 4740
    assert trip.silo_arrived_at == entry.stable_weight_at and trip.arrived_at == entry.stable_weight_at
    assert entry.status == "assigned" and entry.action == "entry" and entry.wagon_id == trip.pk
    assert entry.identity_check.status == "matched" and entry.identity_check.reason == "automatic_entry"
    assert not trip.weighings.filter(source="historical").exists()
    assert UnassignedWeighing.objects.filter(status="open").count() == 0


def test_reentry_takes_the_exit_whose_weak_far_plate_the_frame_did_not_confirm():
    # Two votes of three for 402BJG13 at the rear and no plate readable in the
    # frame: that is not a reading of another truck, the exit is unread.
    first = _open_visit("065CUA13", 5340, 130)
    weak = weak_event(number="402BJG13", orientation="rear", weight=10860, minutes=80)
    process_gpt("", "rear")
    weak.refresh_from_db()
    assert weak.status == "open" and weak.vehicle_number == "402BJG13" and weak.identity_check.reason == "plate_unreadable"
    again = event(number="065CUA13", orientation="front", weight=5320, minutes=20)
    with patch.object(identity, "request_verification") as request:
        identity.process_once()
    request.assert_not_called()
    again.refresh_from_db()
    first.refresh_from_db()
    weak.refresh_from_db()
    assert first.status == st.COMPLETED and first.tare_weight_kg == 10860 and first.net_weight_kg == 5520
    assert weak.status == "assigned" and weak.action == "exit" and weak.wagon_id == first.pk
    assert again.wagon is not None and again.wagon.pk != first.pk and again.wagon.gross_weight_kg == 5320


def test_weak_rear_plate_the_frame_confirmed_still_names_another_truck():
    first = _open_visit("065CUA13", 5340, 130)
    weak = weak_event(number="402BJG13", orientation="rear", weight=10860, minutes=80)
    process_gpt("402BJG13", "rear")
    weak.refresh_from_db()
    assert weak.status == "open" and weak.identity_check.reason == "saved_tare_missing"
    again = event(number="065CUA13", orientation="front", weight=5320, minutes=20)
    process_gpt("065CUA13", "front")
    again.refresh_from_db()
    first.refresh_from_db()
    weak.refresh_from_db()
    assert again.status == "open" and again.identity_check.reason == "previous_exit_missing"
    assert first.status == st.AT_SILO and first.tare_weight_kg is None
    assert weak.status == "open"


def test_reentry_leaves_a_one_edit_neighbour_older_than_the_longest_trip_to_the_operator():
    # 123ABC13 and 124ABC13 are fleet siblings with the same empty weight: a
    # visit under the neighbour's plate is not this truck's just because it is
    # old, so it is neither closed nor cancelled without an exit read as it.
    neighbour = _open_visit("124ABC13", 3800, 13 * 60)
    again = event(number="123ABC13", orientation="front", weight=3850, minutes=20)
    process_gpt("123ABC13", "front")  # one edit from a plate on site: the frame is re-read, as always
    again.refresh_from_db()
    neighbour.refresh_from_db()
    assert neighbour.status == st.AT_SILO and neighbour.tare_weight_kg is None and neighbour.exit_note == ""
    assert again.status == "assigned" and again.wagon.number == "123ABC13" and again.wagon.status == st.AT_SILO
    assert Wagon.objects.filter(status__in=st.ON_SITE_STATUSES).count() == 2


def test_reentry_waits_while_a_weak_far_plate_exit_is_still_being_checked():
    # The camera's two votes named a stranger, but the frame check has not
    # ended: that exit may still turn out to be this truck's, so nothing is
    # decided about the stale visit until it does.
    first = _open_visit("065CUA13", 5340, 13 * 60)
    weak = weak_event(number="402BJG13", orientation="rear", weight=10860, minutes=8 * 60)
    WeighingIdentityCheck.objects.create(
        weighing=weak, status="retrying", reason="verification_unavailable",
        next_attempt_at=timezone.now() + timedelta(minutes=10),
    )
    again = event(number="065CUA13", orientation="front", weight=5340, minutes=20)
    process_gpt("065CUA13", "front")
    again.refresh_from_db()
    first.refresh_from_db()
    weak.refresh_from_db()
    assert again.status == "open" and again.identity_check.reason == "previous_exit_missing"
    assert first.status == st.AT_SILO and first.tare_weight_kg is None
    assert weak.status == "open"


def test_reentry_leaves_the_misread_visit_alone_while_its_candidate_exit_is_unchecked():
    # The phantom is old enough to abandon, but an exit in its window has no
    # verdict yet; once read it may close the phantom properly (T765CUA13).
    phantom = _open_visit("E065CUA", 5380, 13 * 60)
    pending = _pending_unread(10640, 12 * 60 + 30)  # half an hour after that entry, inside its trip window
    again = event(number="065CUA13", orientation="front", weight=5340, minutes=20)
    with patch.object(identity, "request_verification") as request:
        identity.process_once()
    request.assert_not_called()
    again.refresh_from_db()
    phantom.refresh_from_db()
    pending.refresh_from_db()
    assert phantom.status == st.AT_SILO and phantom.tare_weight_kg is None
    assert pending.status == "open"
    assert again.status == "assigned" and again.wagon.number == "065CUA13" and again.wagon.gross_weight_kg == 5340


# ── Reconcile on a timer ─────────────────────────────────────────────────────


def _age_visit(visit, hours):
    """Move the visit's entry ``hours`` back, past the day the identity claim looks back."""
    entered = timezone.now() - timedelta(hours=hours)
    Wagon.objects.filter(pk=visit.pk).update(arrived_at=entered, silo_arrived_at=entered, unloading_started_at=entered)
    UnassignedWeighing.objects.filter(wagon=visit).update(stable_weight_at=entered)
    WeighingRecord.objects.filter(wagon=visit).update(created_at=entered)
    visit.refresh_from_db()
    return visit


def _stale_visit(number, weight, hours):
    """An open visit that entered ``hours`` ago, beyond the day the claim looks back when needed."""
    visit = _open_visit(number, weight, min(hours, 23) * 60)
    return _age_visit(visit, hours) if hours > 23 else visit


def test_reconcile_closes_a_stale_visit_with_the_single_unread_loaded_exit_in_its_window():
    # The truck never came back, so no re-entry could reveal its missed exit;
    # the timer closes the visit with the one loaded weighing nobody read.
    first = _stale_visit("065CUA13", 5340, 13)
    unread = event(number="", orientation="rear", weight=10860, minutes=10 * 60)
    process_gpt("", "rear")
    assert automatic_routing.reconcile_stale_visits() == {"closed": 1, "cancelled": 0, "left": 0}
    first.refresh_from_db()
    unread.refresh_from_db()
    assert first.status == st.COMPLETED and first.tare_weight_kg == 10860 and first.net_weight_kg == 5520
    assert unread.status == "assigned" and unread.action == "exit" and unread.wagon_id == first.pk
    assert unread.identity_check.status == "matched" and unread.identity_check.reason == "automatic_exit"
    recovered = EventLog.objects.get(event_type="grain_automatic_binding", payload__unassigned_id=unread.pk)
    assert "выезд восстановлен по сроку" in recovered.message and "рейс старше 12 ч" in recovered.message
    assert "не прочитан" in recovered.message and recovered.payload["auto"] is True
    assert automatic_routing.reconcile_stale_visits() == {"closed": 0, "cancelled": 0, "left": 0}


def test_reconcile_cancels_a_visit_open_twice_the_longest_trip_without_any_exit():
    first = _stale_visit("065CUA13", 5340, 25)
    assert automatic_routing.reconcile_stale_visits() == {"closed": 0, "cancelled": 1, "left": 0}
    first.refresh_from_db()
    assert first.status == st.CANCELLED and first.tare_weight_kg is None
    assert first.exit_note == "Выезд не зафиксирован: рейс закрыт автоматически по сроку"
    closed = EventLog.objects.get(event_type="grain_status", payload__new_status=st.CANCELLED, payload__wagon_id=first.pk)
    assert closed.payload["auto"] is True
    assert "рейс закрыт без выезда по сроку" in closed.message and "за 24 ч" in closed.message
    assert not Wagon.objects.filter(status__in=st.ON_SITE_STATUSES).exists()
    # The plate is free again: its next entry opens a new visit.
    again = event(number="065CUA13", orientation="front", weight=5320, minutes=20)
    with patch.object(identity, "request_verification") as request:
        identity.process_once()
    request.assert_not_called()
    again.refresh_from_db()
    assert again.wagon is not None and again.wagon.pk != first.pk and again.wagon.gross_weight_kg == 5320


def test_reconcile_leaves_a_visit_younger_than_twice_the_longest_trip_without_a_candidate():
    first = _stale_visit("065CUA13", 5340, 13)
    assert automatic_routing.reconcile_stale_visits() == {"closed": 0, "cancelled": 0, "left": 1}
    first.refresh_from_db()
    assert first.status == st.AT_SILO and first.tare_weight_kg is None and first.exit_note == ""


def test_reconcile_leaves_a_stale_visit_with_two_unread_exits_in_its_window():
    first = _stale_visit("065CUA13", 5340, 25)
    for minutes in (20 * 60, 18 * 60):
        event(number="", orientation="rear", weight=10800, minutes=minutes)
        process_gpt("", "rear")
    assert automatic_routing.reconcile_stale_visits() == {"closed": 0, "cancelled": 0, "left": 1}
    first.refresh_from_db()
    assert first.status == st.AT_SILO and first.tare_weight_kg is None and first.exit_note == ""
    assert UnassignedWeighing.objects.filter(orientation="rear", status="open").count() == 2


def test_reconcile_leaves_a_stale_visit_while_an_unread_exit_in_its_window_has_no_verdict():
    first = _stale_visit("065CUA13", 5340, 25)
    pending = _pending_unread(10860, 20 * 60)
    assert automatic_routing.reconcile_stale_visits() == {"closed": 0, "cancelled": 0, "left": 1}
    first.refresh_from_db()
    pending.refresh_from_db()
    assert first.status == st.AT_SILO and first.tare_weight_kg is None and first.exit_note == ""
    assert pending.status == "open"


def test_reconcile_skips_a_visit_without_a_plate():
    entered = timezone.now() - timedelta(hours=25)
    blank = Wagon.objects.create(
        number="", direction=Wagon.PASSAGE, workflow="simple", cargo_name="Test", status=st.AT_SILO,
        gross_weight_kg=5340, arrived_at=entered, silo_arrived_at=entered,
    )
    unread = event(number="", orientation="rear", weight=10860, minutes=20 * 60)
    process_gpt("", "rear")
    assert automatic_routing.reconcile_stale_visits() == {"closed": 0, "cancelled": 0, "left": 0}
    blank.refresh_from_db()
    unread.refresh_from_db()
    assert blank.status == st.AT_SILO and blank.tare_weight_kg is None
    assert unread.status == "open"


def test_reconcile_goes_on_with_the_next_visit_when_one_fails(caplog):
    # Each visit has the one unread loaded exit of its own window; the first
    # visit's booking breaks, the second is still closed and the failure is logged.
    first = _stale_visit("065CUA13", 5340, 25)
    second = _stale_visit("123ABC13", 4000, 14)
    for weight, minutes in ((10860, 20 * 60), (8500, 10 * 60)):
        event(number="", orientation="rear", weight=weight, minutes=minutes)
        process_gpt("", "rear")
    real = automatic_routing._recover_exit

    def failing(wagon, departure, number, **kwargs):
        if wagon.pk == first.pk:
            raise ValueError("booking_conflict")
        return real(wagon, departure, number, **kwargs)

    with patch.object(automatic_routing, "_recover_exit", side_effect=failing), \
            caplog.at_level(logging.ERROR, logger="apps.grain.automatic_routing"):
        assert automatic_routing.reconcile_stale_visits() == {"closed": 1, "cancelled": 0, "left": 1}
    assert any("065CUA13" in record.getMessage() for record in caplog.records)
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.status == st.AT_SILO and first.tare_weight_kg is None
    assert second.status == st.COMPLETED and second.tare_weight_kg == 8500 and second.net_weight_kg == 4500


def test_process_once_reconciles_stale_visits_at_most_once_per_interval():
    calls = []
    identity._next_reconcile_at = 0.0
    with patch.object(automatic_routing, "reconcile_stale_visits", side_effect=lambda: calls.append(1) or {}), \
            patch.object(identity, "time") as clock:
        clock.monotonic.return_value = 1000.0
        identity.process_once()
        identity.process_once()
        assert len(calls) == 1
        clock.monotonic.return_value = 1000.0 + identity.RECONCILE_INTERVAL_SECONDS - 1
        identity.process_once()
        assert len(calls) == 1
        clock.monotonic.return_value = 1000.0 + identity.RECONCILE_INTERVAL_SECONDS
        identity.process_once()
        assert len(calls) == 2
    assert identity.RECONCILE_INTERVAL_SECONDS == 300


def test_process_once_keeps_booking_when_the_reconcile_fails():
    item = event(number="123ABC13", orientation="front")
    identity._next_reconcile_at = 0.0
    with patch.object(automatic_routing, "reconcile_stale_visits", side_effect=ValueError("too_many_entry_records")):
        identity.process_once()
    item.refresh_from_db()
    assert item.status == "assigned" and item.wagon.gross_weight_kg == 4000


def _reentry_after_a_missed_exit(number, hours):
    """The production shape of a missed exit: the visit is open, the truck came back
    10 h ago (parked as ``previous_exit_missing``) and left again 9 h ago under
    its own plate (parked as ``earlier_entry_pending`` behind that re-entry)."""
    first = _stale_visit(number, 5340, hours)
    again = event(number=number, orientation="front", weight=5320, minutes=10 * 60)
    process_gpt(number, "front")
    again.refresh_from_db()
    assert again.status == "open" and again.identity_check.reason == "previous_exit_missing"
    departure = event(number=number, orientation="rear", weight=10380, minutes=9 * 60)
    with patch.object(identity, "request_verification") as request:
        identity.process_once()
    request.assert_not_called()
    departure.refresh_from_db()
    assert departure.status == "open" and departure.identity_check.reason == "earlier_entry_pending"
    return first, again, departure


def test_reconcile_does_not_close_a_visit_with_an_exit_parked_after_its_plates_re_entry():
    # The exit 9 h ago belongs to the re-entry 10 h ago, not to the visit that
    # entered 13 h ago: the timer looks for that visit's exit only up to the
    # re-entry. With nothing there and the visit younger than 24 h it waits.
    first, again, departure = _reentry_after_a_missed_exit("065CUA13", 13)
    assert automatic_routing.reconcile_stale_visits() == {"closed": 0, "cancelled": 0, "left": 1}
    first.refresh_from_db()
    again.refresh_from_db()
    departure.refresh_from_db()
    assert first.status == st.AT_SILO and first.tare_weight_kg is None and first.exit_note == ""
    assert again.status == "open" and departure.status == "open"
    assert Wagon.objects.filter(number="065CUA13").count() == 1


def test_reconcile_cancels_the_visit_before_its_plates_re_entry_and_the_re_entry_then_takes_the_exit():
    first, again, departure = _reentry_after_a_missed_exit("065CUA13", 13)
    _age_visit(first, 25)
    assert automatic_routing.reconcile_stale_visits() == {"closed": 0, "cancelled": 1, "left": 0}
    first.refresh_from_db()
    departure.refresh_from_db()
    assert first.status == st.CANCELLED and first.tare_weight_kg is None
    assert first.exit_note == "Выезд не зафиксирован: рейс закрыт автоматически по сроку"
    assert departure.status == "open"
    # The plate is free: the parked re-entry opens the next visit and the exit closes it.
    WeighingIdentityCheck.objects.filter(weighing__in=[again, departure]).update(next_attempt_at=timezone.now()-timedelta(seconds=1))
    with patch.object(identity, "request_verification") as request:
        identity.process_once()
        identity.process_once()
    request.assert_not_called()
    again.refresh_from_db()
    departure.refresh_from_db()
    second = again.wagon
    assert second is not None and second.pk != first.pk and second.gross_weight_kg == 5320
    assert departure.wagon_id == second.pk and second.status == st.COMPLETED and second.net_weight_kg == 5060
    assert UnassignedWeighing.objects.filter(status="open").count() == 0


def test_reconcile_neither_closes_nor_cancels_two_stale_visits_that_share_the_only_unread_exit():
    # One loaded weighing nobody read fits both trucks; between two trucks the
    # timer does not choose, and neither visit is cancelled by age while it
    # has a candidate.
    heavy = _stale_visit("065CUA13", 5000, 26)
    heavier = _stale_visit("123ABC13", 9000, 25)
    unread = event(number="", orientation="rear", weight=12000, minutes=20 * 60)
    process_gpt("", "rear")
    assert automatic_routing.reconcile_stale_visits() == {"closed": 0, "cancelled": 0, "left": 2}
    heavy.refresh_from_db()
    heavier.refresh_from_db()
    unread.refresh_from_db()
    assert heavy.status == st.AT_SILO and heavy.tare_weight_kg is None and heavy.exit_note == ""
    assert heavier.status == st.AT_SILO and heavier.tare_weight_kg is None and heavier.exit_note == ""
    assert unread.status == "open"


def test_reconcile_leaves_a_stale_visit_whose_only_candidate_also_fits_a_younger_open_visit():
    stale = _stale_visit("065CUA13", 5340, 13)
    young = _open_visit("123ABC13", 4000, 6 * 60)
    unread = event(number="", orientation="rear", weight=10860, minutes=3 * 60)
    process_gpt("", "rear")
    assert automatic_routing.reconcile_stale_visits() == {"closed": 0, "cancelled": 0, "left": 1}
    stale.refresh_from_db()
    young.refresh_from_db()
    unread.refresh_from_db()
    assert stale.status == st.AT_SILO and stale.tare_weight_kg is None
    assert young.status == st.AT_SILO and young.tare_weight_kg is None
    assert unread.status == "open"


def test_reconcile_takes_an_unchecked_exit_older_than_the_identity_window_as_unread():
    # The identity worker only claims weighings of the last 24 h: a parked
    # exit older than that without any check will never be read, so it is not
    # "still being checked" but unread for good.
    first = _stale_visit("065CUA13", 5340, 30)
    unread = event(number="", orientation="rear", weight=10860, minutes=28 * 60)
    assert not WeighingIdentityCheck.objects.filter(weighing=unread).exists()
    assert automatic_routing.reconcile_stale_visits() == {"closed": 1, "cancelled": 0, "left": 0}
    first.refresh_from_db()
    unread.refresh_from_db()
    assert first.status == st.COMPLETED and first.tare_weight_kg == 10860 and first.net_weight_kg == 5520
    assert unread.status == "assigned" and unread.action == "exit" and unread.wagon_id == first.pk
    # The audit marker exists even though no check row did before.
    assert unread.identity_check.status == "matched" and unread.identity_check.reason == "automatic_exit"


def test_reconcile_decides_exclusivity_before_any_visit_of_the_run_is_cancelled():
    # A's exit window ends at A's own re-entry, so the unread exit after it is
    # not A's candidate and A is cancelled by age. That exit still fits A at
    # the time the run is planned, so B may not take it just because A was
    # cancelled a moment earlier in the same run.
    first = _stale_visit("065CUA13", 5000, 26)
    again = event(number="065CUA13", orientation="front", weight=5000, minutes=22 * 60)
    process_gpt("065CUA13", "front")
    again.refresh_from_db()
    assert again.status == "open" and again.identity_check.reason == "previous_exit_missing"
    other = _stale_visit("123ABC13", 5000, 25)
    unread = event(number="", orientation="rear", weight=12000, minutes=20 * 60)
    process_gpt("", "rear")
    assert automatic_routing.reconcile_stale_visits() == {"closed": 0, "cancelled": 1, "left": 1}
    first.refresh_from_db()
    other.refresh_from_db()
    unread.refresh_from_db()
    assert first.status == st.CANCELLED
    assert other.status == st.AT_SILO and other.tare_weight_kg is None and other.exit_note == ""
    assert unread.status == "open"


def test_reconcile_keeps_a_loaded_weighing_of_unknown_direction_as_the_exit_candidate():
    # The plate was read on a loaded weighing whose direction stayed unknown:
    # a truck coming back is empty, so this is the visit's exit, not a re-entry
    # that would cut the window short.
    first = _stale_visit("065CUA13", 5340, 13)
    loaded = event(number="065CUA13", orientation="", weight=10860, minutes=10 * 60)
    WeighingIdentityCheck.objects.create(weighing=loaded, status="review", reason="orientation_unknown")
    assert automatic_routing.reconcile_stale_visits() == {"closed": 1, "cancelled": 0, "left": 0}
    first.refresh_from_db()
    loaded.refresh_from_db()
    assert first.status == st.COMPLETED and first.tare_weight_kg == 10860
    assert loaded.status == "assigned" and loaded.action == "exit" and loaded.wagon_id == first.pk


def test_manual_binding_takes_the_lane_lock_before_the_weighing():
    # The operator's binding shares the lane -> weighing -> visit lock order
    # of automatic booking and the timer, so it never deadlocks against them.
    from apps.grain import services
    from apps.grain.models import PassageScaleAutomationState
    visit = Wagon.objects.create(number="123ABC13", direction=Wagon.PASSAGE, workflow="simple", cargo_name="Test", status=st.ARRIVED)
    item = event(number="123ABC13", orientation="front", weight=4000, minutes=5)
    PassageScaleAutomationState.objects.all().delete()
    services.assign_unassigned_weighing(item, visit, None)
    assert PassageScaleAutomationState.objects.filter(scale_number="truck").exists()
    item.refresh_from_db()
    assert item.status == "assigned" and item.action == "entry" and item.wagon.gross_weight_kg == 4000


# ── An exit that names the truck a misread front plate opened a visit for ────


def test_plate_overlap_counts_the_characters_the_plate_cores_share():
    # The region is left out: two strangers from one region would share it.
    assert automatic_routing._plate_overlap("253ZOU81", "532OUB13") == 5
    assert automatic_routing._plate_overlap("E065CUA", "065CUA13") == 6
    assert automatic_routing._plate_overlap("132XYZ13", "123ABC13") == 3
    assert automatic_routing._plate_overlap("237AAX01", "853UVA13") == 2
    assert automatic_routing.ORPHAN_PLATE_OVERLAP == 4


def test_exit_with_a_plate_merges_the_orphan_visit_opened_under_a_misread_front_plate():
    # The front camera read 253ZOU81 for 532OUB13 (six characters in common);
    # nothing ever left under that spelling, the visit's empty weight is what
    # 532OUB13 weighs empty, and the exit reads fine. The visit is this truck's.
    _remember_history("532OUB13", 5680, minutes=180)
    phantom = _open_visit("253ZOU81", 5600, 20)
    departure = event(number="532OUB13", orientation="rear", weight=11460, minutes=1)
    process_gpt("532OUB13", "rear")
    departure.refresh_from_db()
    phantom.refresh_from_db()
    assert departure.wagon_id == phantom.pk and phantom.number == "532OUB13"
    assert phantom.status == st.COMPLETED and phantom.gross_weight_kg == 5600 and phantom.net_weight_kg == 5860
    assert not phantom.weighings.filter(source="historical").exists()
    renamed = EventLog.objects.get(event_type="grain_number", payload__wagon_id=phantom.pk)
    assert renamed.payload["previous_number"] == "253ZOU81" and renamed.payload["number"] == "532OUB13"
    merged = EventLog.objects.get(event_type="grain_automatic_binding", message__contains="рейс был открыт под номером")
    assert "253ZOU81" in merged.message and "532OUB13" in merged.message and merged.payload["wagon_id"] == phantom.pk
    assert not Wagon.objects.filter(number="253ZOU81").exists()
    assert not VehicleTareMemory.objects.filter(number="253ZOU81").exists()
    assert VehicleTareMemory.objects.get(number="532OUB13").record.weight_kg == 5600
    assert not Wagon.objects.filter(status__in=st.ON_SITE_STATUSES).exists()
    assert UnassignedWeighing.objects.filter(status="open").count() == 0


def test_exit_with_a_plate_does_not_merge_a_visit_whose_plate_shares_too_few_characters():
    _remember_history("853UVA13", 5600, minutes=180)
    other = _open_visit("237AAX01", 5600, 20)
    departure = event(number="853UVA13", orientation="rear", weight=11460, minutes=1)
    process_gpt("853UVA13", "rear")
    departure.refresh_from_db()
    other.refresh_from_db()
    assert other.status == st.AT_SILO and other.number == "237AAX01" and other.tare_weight_kg is None
    assert departure.wagon_id != other.pk and departure.wagon.status == st.COMPLETED
    assert departure.wagon.weighings.get(kind="gross").source == "historical"


def test_exit_with_a_plate_does_not_merge_when_two_orphan_visits_fit():
    _remember_history("532OUB13", 5680, minutes=180)
    first = _open_visit("253ZOU81", 5600, 30)
    second = _open_visit("235ZOU18", 5640, 20)
    departure = event(number="532OUB13", orientation="rear", weight=11460, minutes=1)
    process_gpt("532OUB13", "rear")
    departure.refresh_from_db()
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.status == st.AT_SILO and first.number == "253ZOU81"
    assert second.status == st.AT_SILO and second.number == "235ZOU18"
    assert departure.wagon.status == st.COMPLETED and departure.wagon.weighings.get(kind="gross").source == "historical"
    assert Wagon.objects.filter(status__in=st.ON_SITE_STATUSES).count() == 2


def test_exit_with_a_plate_does_not_merge_a_visit_whose_plate_has_completed_trips_of_its_own():
    # 253ZOU81 has left the site under that spelling before: it is a real
    # plate of another truck, not a misreading of 532OUB13.
    _remember_history("253ZOU81", 5600, minutes=240)
    _remember_history("532OUB13", 5680, minutes=180)
    real = _open_visit("253ZOU81", 5600, 20)
    departure = event(number="532OUB13", orientation="rear", weight=11460, minutes=1)
    process_gpt("532OUB13", "rear")
    departure.refresh_from_db()
    real.refresh_from_db()
    assert real.status == st.AT_SILO and real.number == "253ZOU81" and real.tare_weight_kg is None
    assert departure.wagon_id != real.pk and departure.wagon.weighings.get(kind="gross").source == "historical"
    assert Wagon.objects.filter(status__in=st.ON_SITE_STATUSES).count() == 1


def _exit_leaves_the_visit_alone(visit, departure, *, net):
    departure.refresh_from_db()
    visit.refresh_from_db()
    assert visit.status == st.AT_SILO and visit.tare_weight_kg is None
    assert departure.wagon_id != visit.pk and departure.wagon.status == st.COMPLETED
    assert departure.wagon.weighings.get(kind="gross").source == "historical" and departure.wagon.net_weight_kg == net
    assert Wagon.objects.filter(status__in=st.ON_SITE_STATUSES).count() == 1


def test_exit_with_a_plate_does_not_merge_a_visit_from_the_same_region_sharing_only_digits():
    # 132XYZ13 and 123ABC13 share the region and three digits: the region
    # says nothing about the truck, and three characters are a stranger.
    _remember_history("123ABC13", 5680, minutes=180)
    other = _open_visit("132XYZ13", 5600, 20)
    departure = event(number="123ABC13", orientation="rear", weight=11460, minutes=1)
    process_gpt("123ABC13", "rear")
    _exit_leaves_the_visit_alone(other, departure, net=5780)
    other.refresh_from_db()
    assert other.number == "132XYZ13"


def test_exit_with_a_plate_does_not_merge_a_visit_whose_entry_weight_is_not_this_trucks_tare():
    _remember_history("532OUB13", 5680, minutes=180)
    other = _open_visit("253ZOU81", 6100, 20)  # 420 kg from what 532OUB13 weighs empty
    departure = event(number="532OUB13", orientation="rear", weight=11460, minutes=1)
    process_gpt("532OUB13", "rear")
    _exit_leaves_the_visit_alone(other, departure, net=5780)


def test_exit_with_a_plate_does_not_merge_a_visit_it_would_leave_barely_heavier():
    _remember_history("532OUB13", 5680, minutes=180)
    other = _open_visit("253ZOU81", 5600, 20)
    departure = event(number="532OUB13", orientation="rear", weight=6400, minutes=1)  # 800 kg over the entry: not loaded
    process_gpt("532OUB13", "rear")
    _exit_leaves_the_visit_alone(other, departure, net=720)


def test_exit_with_a_plate_takes_neither_the_orphan_visit_nor_the_parked_entry_when_both_fit():
    # A visit under a misread plate and an unread front weighing both weigh
    # what this truck weighs empty: two stories for one exit. Neither is
    # told; the exit completes from history and the operator sees both.
    _remember_history("532OUB13", 5680, minutes=180)
    entry = _unread_front(5650, 40)
    phantom = _open_visit("253ZOU81", 5600, 20)
    departure = event(number="532OUB13", orientation="rear", weight=11460, minutes=1)
    process_gpt("532OUB13", "rear")
    _exit_leaves_the_visit_alone(phantom, departure, net=5780)
    phantom.refresh_from_db()
    entry.refresh_from_db()
    assert phantom.number == "253ZOU81"
    assert entry.status == "open" and entry.wagon_id is None
    assert not Wagon.objects.filter(number="532OUB13", status__in=st.ON_SITE_STATUSES).exists()
    assert Wagon.objects.filter(number="532OUB13").count() == 2  # the remembered trip and the one completed from history
