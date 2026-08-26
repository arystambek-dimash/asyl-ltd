import json
import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.db import DatabaseError
from django.test import override_settings

from apps.cameras.models import VehiclePlateEvent

pytestmark = pytest.mark.django_db

WEBHOOK_URL = "/api/integrations/vehicle-plate-events"
LIST_URL = "/api/vehicle-plate-events"
WEBHOOK_TOKEN = "vehicle-plate-test-token-that-is-long-enough"
EVENT_ID = "0fa68fe2-6fd8-4cc5-93f7-4b90ae690f19"


@pytest.fixture(autouse=True)
def vehicle_plate_settings(settings):
    settings.VEHICLE_PLATE_WEBHOOK_TOKEN = WEBHOOK_TOKEN
    settings.VEHICLE_PLATE_WEBHOOK_MAX_BODY_BYTES = 64 * 1024


def payload(*, event_id=EVENT_ID, vehicle_number="123ABC02", **overrides):
    body = {
        "schema_version": 1,
        "event_id": event_id,
        "event_type": "vehicle_plate_detected",
        "detected_at": "2026-08-25T12:30:00.000Z",
        "vehicle_number": vehicle_number,
        "camera": "cam1",
        "source": "main",
        "stationary_seconds": 3.4,
        "confirmation": {
            "votes": 3,
            "detector_confidence": 0.91,
            "ocr_confidence": 0.96,
        },
        "bbox": {
            "pixels": [820, 510, 1050, 590],
            "normalized": {
                "x": 0.320312,
                "y": 0.354167,
                "w": 0.089844,
                "h": 0.055556,
            },
        },
        "vehicle_roi": {
            "coordinate_space": "normalized",
            "points": [
                {"x": 0.38, "y": 0.20},
                {"x": 0.63, "y": 0.32},
                {"x": 0.98, "y": 1.00},
                {"x": 0.18, "y": 1.00},
            ],
        },
        "image": {"width": 2560, "height": 1440},
        "models": {
            "detector": "vehicle-license-plate.pt",
            "ocr": "en_PP-OCRv5_mobile_rec",
        },
    }
    body.update(overrides)
    return body


def post_event(client, body=None, *, token=WEBHOOK_TOKEN, key=None, secure=True):
    body = payload() if body is None else body
    event_id = body.get("event_id", EVENT_ID) if isinstance(body, dict) else EVENT_ID
    headers = {
        "HTTP_AUTHORIZATION": f"Bearer {token}",
        "HTTP_IDEMPOTENCY_KEY": key if key is not None else event_id,
    }
    return client.post(
        WEBHOOK_URL,
        body,
        format="json",
        secure=secure,
        **headers,
    )


def create_event(*, event_id=None, detected_at=None, **overrides):
    return VehiclePlateEvent.objects.create(
        event_id=event_id or uuid.uuid4(),
        vehicle_number=overrides.pop("vehicle_number", "123ABC02"),
        camera=overrides.pop("camera", "cam1"),
        source=overrides.pop("source", "main"),
        detected_at=detected_at or datetime(2026, 8, 25, 12, 30, tzinfo=UTC),
        stationary_seconds=overrides.pop("stationary_seconds", Decimal("3.400")),
        confirmation_votes=overrides.pop("confirmation_votes", 3),
        detector_confidence=overrides.pop(
            "detector_confidence", Decimal("0.9100")
        ),
        ocr_confidence=overrides.pop("ocr_confidence", Decimal("0.9600")),
        payload_json=overrides.pop("payload_json", {}),
        **overrides,
    )


def test_webhook_saves_metadata_and_returns_201(api_client):
    response = post_event(api_client)

    assert response.status_code == 201, response.data
    event = VehiclePlateEvent.objects.get()
    assert response.data == {
        "ok": True,
        "duplicate": False,
        "event_id": EVENT_ID,
        "vehicle_event_id": event.pk,
    }
    assert event.vehicle_number == "123ABC02"
    assert event.camera == "cam1"
    assert event.source == "main"
    assert event.detected_at == datetime(2026, 8, 25, 12, 30, tzinfo=UTC)
    assert event.stationary_seconds == Decimal("3.400")
    assert event.confirmation_votes == 3
    assert event.detector_confidence == Decimal("0.9100")
    assert event.ocr_confidence == Decimal("0.9600")
    assert event.processing_status == VehiclePlateEvent.RECEIVED
    assert event.payload_json["detected_at"] == "2026-08-25T12:30:00+00:00"
    assert event.payload_json["models"]["detector"] == "vehicle-license-plate.pt"
    assert response["Cache-Control"] == "no-store"


def test_webhook_is_idempotent_and_does_not_mutate_first_event(api_client):
    first = post_event(api_client)
    changed = payload(stationary_seconds=9.2)
    second = post_event(api_client, changed)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.data == {
        "ok": True,
        "duplicate": True,
        "event_id": EVENT_ID,
    }
    assert VehiclePlateEvent.objects.count() == 1
    assert VehiclePlateEvent.objects.get().stationary_seconds == Decimal("3.400")


def test_webhook_requires_matching_idempotency_key(api_client):
    missing = api_client.post(
        WEBHOOK_URL,
        payload(),
        format="json",
        secure=True,
        HTTP_AUTHORIZATION=f"Bearer {WEBHOOK_TOKEN}",
    )
    mismatch = post_event(api_client, key=str(uuid.uuid4()))
    malformed = post_event(api_client, key="not-a-uuid")

    assert missing.status_code == 400
    assert missing.data["code"] == "invalid_idempotency_key"
    assert mismatch.status_code == 400
    assert mismatch.data["code"] == "idempotency_key_mismatch"
    assert malformed.status_code == 400
    assert malformed.data["code"] == "invalid_idempotency_key"
    assert not VehiclePlateEvent.objects.exists()


def test_webhook_requires_https(api_client):
    response = post_event(api_client, secure=False)

    assert response.status_code == 400
    assert response.data["code"] == "https_required"
    assert not VehiclePlateEvent.objects.exists()


@pytest.mark.parametrize("authorization", [None, "", "Basic abc", "Bearer wrong"])
def test_webhook_rejects_missing_or_wrong_token(api_client, authorization):
    headers = {"HTTP_IDEMPOTENCY_KEY": EVENT_ID}
    if authorization is not None:
        headers["HTTP_AUTHORIZATION"] = authorization
    response = api_client.post(
        WEBHOOK_URL,
        payload(),
        format="json",
        secure=True,
        **headers,
    )

    assert response.status_code == 401
    assert response["WWW-Authenticate"] == "Bearer"
    assert WEBHOOK_TOKEN not in json.dumps(response.data)
    assert not VehiclePlateEvent.objects.exists()


def test_unconfigured_token_fails_closed(api_client, settings):
    settings.VEHICLE_PLATE_WEBHOOK_TOKEN = ""

    response = post_event(api_client)

    assert response.status_code == 401
    assert not VehiclePlateEvent.objects.exists()


@pytest.mark.parametrize(
    ("change", "value"),
    [
        ("schema_version", 2),
        ("schema_version", True),
        ("event_id", "not-a-uuid"),
        ("event_id", {"token": "must-not-log"}),
        ("event_type", "wagon_plate_detected"),
        ("detected_at", "2026-08-25 12:30:00"),
        ("detected_at", "2026-08-25X12:30:00Z"),
        ("detected_at", "2026-08-25\x0012:30:00Z"),
        ("detected_at", "2026-02-30T12:30:00Z"),
        ("detected_at", "0000-01-01T12:30:00Z"),
        ("detected_at", "2026-13-25T12:30:00Z"),
        ("detected_at", "2026-08-25T25:30:00Z"),
        ("detected_at", "2026-08-25T12:60:00Z"),
        ("detected_at", "2026-08-25T12:30:60Z"),
        ("detected_at", "2026-08-25T12:30:00+24:00"),
        ("detected_at", "2026-08-25T12:30:00+12:60"),
        ("detected_at", "9999-12-31T23:59:59-23:59"),
        ("detected_at", "0001-01-01T00:00:00+23:59"),
        ("detected_at", "not-a-date"),
        ("vehicle_number", "123АВС02"),
        ("vehicle_number", "123abc02"),
        ("vehicle_number", "12ABC02"),
        ("vehicle_number", {"password": "must-not-log"}),
        ("camera", "camera1"),
        ("camera", "cam0"),
        ("camera", "cam" + "1" * 100),
        ("source", "preview"),
        ("stationary_seconds", 2.99),
        ("stationary_seconds", "3.4"),
    ],
)
def test_webhook_rejects_invalid_top_level_contract(api_client, change, value):
    body = payload(**{change: value})
    response = post_event(
        api_client,
        body,
        key=EVENT_ID if change == "event_id" else body.get("event_id"),
    )

    assert response.status_code == 400, response.data
    assert response.data["code"] == "invalid_payload"
    assert not VehiclePlateEvent.objects.exists()


@pytest.mark.parametrize(
    "confirmation",
    [
        None,
        [],
        {"votes": 2, "detector_confidence": 0.9, "ocr_confidence": 0.9},
        {"votes": True, "detector_confidence": 0.9, "ocr_confidence": 0.9},
        {"votes": 32768, "detector_confidence": 0.9, "ocr_confidence": 0.9},
        {"votes": 3, "detector_confidence": -0.1, "ocr_confidence": 0.9},
        {"votes": 3, "detector_confidence": 0.9, "ocr_confidence": 1.1},
        {"votes": 3, "detector_confidence": "0.9", "ocr_confidence": 0.9},
    ],
)
def test_webhook_rejects_invalid_confirmation(api_client, confirmation):
    response = post_event(api_client, payload(confirmation=confirmation))

    assert response.status_code == 400, response.data
    assert not VehiclePlateEvent.objects.exists()


def test_webhook_ignores_unknown_fields_and_persists_only_metadata_allowlist(
    api_client,
):
    body = payload(
        future_contract={"enabled": True, "api_token": "body-secret"},
        password="password-in-body",
        photo="inline-photo",
        snapshot_blob="data:image/jpeg;base64,disguised-image",
        content="unrecognized-content",
        image={
            "width": 2560,
            "height": 1440,
            "base64": "inline-image",
            "future_metadata": "not-approved-under-image",
        },
    )

    response = post_event(api_client, body)

    assert response.status_code == 201, response.data
    stored = VehiclePlateEvent.objects.get().payload_json
    assert stored["image"] == {"width": 2560, "height": 1440}
    assert stored["bbox"] == body["bbox"]
    assert stored["vehicle_roi"] == body["vehicle_roi"]
    assert stored["models"] == body["models"]
    assert set(stored) == {
        "schema_version",
        "event_id",
        "event_type",
        "detected_at",
        "vehicle_number",
        "camera",
        "source",
        "stationary_seconds",
        "confirmation",
        "bbox",
        "vehicle_roi",
        "image",
        "models",
    }
    serialized = json.dumps(stored)
    assert "inline-photo" not in serialized
    assert "inline-image" not in serialized
    assert "body-secret" not in serialized
    assert "password-in-body" not in serialized
    assert "disguised-image" not in serialized
    assert "unrecognized-content" not in serialized
    assert WEBHOOK_TOKEN not in serialized


def test_webhook_accepts_maximum_confirmation_votes(api_client):
    body = payload(
        confirmation={
            "votes": 32767,
            "detector_confidence": 1,
            "ocr_confidence": 0,
        }
    )

    response = post_event(api_client, body)

    assert response.status_code == 201, response.data
    assert VehiclePlateEvent.objects.get().confirmation_votes == 32767


def test_payload_projection_rejects_uri_disguised_as_model_name(api_client):
    body = payload(
        models={
            "detector": "data:image/jpeg;base64,disguised",
            "ocr": "en_PP-OCRv5_mobile_rec",
        }
    )

    response = post_event(api_client, body)

    assert response.status_code == 201, response.data
    stored_models = VehiclePlateEvent.objects.get().payload_json["models"]
    assert stored_models == {"ocr": "en_PP-OCRv5_mobile_rec"}
    assert "disguised" not in json.dumps(stored_models)


def test_webhook_rejects_non_object_and_malformed_json(api_client):
    headers = {
        "HTTP_AUTHORIZATION": f"Bearer {WEBHOOK_TOKEN}",
        "HTTP_IDEMPOTENCY_KEY": EVENT_ID,
    }
    array = api_client.post(
        WEBHOOK_URL,
        [],
        format="json",
        secure=True,
        **headers,
    )
    malformed = api_client.generic(
        "POST",
        WEBHOOK_URL,
        b"{",
        content_type="application/json",
        secure=True,
        **headers,
    )

    assert array.status_code == 400
    assert array.data["code"] == "invalid_payload"
    assert malformed.status_code == 400
    assert malformed.data["code"] == "parse_error"


def test_webhook_rejects_body_over_endpoint_limit(api_client, settings):
    settings.VEHICLE_PLATE_WEBHOOK_MAX_BODY_BYTES = 512
    body = payload(padding="x" * 1024)

    response = post_event(api_client, body)

    assert response.status_code == 413
    assert response.data["code"] == "payload_too_large"
    assert not VehiclePlateEvent.objects.exists()


def test_webhook_returns_503_for_temporary_database_error(api_client):
    with patch.object(
        VehiclePlateEvent.objects,
        "get_or_create",
        side_effect=DatabaseError("database unavailable"),
    ):
        response = post_event(api_client)

    assert response.status_code == 503
    assert response.data == {
        "detail": "Temporary storage error",
        "code": "temporary_storage_error",
    }
    assert "database unavailable" not in json.dumps(response.data)


def test_webhook_logs_metadata_status_and_result_without_token(api_client, caplog):
    caplog.set_level(logging.INFO, logger="apps.cameras.vehicle_plate_events")

    assert post_event(api_client).status_code == 201
    assert post_event(api_client).status_code == 200

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert f"event_id={EVENT_ID}" in messages
    assert "vehicle_number=123ABC02" in messages
    assert "camera=cam1" in messages
    assert "http_status=201 result=created" in messages
    assert "http_status=200 result=duplicate" in messages
    assert WEBHOOK_TOKEN not in messages


THROTTLED = {
    "DEFAULT_THROTTLE_RATES": {"vehicle_plate_webhook": "2/min"},
    "NUM_PROXIES": 0,
}


@override_settings(REST_FRAMEWORK=THROTTLED)
def test_webhook_is_rate_limited_per_source_ip(api_client):
    cache.clear()
    codes = []
    for _ in range(3):
        body = payload(event_id=str(uuid.uuid4()))
        codes.append(post_event(api_client, body).status_code)

    assert codes == [201, 201, 429]
    assert VehiclePlateEvent.objects.count() == 2


@override_settings(REST_FRAMEWORK=THROTTLED)
def test_webhook_rate_limit_also_bounds_invalid_credentials(api_client):
    cache.clear()

    codes = [
        post_event(api_client, token="wrong-credential").status_code
        for _ in range(3)
    ]

    assert codes == [401, 401, 429]
    assert not VehiclePlateEvent.objects.exists()


def test_internal_list_requires_events_permission(api_client, auth_client, make_user):
    assert api_client.get(LIST_URL).status_code == 401

    ordinary = make_user("plate-no-permission")
    assert auth_client(ordinary).get(LIST_URL).status_code == 403


def test_integration_webhook_to_database_to_internal_api(
    api_client,
    auth_client,
    user_with_perms,
):
    created = post_event(api_client)
    assert created.status_code == 201, created.data
    stored = VehiclePlateEvent.objects.get(pk=created.data["vehicle_event_id"])

    viewer = user_with_perms("plate-integration-viewer", codes=["events.view"])
    listed = auth_client(viewer).get(
        f"{LIST_URL}?vehicle_number=123ABC02&camera=cam1"
        "&date_from=2026-08-25&date_to=2026-08-25&limit=10"
    )

    assert listed.status_code == 200, listed.data
    assert listed.data["count"] == 1
    row = listed.data["results"][0]
    assert row["id"] == stored.pk == created.data["vehicle_event_id"]
    assert row["event_id"] == str(stored.event_id) == created.data["event_id"]
    assert row["vehicle_number"] == stored.vehicle_number == "123ABC02"
    assert row["camera"] == stored.camera == "cam1"
    assert row["stationary_seconds"] == float(stored.stationary_seconds) == 3.4
    assert row["ocr_confidence"] == float(stored.ocr_confidence) == 0.96
    assert row["processing_status"] == stored.processing_status == "received"


def test_internal_list_returns_paginated_contract(auth_client, user_with_perms):
    viewer = user_with_perms("plate-viewer", codes=["events.view"])
    client = auth_client(viewer)
    first = create_event(
        vehicle_number="111AAA01",
        detected_at=datetime(2026, 8, 25, 10, 0, tzinfo=UTC),
    )
    second = create_event(
        vehicle_number="222BBB02",
        detected_at=datetime(2026, 8, 25, 11, 0, tzinfo=UTC),
        stationary_seconds=Decimal("4.500"),
    )
    create_event(
        vehicle_number="333CCC03",
        detected_at=datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
    )

    response = client.get(f"{LIST_URL}?page=1&limit=2")

    assert response.status_code == 200, response.data
    assert set(response.data) == {"count", "next", "previous", "results"}
    assert response.data["count"] == 3
    assert response.data["previous"] is None
    assert response.data["next"] is not None
    assert len(response.data["results"]) == 2
    row = response.data["results"][1]
    assert row == {
        "id": second.pk,
        "event_id": str(second.event_id),
        "vehicle_number": "222BBB02",
        "camera": "cam1",
        "source": "main",
        "detected_at": "2026-08-25T16:00:00+05:00",
        "stationary_seconds": 4.5,
        "confirmation_votes": 3,
        "detector_confidence": 0.91,
        "ocr_confidence": 0.96,
        "processing_status": "received",
        "processing_attempts": 0,
        "processing_action": "",
        "processing_error": "",
        "processing_started_at": None,
        "processed_at": None,
    }
    assert first.pk not in {item["id"] for item in response.data["results"]}
    assert response["Cache-Control"] == "no-store"


def test_internal_list_filters_dates_plate_and_camera(
    auth_client,
    user_with_perms,
):
    viewer = user_with_perms("plate-filter-viewer", codes=["events.view"])
    client = auth_client(viewer)
    wanted = create_event(
        vehicle_number="123ABC02",
        camera="cam1",
        detected_at=datetime(2026, 8, 25, 8, 0, tzinfo=UTC),
    )
    create_event(
        vehicle_number="999XYZ01",
        camera="cam2",
        detected_at=datetime(2026, 8, 24, 8, 0, tzinfo=UTC),
    )
    create_event(
        vehicle_number="123ABC02",
        camera="cam2",
        detected_at=datetime(2026, 8, 26, 8, 0, tzinfo=UTC),
    )

    response = client.get(
        f"{LIST_URL}?date_from=2026-08-25&date_to=2026-08-25"
        "&vehicle_number=123&camera=cam1&page_size=10"
    )

    assert response.status_code == 200, response.data
    assert response.data["count"] == 1
    assert response.data["results"][0]["id"] == wanted.pk


@pytest.mark.parametrize(
    "query",
    [
        "date_from=25-08-2026",
        "date_from=2026-08-26&date_to=2026-08-25",
        "vehicle_number=123-%20ABC",
        "camera=cam0",
        "camera=cam" + "1" * 100,
        "limit=0",
        "limit=201",
        "limit=abc",
    ],
)
def test_internal_list_rejects_invalid_filters(
    auth_client,
    user_with_perms,
    query,
):
    viewer = user_with_perms(f"plate-invalid-{uuid.uuid4()}", codes=["events.view"])

    response = auth_client(viewer).get(f"{LIST_URL}?{query}")

    assert response.status_code == 400, response.data
