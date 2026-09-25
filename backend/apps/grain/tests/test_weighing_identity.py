import http.client
import json
from datetime import timedelta
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from django.core.files.base import ContentFile
from django.utils import timezone

from apps.common import openai_responses
from apps.grain import statuses as st, weighing_identity as identity
from apps.grain.models import (
    AutomaticPassageCapture,
    UnassignedWeighing,
    WeighingIdentityCheck,
    WeighingRecord,
)
from apps.grain.tests.factories import JPEG, passage_trip, unassigned_weighing

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def config(identity_ai):
    identity_ai.VEHICLE_PLATE_AUTO_EXPORT_MIN_TRIP_SECONDS = 60
    identity_ai.WEIGHING_AI_ENTRY_MAX_HOURS = 12


def parked(*, number="149ABC13", orientation="rear", weight=8000, ago=timedelta(seconds=1)):
    return unassigned_weighing(weight, ago=ago, vehicle_number=number, orientation=orientation)


@pytest.fixture
def visit():
    at = timezone.now() - timedelta(minutes=30)
    wagon = passage_trip(
        "449ABC13", status=st.AT_SILO, entry=4000, arrived_at=at, silo_arrived_at=at, number_source="manual",
    )
    record = WeighingRecord.objects.create(
        wagon=wagon,
        kind="gross",
        weight_kg=4000,
        source="scale",
        scale_number="truck",
        photo_camera="cam1",
        orientation="front",
        photo_request_id=uuid4(),
    )
    record.photo.save("entry.jpg", ContentFile(JPEG))
    return wagon, record, parked()


def unreadable():
    return {"exit": {"plate": "", "plate_clear": False, "orientation": "unknown"}}


def test_exact_exit_ocr_also_deferred_when_enabled(visit):
    capture = AutomaticPassageCapture.objects.create(
        idempotency_key=uuid4(),
        camera="cam1",
        orientation="rear",
        weight_kg=8000,
        stable_weight_at=timezone.now(),
        vehicle_number="449ABC13",
    )
    result = identity.defer_exit(capture)
    assert result.action == "unassigned"
    saved = UnassignedWeighing.objects.get(pk=result.unassigned_id)
    assert saved.photo_request_id == capture.idempotency_key
    assert identity.defer_exit(capture).unassigned_id == result.unassigned_id
    capture.orientation = "front"
    assert identity.defer_exit(capture).unassigned_id == result.unassigned_id
    capture.vehicle_number = ""
    capture.plate_unresolved = True
    assert identity.defer_exit(capture).unassigned_id == result.unassigned_id


def test_api_request_has_one_image_no_database_answers_or_tools():
    item = parked()
    expected = {"exit": {"plate": "149ABC13", "plate_clear": True, "orientation": "rear"}}
    payload = {
        "status": "completed",
        "id": "resp-test",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": json.dumps(expected)}],
            }
        ],
    }
    conn = MagicMock()
    conn.getresponse.return_value.status = 200
    conn.getresponse.return_value.read.return_value = json.dumps(payload).encode()
    with patch.object(openai_responses.http.client, "HTTPSConnection", return_value=conn):
        assert identity.request_verification(item) == (expected, "resp-test")
    body = json.loads(conn.request.call_args.kwargs["body"])
    assert body["store"] is False and "tools" not in body
    assert "149ABC13" not in json.dumps(body)
    assert len(body["input"][0]["content"]) == 2
    assert body["text"]["format"]["strict"] is True


def test_http_failure_retries_are_bounded():
    item = parked(number="")
    with patch.object(
        identity,
        "request_verification",
        side_effect=http.client.HTTPException("private"),
    ) as request:
        for _ in range(identity.MAX_ATTEMPTS + 2):
            identity.process_once()
            WeighingIdentityCheck.objects.filter(weighing=item).update(
                next_attempt_at=timezone.now() - timedelta(seconds=1)
            )
    assert request.call_count == identity.MAX_ATTEMPTS
    check = WeighingIdentityCheck.objects.get(weighing=item)
    assert check.status == "review" and "private" not in json.dumps(check.evidence)
    item.refresh_from_db()
    assert item.status == "open"


def test_lease_prevents_duplicate_claims():
    parked(number="")
    assert identity._claim() is not None
    assert identity._claim() is None


def test_budget_exhaustion_is_visible_and_retries_next_local_day(settings):
    settings.WEIGHING_AI_MAX_DAILY_REQUESTS = 1
    parked(number="")
    with patch.object(
        identity, "request_verification", return_value=(unreadable(), "resp-test")
    ) as request:
        identity.process_once()
        another = parked(number="")
        identity.process_once()
    request.assert_called_once()
    check = WeighingIdentityCheck.objects.get(weighing=another)
    assert check.reason == "daily_budget_exhausted" and check.attempts == 0
    midnight = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    assert check.next_attempt_at == midnight + timedelta(days=1)
    assert identity.public_status(another)["status"] == "waiting_budget"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("KZ 411 BBF 13", "411BBF13"),
        ("KZ854ANB13", "854ANB13"),
        ("X 315 FPM", "X315FPM"),
        ("449-ABC-13", "449ABC13"),
        ("KZ 411 ВВF 13", "411BBF13"),
        ("KZ 449 A?C 13", ""),
        ("449ABC13 OR 149ABC13", ""),
        ("9449ABC13", ""),
    ],
)
def test_normalizes_only_plate_layout_not_unclear_characters(raw, expected):
    assert identity.normalized_plate(raw) == expected


def test_expired_or_missing_photos_are_not_reported_as_active_vision():
    item = parked(ago=timedelta(hours=25))
    assert identity.public_status(item)["status"] == "review"
    item.stable_weight_at = timezone.now()
    item.photo = None
    assert identity.public_status(item)["status"] == "waiting_photo"
    item.photo_request_id = None
    assert identity.public_status(item)["status"] == "review"
