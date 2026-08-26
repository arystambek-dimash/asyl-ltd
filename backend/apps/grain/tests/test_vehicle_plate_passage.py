import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from apps.cameras.models import VehiclePlateEvent
from apps.grain import statuses as st
from apps.grain.models import Wagon
from django.db import IntegrityError
from django.utils import timezone

pytestmark = pytest.mark.django_db

CANDIDATES_URL = "/api/grain/wagons/vehicle-plate-candidates/"
PASSAGE_URL = "/api/grain/wagons/passage/"
WEBHOOK_URL = "/api/integrations/vehicle-plate-events"
WEBHOOK_TOKEN = "grain-vehicle-plate-test-token-long-enough"


@pytest.fixture
def gate_operator(user_with_perms):
    return user_with_perms(
        "vehicle-plate-gate",
        codes=["grain.view", "grain.arrive"],
    )


def create_event(
    *,
    detected_at=None,
    received_at=None,
    camera="cam1",
    source="main",
    processing_status=VehiclePlateEvent.RECEIVED,
    vehicle_number="123ABC02",
):
    now = timezone.now()
    event = VehiclePlateEvent.objects.create(
        event_id=uuid.uuid4(),
        vehicle_number=vehicle_number,
        camera=camera,
        source=source,
        detected_at=detected_at or now - timedelta(seconds=30),
        stationary_seconds=Decimal("3.400"),
        confirmation_votes=3,
        detector_confidence=Decimal("0.9100"),
        ocr_confidence=Decimal("0.9600"),
        payload_json={"models": {"detector": "metadata-only"}},
        processing_status=processing_status,
    )
    if received_at is not None:
        VehiclePlateEvent.objects.filter(pk=event.pk).update(received_at=received_at)
        event.refresh_from_db()
    return event


def webhook_payload(*, event_id, detected_at):
    return {
        "schema_version": 1,
        "event_id": str(event_id),
        "event_type": "vehicle_plate_detected",
        "detected_at": detected_at.isoformat(),
        "vehicle_number": "123ABC02",
        "camera": "cam1",
        "source": "main",
        "stationary_seconds": 3.4,
        "confirmation": {
            "votes": 3,
            "detector_confidence": 0.91,
            "ocr_confidence": 0.96,
        },
    }


def test_candidates_require_only_grain_arrive_permission(
    api_client,
    auth_client,
    user_with_perms,
):
    viewer = user_with_perms("vehicle-plate-viewer", codes=["grain.view"])
    gate_only = user_with_perms("vehicle-plate-arrive", codes=["grain.arrive"])

    assert api_client.get(CANDIDATES_URL).status_code == 401
    assert auth_client(viewer).get(CANDIDATES_URL).status_code == 403
    assert auth_client(gate_only).get(CANDIDATES_URL).status_code == 200


def test_candidates_expose_only_minimal_fresh_metadata(auth_client, gate_operator):
    event = create_event()

    response = auth_client(gate_operator).get(CANDIDATES_URL)

    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    assert len(response.data) == 1
    candidate = response.data[0]
    assert set(candidate) == {
        "event_id",
        "vehicle_number",
        "camera",
        "source",
        "detected_at",
        "stationary_seconds",
        "ocr_confidence",
    }
    assert candidate["event_id"] == str(event.event_id)
    assert candidate["vehicle_number"] == "123ABC02"
    assert candidate["camera"] == "cam1"
    assert candidate["source"] == "main"
    assert candidate["stationary_seconds"] == 3.4
    assert candidate["ocr_confidence"] == 0.96


def test_candidates_require_both_timestamps_fresh_and_fixed_lane(
    auth_client,
    gate_operator,
):
    now = timezone.now()
    fresh = create_event(
        detected_at=now - timedelta(seconds=20),
        received_at=now - timedelta(seconds=10),
    )
    create_event(
        detected_at=now - timedelta(minutes=6),
        received_at=now - timedelta(seconds=10),
        vehicle_number="111AAA01",
    )
    create_event(
        detected_at=now - timedelta(seconds=20),
        received_at=now - timedelta(minutes=6),
        vehicle_number="222BBB02",
    )
    create_event(
        detected_at=now + timedelta(minutes=2),
        received_at=now - timedelta(seconds=10),
        vehicle_number="333CCC03",
    )
    create_event(
        detected_at=now - timedelta(seconds=20),
        received_at=now + timedelta(minutes=2),
        vehicle_number="444DDD04",
    )
    create_event(camera="cam2", vehicle_number="555EEE05")
    create_event(source="sub", vehicle_number="666FFF06")
    create_event(
        processing_status=VehiclePlateEvent.PROCESSED,
        vehicle_number="777GGG07",
    )
    linked = create_event(vehicle_number="888HHH08")
    Wagon.objects.create(
        number=linked.vehicle_number,
        direction=Wagon.PASSAGE,
        status=st.ARRIVED,
        vehicle_plate_event=linked,
    )

    response = auth_client(gate_operator).get(CANDIDATES_URL)

    assert response.status_code == 200
    assert [row["event_id"] for row in response.data] == [str(fresh.event_id)]


def test_candidates_are_newest_first_and_limited_to_five(
    auth_client,
    gate_operator,
):
    now = timezone.now()
    events = [
        create_event(
            detected_at=now - timedelta(seconds=index + 1),
            received_at=now - timedelta(seconds=1),
            vehicle_number=f"{index:03d}ABC02",
        )
        for index in range(7)
    ]

    response = auth_client(gate_operator).get(CANDIDATES_URL)

    assert response.status_code == 200
    assert [row["event_id"] for row in response.data] == [
        str(event.event_id) for event in events[:5]
    ]


def test_webhook_candidate_passage_and_list_share_server_derived_plate(
    api_client,
    auth_client,
    gate_operator,
    settings,
):
    settings.VEHICLE_PLATE_WEBHOOK_TOKEN = WEBHOOK_TOKEN
    settings.VEHICLE_PLATE_WEBHOOK_MAX_BODY_BYTES = 64 * 1024
    event_id = uuid.uuid4()
    detected_at = timezone.now() - timedelta(seconds=10)

    webhook = api_client.post(
        WEBHOOK_URL,
        webhook_payload(event_id=event_id, detected_at=detected_at),
        format="json",
        secure=True,
        HTTP_AUTHORIZATION=f"Bearer {WEBHOOK_TOKEN}",
        HTTP_IDEMPOTENCY_KEY=str(event_id),
    )
    assert webhook.status_code == 201, webhook.data

    client = auth_client(gate_operator)
    candidates = client.get(CANDIDATES_URL)
    assert candidates.status_code == 200
    assert [row["event_id"] for row in candidates.data] == [str(event_id)]

    passage = client.post(
        PASSAGE_URL,
        {
            "vehicle_plate_event_id": str(event_id),
            "number": "999XYZ01",
            "number_source": "manual",
            "camera_source": "cam99",
            "cargo_name": "Отруби",
        },
        format="json",
    )
    assert passage.status_code == 201, passage.data
    assert passage.data["number"] == "123ABC02"
    assert passage.data["number_source"] == "camera"
    assert passage.data["number_camera_source"] == "cam1"

    event = VehiclePlateEvent.objects.get(event_id=event_id)
    wagon = Wagon.objects.get(pk=passage.data["id"])
    assert wagon.vehicle_plate_event == event
    assert event.processing_status == VehiclePlateEvent.PROCESSED

    listed = client.get("/api/grain/wagons/?scope=on_site&direction=passage")
    assert listed.status_code == 200
    row = next(item for item in listed.data if item["id"] == wagon.pk)
    assert row["number"] == "123ABC02"
    assert row["number_source"] == "camera"
    assert row["number_camera_source"] == "cam1"
    assert client.get(CANDIDATES_URL).data == []


def test_passage_rejects_an_event_that_was_already_claimed(
    auth_client,
    gate_operator,
):
    event = create_event()
    client = auth_client(gate_operator)
    body = {
        "vehicle_plate_event_id": str(event.event_id),
        "cargo_name": "Отруби",
    }

    first = client.post(PASSAGE_URL, body, format="json")
    second = client.post(PASSAGE_URL, body, format="json")

    assert first.status_code == 201
    assert second.status_code == 400
    assert second.data["code"] == "vehicle_plate_event_unavailable"
    assert Wagon.objects.filter(vehicle_plate_event=event).count() == 1


def test_passage_turns_a_unique_claim_race_into_a_safe_rejection(
    auth_client,
    gate_operator,
):
    event = create_event()

    with patch.object(
        Wagon.objects,
        "create",
        side_effect=IntegrityError("simulated concurrent one-to-one claim"),
    ):
        response = auth_client(gate_operator).post(
            PASSAGE_URL,
            {
                "vehicle_plate_event_id": str(event.event_id),
                "cargo_name": "Отруби",
            },
            format="json",
        )

    assert response.status_code == 400
    assert response.data["code"] == "vehicle_plate_event_unavailable"
    event.refresh_from_db()
    assert event.processing_status == VehiclePlateEvent.RECEIVED
    assert not Wagon.objects.filter(vehicle_plate_event=event).exists()


def test_manual_passage_stays_compatible_and_ignores_source_spoofing(
    auth_client,
    gate_operator,
):
    response = auth_client(gate_operator).post(
        PASSAGE_URL,
        {
            "number": "123 ABC 02",
            "cargo_name": "Отруби",
            "note": "Ручной резервный путь",
            "number_source": "camera",
            "camera_source": "cam1",
        },
        format="json",
    )

    assert response.status_code == 201, response.data
    assert response.data["number"] == "123ABC02"
    assert response.data["number_source"] == "manual"
    assert response.data["number_camera_source"] == ""
    wagon = Wagon.objects.get(pk=response.data["id"])
    assert wagon.vehicle_plate_event_id is None


def test_passage_rejects_a_stale_event_even_when_uuid_is_known(
    auth_client,
    gate_operator,
):
    now = timezone.now()
    event = create_event(
        detected_at=now - timedelta(minutes=6),
        received_at=now - timedelta(seconds=10),
    )

    response = auth_client(gate_operator).post(
        PASSAGE_URL,
        {
            "vehicle_plate_event_id": str(event.event_id),
            "cargo_name": "Отруби",
        },
        format="json",
    )

    assert response.status_code == 400
    assert response.data["code"] == "vehicle_plate_event_unavailable"
    assert not Wagon.objects.exists()
