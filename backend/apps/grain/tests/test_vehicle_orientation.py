"""Front/rear camera verdict drives entry vs exit without an operator."""

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from apps.cameras import ai as camera_ai
from apps.cameras.models import VehiclePlateEvent
from apps.grain import scale, services, vehicle_weight_capture
from apps.grain import statuses as st
from apps.grain.models import UnassignedWeighing, Wagon, WeighingRecord
from django.core.files.base import ContentFile
from django.utils import timezone

pytestmark = pytest.mark.django_db

JPEG = b"\xff\xd8\xff\xe0" + b"1" * 32


@pytest.fixture(autouse=True)
def orientation_settings(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED = False
    settings.VEHICLE_PLATE_WEIGHT_FIRST_CAMERA = "cam1"
    settings.VEHICLE_PLATE_WEIGHT_FIRST_SOURCE = "main"
    settings.VEHICLE_PLATE_AUTO_EXPORT_CARGO_NAME = "Отруби"
    settings.VEHICLE_PLATE_AUTO_EXPORT_MIN_TRIP_SECONDS = 60
    settings.VEHICLE_PLATE_AUTO_MISSED_ENTRY_MAX_AGE_HOURS = 24
    settings.TRUCK_SCALE_TIMEOUT_SECONDS = 3


def _reading(weight: str) -> scale.ScaleReading:
    return scale.ScaleReading(
        weight_kg=Decimal(weight),
        age_seconds=Decimal("0.2"),
        updated_at="2026-09-05T10:00:00Z",
    )


def _event(number="854ANB13", *, detected_at=None):
    return VehiclePlateEvent.objects.create(
        event_id=uuid.uuid4(),
        vehicle_number=number,
        camera="cam1",
        source="main",
        detected_at=detected_at or timezone.now() - timedelta(seconds=3),
        stationary_seconds=Decimal("0"),
        confirmation_votes=3,
        detector_confidence=Decimal("0.9100"),
        ocr_confidence=Decimal("0.9600"),
        payload_json={},
    )


def _apply(event, weight, *, orientation):
    return services.apply_automatic_passage_scale_sample(
        event.pk,
        reading=_reading(weight),
        photo_request_id=event.event_id,
        photo_camera="cam1",
        orientation=orientation,
    )


def _open_trip(number="854ANB13", *, entry=3880, entered_ago=timedelta(hours=2)):
    entered_at = timezone.now() - entered_ago
    wagon = Wagon.objects.create(
        number=number,
        direction=Wagon.PASSAGE,
        workflow="simple",
        cargo_name="Отруби",
        status=st.AT_SILO,
        arrived_at=entered_at,
        silo_arrived_at=entered_at,
        gross_weight_kg=entry,
        number_source="camera",
    )
    WeighingRecord.objects.create(
        wagon=wagon, kind="gross", weight_kg=entry, source="scale", orientation="rear"
    )
    return wagon


def _parked(weight, *, ago, orientation="", with_photo=True):
    item = UnassignedWeighing.objects.create(
        weight_kg=weight,
        stable_weight_at=timezone.now() - ago,
        scale_number="truck",
        scale_age_seconds=Decimal("0.200"),
        scale_updated_at="2026-09-05T09:00:00Z",
        camera="cam1",
        photo_request_id=uuid.uuid4(),
        orientation=orientation,
        reason="open_passages_exist",
    )
    if with_photo:
        item.photo.save(f"{item.photo_request_id}.jpg", ContentFile(JPEG), save=True)
    return item


# ── Recognized plate + camera verdict ────────────────────────────────────────


def test_rear_without_open_trip_rebuilds_the_trip_from_the_parked_empty_weight():
    parked = _parked(3880, ago=timedelta(hours=1), orientation="front")

    result = _apply(_event(), "8760", orientation="rear")

    wagon = Wagon.objects.get(pk=result.wagon_id)
    parked.refresh_from_db()
    assert result.action == "exit"
    assert wagon.number == "854ANB13"
    assert wagon.status == st.COMPLETED
    assert (wagon.entry_weight_kg, wagon.exit_weight_kg, wagon.net_weight_kg) == (
        3880,
        8760,
        4880,
    )
    assert wagon.arrived_at == parked.stable_weight_at
    assert parked.status == UnassignedWeighing.ASSIGNED
    assert parked.action == "entry"
    assert parked.wagon_id == wagon.pk
    entry, exit_record = (
        wagon.weighings.get(kind="gross"),
        wagon.weighings.get(kind="tare"),
    )
    assert entry.photo.name == parked.photo.name
    assert entry.orientation == "front"
    assert exit_record.orientation == "rear"


def test_rear_without_open_trip_or_parked_entry_is_parked_with_the_plate():
    _parked(9000, ago=timedelta(hours=1), orientation="rear")  # heavier: not an entry
    event = _event()

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


def test_front_with_open_trip_closes_it_from_the_parked_loaded_weight():
    stale = _open_trip(entry=3880, entered_ago=timedelta(hours=3))
    parked = _parked(8700, ago=timedelta(hours=1), orientation="rear")

    result = _apply(_event(), "3900", orientation="front")

    stale.refresh_from_db()
    parked.refresh_from_db()
    fresh = Wagon.objects.get(pk=result.wagon_id)
    assert result.action == "entry"
    assert fresh.pk != stale.pk
    assert (fresh.status, fresh.entry_weight_kg) == (st.AT_SILO, 3900)
    assert stale.status == st.COMPLETED
    assert (stale.exit_weight_kg, stale.net_weight_kg) == (8700, 4820)
    assert parked.status == UnassignedWeighing.ASSIGNED
    assert parked.action == "exit"
    assert parked.wagon_id == stale.pk


def test_front_with_open_trip_and_nothing_parked_cancels_the_stale_trip():
    stale = _open_trip(entry=3880)
    event = _event()

    result = _apply(event, "3900", orientation="front")

    stale.refresh_from_db()
    fresh = Wagon.objects.get(pk=result.wagon_id)
    assert result.action == "entry"
    assert fresh.pk != stale.pk
    assert fresh.status == st.AT_SILO
    assert stale.status == st.CANCELLED
    assert stale.exited_at == event.detected_at
    assert "не взвешен" in stale.exit_note


def test_plate_missing_one_series_letter_matches_the_open_trip():
    trip = _open_trip("849ATT13", entry=4160)

    result = _apply(_event("849AT13"), "9120", orientation="rear")

    trip.refresh_from_db()
    assert result.action == "exit"
    assert result.wagon_id == trip.pk
    assert trip.status == st.COMPLETED
    assert trip.number == "849ATT13"
    assert trip.exit_weight_kg == 9120


def test_plate_similarity_never_guesses_between_two_candidates():
    _open_trip("849ATT13", entry=4160)
    _open_trip("849ATB13", entry=4200)

    result = _apply(_event("849AT13"), "9120", orientation="")

    # Two compatible trips: fall back to the old rule (unknown plate = entry).
    assert result.action == "entry"
    assert Wagon.objects.filter(number="849AT13").exists()


def test_without_camera_verdict_the_passage_state_still_decides():
    trip = _open_trip(entry=3880)

    result = _apply(_event(), "8760", orientation="")

    trip.refresh_from_db()
    assert (result.action, result.wagon_id) == ("exit", trip.pk)
    assert trip.status == st.COMPLETED


# ── Plate not recognized + camera verdict ────────────────────────────────────


def _unidentified(weight, *, orientation):
    return services.apply_unidentified_passage_scale_sample(
        reading=_reading(weight),
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


def test_rear_without_plate_closes_the_only_trip_waiting_for_a_loaded_weight():
    waiting = _open_trip("465BDS13", entry=3760)
    _open_trip("506WKZ13", entry=9000)  # heavier than this weight: not a candidate

    result = _unidentified("8900", orientation="rear")

    waiting.refresh_from_db()
    assert (result.action, result.wagon_id) == ("exit", waiting.pk)
    assert waiting.status == st.COMPLETED
    assert waiting.exit_weight_kg == 8900
    assert not UnassignedWeighing.objects.exists()


def test_rear_without_plate_and_two_candidates_is_parked_for_the_operator():
    _open_trip("465BDS13", entry=3760)
    _open_trip("506WKZ13", entry=3840)

    result = _unidentified("8900", orientation="rear")

    parked = UnassignedWeighing.objects.get(pk=result.unassigned_id)
    assert result.action == "unassigned"
    assert (parked.reason, parked.orientation) == ("open_passages_exist", "rear")


def test_rear_without_plate_and_no_candidate_is_parked_as_missing_entry():
    _open_trip("465BDS13", entry=9000)

    result = _unidentified("8900", orientation="rear")

    parked = UnassignedWeighing.objects.get(pk=result.unassigned_id)
    assert (parked.reason, parked.orientation) == ("entry_missing", "rear")
    assert Wagon.objects.count() == 1


def test_rear_without_plate_on_an_empty_site_never_creates_a_bogus_entry():
    result = _unidentified("8900", orientation="rear")

    assert result.action == "unassigned"
    assert not Wagon.objects.exists()


def test_no_verdict_keeps_the_old_rule_for_unrecognized_plates():
    assert _unidentified("3900", orientation="").action == "entry"
    assert _unidentified("8900", orientation="").action == "unassigned"


# ── Operator repair: the booked entry was really the exit ────────────────────


def test_assigning_an_earlier_lighter_weight_swaps_it_into_the_entry():
    wagon = _open_trip(entry=8760, entered_ago=timedelta(hours=1))
    booked = wagon.weighings.get(kind="gross")
    booked.photo.save("booked.jpg", ContentFile(JPEG), save=True)
    parked = _parked(3880, ago=timedelta(hours=2), orientation="front")

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
    later_loaded = _parked(8760, ago=timedelta(minutes=10), orientation="rear")

    services.assign_unassigned_weighing(later_loaded, wagon, None)

    wagon.refresh_from_db()
    assert wagon.status == st.COMPLETED
    assert (wagon.entry_weight_kg, wagon.exit_weight_kg) == (3880, 8760)


def test_create_passage_from_a_parked_exit_uses_its_plate_by_default(
    auth_client, user_with_perms
):
    operator = user_with_perms("orientation-op", codes=["grain.weigh", "grain.view"])
    parked = _parked(3880, ago=timedelta(minutes=5), orientation="front")
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
    safe = vehicle_weight_capture._safe_ai_payload(
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
