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
from apps.cameras.tests.vehicle_plate_fakes import EVENT_ID, WEBHOOK_TOKEN, WEBHOOK_URL, payload, post_event

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def vehicle_plate_settings(settings):
    settings.VEHICLE_PLATE_WEBHOOK_TOKEN = WEBHOOK_TOKEN
    settings.VEHICLE_PLATE_WEBHOOK_MAX_BODY_BYTES = 64 * 1024


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
    assert event.payload_json == {}
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


def test_webhook_ignores_unknown_fields_and_stores_no_raw_body(api_client):
    body = payload(
        future_contract={"enabled": True, "api_token": "body-secret"},
        password="password-in-body",
        photo="inline-photo",
        image={"width": 2560, "height": 1440, "base64": "inline-image"},
    )

    response = post_event(api_client, body)

    assert response.status_code == 201, response.data
    assert VehiclePlateEvent.objects.get().payload_json == {}


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
