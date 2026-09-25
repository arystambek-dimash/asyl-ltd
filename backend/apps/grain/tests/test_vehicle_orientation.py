"""Front/rear camera verdict drives entry vs exit without an operator."""

import uuid
from datetime import timedelta

import pytest
from apps.cameras import ai as camera_ai
from apps.cameras.models import VehiclePlateEvent
from apps.eventlog.models import EventLog
from apps.grain import plate_recognition, services
from apps.grain import statuses as st
from apps.grain.models import UnassignedWeighing, Wagon, WeighingRecord
from apps.grain.tests.factories import (
    JPEG,
    passage_trip,
    scale_reading,
    unassigned_weighing,
    vehicle_plate_event,
)
from django.core.files.base import ContentFile
from django.utils import timezone

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def orientation_settings(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED = False
    settings.VEHICLE_PLATE_WEIGHT_FIRST_CAMERA = "cam1"
    settings.VEHICLE_PLATE_WEIGHT_FIRST_SOURCE = "main"
    settings.VEHICLE_PLATE_AUTO_EXPORT_CARGO_NAME = "Отруби"
    settings.VEHICLE_PLATE_AUTO_EXPORT_MIN_TRIP_SECONDS = 60
    settings.TRUCK_SCALE_TIMEOUT_SECONDS = 3


def _apply(event, weight, *, orientation):
    return services.apply_automatic_passage_scale_sample(
        event.pk,
        reading=scale_reading(weight),
        photo_request_id=event.event_id,
        photo_camera="cam1",
        orientation=orientation,
    )


def _open_trip(number="854ANB13", *, entry=3880, entered_ago=timedelta(hours=2)):
    entered_at = timezone.now() - entered_ago
    wagon = passage_trip(number, status=st.AT_SILO, entry=entry, arrived_at=entered_at, silo_arrived_at=entered_at)
    WeighingRecord.objects.create(
        wagon=wagon, kind="gross", weight_kg=entry, source="scale", orientation="rear"
    )
    return wagon


# ── Recognized plate + camera verdict ────────────────────────────────────────


def test_rear_does_not_guess_from_one_parked_empty_weight():
    entry = unassigned_weighing(3880, ago=timedelta(hours=1), orientation="front")
    result = _apply(vehicle_plate_event(vehicle_number="854ANB13"), "8760", orientation="rear")
    entry.refresh_from_db()
    assert result.action == "unassigned"
    assert entry.status == UnassignedWeighing.OPEN
    assert not Wagon.objects.exists()


def test_rear_without_open_trip_or_parked_entry_is_parked_with_the_plate():
    unassigned_weighing(9000, ago=timedelta(hours=1), orientation="rear")  # heavier: not an entry
    event = vehicle_plate_event(vehicle_number="854ANB13")

    result = _apply(event, "8760", orientation="rear")

    event.refresh_from_db()
    parked = UnassignedWeighing.objects.get(pk=result.unassigned_id)
    assert result.action == "unassigned"
    assert result.wagon_id is None
    assert parked.reason == "entry_missing"
    assert parked.vehicle_number == "854ANB13"
    assert parked.orientation == "rear"
    assert parked.weight_kg == 8760
    assert parked.photo_request_id == event.event_id
    assert event.processing_status == VehiclePlateEvent.PROCESSED
    assert event.processing_action == "unassigned"
    assert not Wagon.objects.exists()


def test_rear_exit_without_entry_is_journaled_like_an_unidentified_weighing():
    result = _apply(vehicle_plate_event(vehicle_number="854ANB13"), "8760", orientation="rear")

    logged = EventLog.objects.get(event_type="grain_unassigned_weighing")
    assert "854ANB13" in logged.message
    assert logged.payload["unassigned_id"] == result.unassigned_id
    assert logged.payload["vehicle_number"] == "854ANB13"
    assert logged.payload["reason"] == "entry_missing"
    assert logged.payload["weight_kg"] == 8760


def test_rear_does_not_name_the_only_blank_trip():
    blank = _open_trip("", entry=3960)
    result = _apply(vehicle_plate_event(vehicle_number="233LUB13"), "9200", orientation="rear")
    blank.refresh_from_db()
    assert result.action == "unassigned"
    assert blank.number == ""
    assert blank.exit_weight_kg is None
    assert blank.status == st.AT_SILO


@pytest.mark.parametrize(
    ("entry", "entered_ago"),
    [
        pytest.param(3960, timedelta(seconds=10), id="opened_seconds_ago"),
        pytest.param(9500, timedelta(hours=2), id="heavier_than_the_exit_weight"),
        pytest.param(3960, timedelta(hours=30), id="older_than_the_entry_window"),
    ],
)
def test_rear_with_plate_ignores_an_unsuitable_blank_trip(entry, entered_ago):
    blank = _open_trip("", entry=entry, entered_ago=entered_ago)

    result = _apply(vehicle_plate_event(vehicle_number="233LUB13"), "9200", orientation="rear")

    blank.refresh_from_db()
    parked = UnassignedWeighing.objects.get(pk=result.unassigned_id)
    assert (result.action, parked.reason) == ("unassigned", "entry_missing")
    assert (blank.number, blank.status, blank.exit_weight_kg) == ("", st.AT_SILO, None)


def test_rear_with_plate_never_guesses_between_two_blank_trips():
    _open_trip("", entry=3960)
    _open_trip("", entry=4100)

    result = _apply(vehicle_plate_event(vehicle_number="233LUB13"), "9200", orientation="rear")

    parked = UnassignedWeighing.objects.get(pk=result.unassigned_id)
    assert result.action == "unassigned"
    assert (parked.reason, parked.vehicle_number) == ("entry_missing", "233LUB13")
    assert Wagon.objects.filter(number="", status=st.AT_SILO).count() == 2


def test_rear_preserves_both_blank_and_named_trips():
    blank = _open_trip("", entry=3960)
    named = _open_trip("465BDS13", entry=3800)
    result = _apply(vehicle_plate_event(vehicle_number="233LUB13"), "9200", orientation="rear")
    assert result.action == "unassigned"
    for wagon in (blank, named):
        wagon.refresh_from_db()
        assert wagon.status == st.AT_SILO
        assert wagon.exit_weight_kg is None


def test_front_does_not_close_trip_from_a_parked_weight():
    stale = _open_trip(entry=3880, entered_ago=timedelta(hours=3))
    parked = unassigned_weighing(8700, ago=timedelta(hours=1), orientation="rear")
    result = _apply(vehicle_plate_event(vehicle_number="854ANB13"), "3900", orientation="front")
    stale.refresh_from_db()
    parked.refresh_from_db()
    assert result.error == "open_trip_conflict"
    assert stale.status == st.AT_SILO
    assert stale.exit_weight_kg is None
    assert parked.status == UnassignedWeighing.OPEN


def test_front_preserves_open_trip_for_review():
    stale = _open_trip(entry=3880)
    result = _apply(vehicle_plate_event(vehicle_number="854ANB13"), "3900", orientation="front")
    stale.refresh_from_db()
    assert result.error == "open_trip_conflict"
    assert stale.status == st.AT_SILO
    assert stale.exit_weight_kg is None
    assert Wagon.objects.count() == 1


def test_missing_series_letter_requires_confirmation():
    trip = _open_trip("849ATT13", entry=4160)
    result = _apply(vehicle_plate_event(vehicle_number="849AT13"), "9120", orientation="rear")
    trip.refresh_from_db()
    assert result.action == "unassigned"
    assert trip.status == st.AT_SILO
    assert trip.exit_weight_kg is None


def test_plate_similarity_never_guesses_between_two_candidates():
    _open_trip("849ATT13", entry=4160)
    _open_trip("849ATB13", entry=4200)
    result = _apply(vehicle_plate_event(vehicle_number="849AT13"), "9120", orientation="")
    assert result.error == "orientation_unknown"
    assert not Wagon.objects.filter(number="849AT13").exists()


def test_without_camera_verdict_the_passage_state_still_decides():
    trip = _open_trip(entry=3880)

    result = _apply(vehicle_plate_event(vehicle_number="854ANB13"), "8760", orientation="")

    trip.refresh_from_db()
    assert (result.action, result.wagon_id) == ("exit", trip.pk)
    assert trip.status == st.COMPLETED


# ── Plate not recognized + camera verdict ────────────────────────────────────


def _unidentified(weight, *, orientation):
    return services.apply_unidentified_passage_scale_sample(
        reading=scale_reading(weight),
        camera="cam1",
        request_id=uuid.uuid4(),
        stable_weight_at=timezone.now() - timedelta(seconds=2),
        orientation=orientation,
    )


def test_front_without_plate_is_a_new_entry_even_with_open_passages():
    _open_trip("465BDS13", entry=3760)

    result = _unidentified("3900", orientation="front")

    wagon = Wagon.objects.get(pk=result.wagon_id)
    assert result.action == "entry"
    assert wagon.number == ""
    assert wagon.status == st.AT_SILO
    assert wagon.weighings.get().orientation == "front"


def test_rear_without_plate_never_closes_trip_by_weight():
    waiting = _open_trip(entry=3900)
    result = _unidentified("8900", orientation="rear")
    waiting.refresh_from_db()
    assert result.action == "unassigned"
    assert waiting.status == st.AT_SILO
    assert waiting.exit_weight_kg is None


def test_rear_without_plate_and_two_candidates_is_parked_for_the_operator():
    _open_trip("465BDS13", entry=3760)
    _open_trip("506WKZ13", entry=3840)

    result = _unidentified("8900", orientation="rear")

    parked = UnassignedWeighing.objects.get(pk=result.unassigned_id)
    assert result.action == "unassigned"
    assert (parked.reason, parked.orientation) == ("plate_unreadable", "rear")


def test_rear_without_plate_and_no_candidate_is_parked_as_missing_entry():
    _open_trip("465BDS13", entry=9000)

    result = _unidentified("8900", orientation="rear")

    parked = UnassignedWeighing.objects.get(pk=result.unassigned_id)
    assert (parked.reason, parked.orientation) == ("plate_unreadable", "rear")
    assert Wagon.objects.count() == 1


def test_rear_without_plate_on_an_empty_site_never_creates_a_bogus_entry():
    result = _unidentified("8900", orientation="rear")

    assert result.action == "unassigned"
    assert not Wagon.objects.exists()


def test_no_verdict_never_guesses_entry_from_an_empty_site():
    result = _unidentified("8900", orientation="")
    assert result.action == "unassigned"
    assert UnassignedWeighing.objects.get().reason == "orientation_unknown"
    assert not Wagon.objects.exists()


# ── Operator repair: the booked entry was really the exit ────────────────────


def test_assigning_an_earlier_lighter_weight_swaps_it_into_the_entry():
    wagon = _open_trip(entry=8760, entered_ago=timedelta(hours=1))
    booked = wagon.weighings.get(kind="gross")
    booked.photo.save("booked.jpg", ContentFile(JPEG), save=True)
    parked = unassigned_weighing(3880, ago=timedelta(hours=2), orientation="front")

    services.assign_unassigned_weighing(parked, wagon, None)

    wagon.refresh_from_db()
    parked.refresh_from_db()
    booked.refresh_from_db()
    assert wagon.status == st.COMPLETED
    assert (wagon.entry_weight_kg, wagon.exit_weight_kg, wagon.net_weight_kg) == (
        3880,
        8760,
        4880,
    )
    assert wagon.silo_arrived_at == parked.stable_weight_at
    assert wagon.arrived_at == parked.stable_weight_at
    assert booked.kind == "tare"
    assert booked.photo.name.endswith("booked.jpg")
    entry = wagon.weighings.get(kind="gross")
    assert entry.weight_kg == 3880
    assert entry.photo.name == parked.photo.name
    assert (parked.status, parked.action) == (UnassignedWeighing.ASSIGNED, "entry")


def test_swap_needs_an_earlier_and_lighter_or_front_facing_weight():
    wagon = _open_trip(entry=3880, entered_ago=timedelta(hours=1))
    later_loaded = unassigned_weighing(8760, ago=timedelta(minutes=10), orientation="rear")

    services.assign_unassigned_weighing(later_loaded, wagon, None)

    wagon.refresh_from_db()
    assert wagon.status == st.COMPLETED
    assert (wagon.entry_weight_kg, wagon.exit_weight_kg) == (3880, 8760)


def test_create_passage_from_a_parked_exit_uses_its_plate_by_default(
    auth_client, user_with_perms
):
    operator = user_with_perms("orientation-op", codes=["grain.weigh", "grain.view"])
    parked = unassigned_weighing(3880, ago=timedelta(minutes=5), orientation="front")
    UnassignedWeighing.objects.filter(pk=parked.pk).update(
        vehicle_number="854ANB13", reason="entry_missing"
    )

    response = auth_client(operator).post(
        f"/api/grain/unassigned-weighings/{parked.pk}/create-passage/",
        {"number": "", "cargo_name": ""},
        format="json",
    )

    assert response.status_code == 200, response.data
    assert response.data["vehicle_number"] == "854ANB13"
    assert response.data["orientation"] == "front"
    assert Wagon.objects.get().number == "854ANB13"


# ── Camera-PC payload contract ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"orientation": {"label": "front", "confidence": 0.97}}, ("front", 0.97)),
        ({"orientation": {"label": None, "confidence": 0.51}}, ("", 0.51)),
        ({"orientation": {"label": "side", "confidence": 0.9}}, ("", 0.9)),
        ({"orientation": {"label": "rear", "confidence": 7}}, ("rear", None)),
        ({"orientation": "rear"}, ("", None)),
        ({}, ("", None)),
        (None, ("", None)),
    ],
)
def test_vehicle_orientation_parsing_is_lenient(payload, expected):
    assert camera_ai.vehicle_orientation(payload) == expected


def test_safe_ai_payload_keeps_a_bounded_orientation_block():
    safe = plate_recognition.safe_ai_payload(
        {
            "status": "no_match",
            "orientation": {
                "label": "rear",
                "confidence": 0.93,
                "raw_label": "rear" * 20,
                "junk": {"nested": True},
            },
        }
    )

    assert safe["orientation"] == {
        "label": "rear",
        "confidence": 0.93,
        "raw_label": "rearrearrearrear",
    }


def test_safe_ai_payload_keeps_bounded_no_match_diagnostics():
    read = {
        "frame": 7,
        "variant": "two_row",
        "raw_text": "2 684 13BFE",
        "number": None,
        "confidence": 0.81,
        "detector_confidence": 0.68,
        "bbox_w": 92.0,
        "bbox_h": 61.5,
    }
    safe = plate_recognition.safe_ai_payload(
        {
            "status": "no_match",
            "detected_frames": 19,
            "ocr_candidates": 21,
            "accepted_reads": 2,
            "confirmation_votes": 3,
            "confirmation_window_seconds": 10.0,
            "best_detector_confidence": 0.68,
            "votes": {"684BFE13": 2, "": 5, "X" * 40: 1, "bad": "many"},
            "last_reads": [read] * 12 + ["junk", {"confidence": float("nan")}],
        }
    )

    assert (safe["detected_frames"], safe["ocr_candidates"], safe["accepted_reads"]) == (
        19,
        21,
        2,
    )
    assert (safe["confirmation_votes"], safe["confirmation_window_seconds"]) == (3, 10.0)
    assert safe["best_detector_confidence"] == 0.68
    assert safe["votes"] == {"684BFE13": 2}
    assert len(safe["last_reads"]) == 8
    assert safe["last_reads"][0] == {
        "frame": 7,
        "variant": "two_row",
        "raw_text": "2 684 13BFE",
        "confidence": 0.81,
        "detector_confidence": 0.68,
        "bbox_w": 92.0,
        "bbox_h": 61.5,
    }
    assert "last_reads" not in plate_recognition.safe_ai_payload(
        {"status": "no_match", "last_reads": "not a list"}
    )


def test_two_plausible_parked_entries_are_never_paired_by_guess():
    unassigned_weighing(3880, ago=timedelta(hours=2), orientation="front")
    unassigned_weighing(3900, ago=timedelta(hours=1), orientation="front")

    result = _apply(vehicle_plate_event(vehicle_number="854ANB13"), "8760", orientation="rear")

    assert result.action == "unassigned"
    assert UnassignedWeighing.objects.get(pk=result.unassigned_id).reason == "entry_missing"
    assert not Wagon.objects.exists()


def test_one_front_photo_does_not_identify_a_leaving_truck():
    unassigned_weighing(3900, ago=timedelta(hours=1), orientation="front")
    unassigned_weighing(4000, ago=timedelta(hours=1), orientation="")
    result = _apply(vehicle_plate_event(vehicle_number="854ANB13"), "8760", orientation="rear")
    assert result.action == "unassigned"
    assert UnassignedWeighing.objects.filter(status="open").count() == 3
    assert not Wagon.objects.exists()


def test_two_parked_weights_do_not_cancel_open_trip():
    stale = _open_trip(entry=3880, entered_ago=timedelta(hours=3))
    unassigned_weighing(8700, ago=timedelta(hours=1), orientation="rear")
    unassigned_weighing(8800, ago=timedelta(minutes=30), orientation="rear")
    result = _apply(vehicle_plate_event(vehicle_number="854ANB13"), "3900", orientation="front")
    stale.refresh_from_db()
    assert result.error == "open_trip_conflict"
    assert stale.status == st.AT_SILO
    assert stale.exit_weight_kg is None
