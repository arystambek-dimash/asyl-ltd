import copy
import http.client
import json
from datetime import timedelta
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from django.core.files.base import ContentFile
from django.utils import timezone

from apps.grain import statuses as st, weighing_identity as identity
from apps.grain.models import (
    AutomaticPassageCapture,
    UnassignedWeighing,
    Wagon,
    WeighingIdentityCheck,
    WeighingRecord,
)

pytestmark = pytest.mark.django_db
JPEG = b"\xff\xd8\xff\xe0" + b"1" * 32


@pytest.fixture(autouse=True)
def config(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.OPENAI_API_KEY = "test-key"
    settings.WEIGHING_AI_ENABLED = True
    settings.VEHICLE_PLATE_AUTO_EXPORT_MIN_TRIP_SECONDS = 60
    settings.WEIGHING_AI_ENTRY_MAX_HOURS = 12
    settings.WEIGHING_AI_MAX_DAILY_REQUESTS = 200


def parked(*, number="149ABC13", orientation="rear", weight=8000, at=None):
    item = UnassignedWeighing.objects.create(
        vehicle_number=number,
        orientation=orientation,
        weight_kg=weight,
        stable_weight_at=at or timezone.now() - timedelta(seconds=1),
        scale_number="truck",
        camera="cam1",
        photo_request_id=uuid4(),
    )
    item.photo.save("frame.jpg", ContentFile(JPEG))
    return item


@pytest.fixture
def visit():
    at = timezone.now() - timedelta(minutes=30)
    wagon = Wagon.objects.create(
        number="449ABC13",
        direction=Wagon.PASSAGE,
        workflow="simple",
        cargo_name="Отруби",
        status=st.AT_SILO,
        gross_weight_kg=4000,
        arrived_at=at,
        silo_arrived_at=at,
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


def verdict(entries, plate="449ABC13"):
    return {
        "exit": {"plate": plate, "plate_clear": True, "orientation": "rear"},
        "entries": [
            {
                "key": row[0]["key"],
                "plate": plate,
                "plate_clear": True,
                "orientation": "front",
                "appearance": "same",
                "visual_evidence": "Совпадают повреждения кузова",
            }
            for row in entries
        ],
    }


def verify(item):
    result = verdict(identity.candidates(item))
    with patch.object(
        identity, "request_verification", return_value=(result, "resp-test")
    ) as request:
        identity.process_once()
    return request


def test_corrects_one_ocr_character_and_preserves_actual_weights(visit):
    wagon, entry, item = visit
    verify(item)
    identity.process_once()
    wagon.refresh_from_db()
    item.refresh_from_db()
    assert wagon.status == st.COMPLETED
    assert (wagon.gross_weight_kg, wagon.tare_weight_kg, wagon.net_weight_kg) == (
        4000,
        8000,
        4000,
    )
    assert item.vehicle_number == "149ABC13"
    assert item.status == "assigned" and item.wagon_id == wagon.pk
    assert wagon.weighings.get(kind="tare").photo.name == item.photo.name
    assert item.identity_check.status == "matched"
    assert item.identity_check.attempts == 1
    assert item.identity_check.evidence["entries"][0]["weight_kg"] == 4000


@pytest.mark.parametrize(
    "case",
    [
        "unclear",
        "appearance",
        "wrong_entry_plate",
        "two_matches",
        "front_exit",
        "foreign_key",
        "large_correction",
        "lower_weight",
    ],
)
def test_ambiguous_evidence_never_books_exit(visit, case):
    wagon, entry, item = visit
    result = verdict(identity.candidates(item))
    if case == "unclear":
        result["exit"]["plate_clear"] = False
    if case == "appearance":
        result["entries"][0]["appearance"] = "uncertain"
    if case == "wrong_entry_plate":
        result["entries"][0]["plate"] = "448ABC13"
    if case == "two_matches":
        result["entries"].append(copy.deepcopy(result["entries"][0]))
    if case == "front_exit":
        result["exit"]["orientation"] = "front"
    if case == "foreign_key":
        result["entries"][0]["key"] = "record:99999"
    if case == "large_correction":
        item.vehicle_number = "111ABC13"
        item.save()
    if case == "lower_weight":
        item.weight_kg = 3000
        item.save()
    with patch.object(
        identity, "request_verification", return_value=(result, "resp-test")
    ):
        identity.process_once()
    wagon.refresh_from_db()
    item.refresh_from_db()
    assert wagon.tare_weight_kg is None and item.status == "open"
    assert item.identity_check.status == (
        "retrying" if case == "large_correction" else "review"
    )


def test_recovers_saved_unassigned_entry_only_once():
    entry = parked(
        number="",
        orientation="front",
        weight=4000,
        at=timezone.now() - timedelta(minutes=20),
    )
    item = parked()
    verify(item)
    entry.refresh_from_db()
    item.refresh_from_db()
    assert entry.status == item.status == "assigned"
    assert entry.wagon_id == item.wagon_id
    wagon = item.wagon
    assert wagon.gross_weight_kg == 4000 and wagon.tare_weight_kg == 8000
    assert wagon.silo_arrived_at == entry.stable_weight_at
    assert wagon.weighings.get(kind="gross").photo.name == entry.photo.name
    second_exit = parked()
    with patch.object(identity, "request_verification") as request:
        identity.process_once()
    request.assert_not_called()
    second_exit.refresh_from_db()
    assert second_exit.status == "open"


@pytest.mark.parametrize(
    "case", ["completed", "stale", "different_camera", "manual", "no_photo"]
)
def test_ineligible_entry_not_sent_to_model(visit, case):
    wagon, entry, item = visit
    if case == "completed":
        wagon.status = st.COMPLETED
    if case == "stale":
        wagon.silo_arrived_at = timezone.now() - timedelta(days=2)
    if case == "different_camera":
        entry.photo_camera = "cam2"
    if case == "manual":
        entry.source = "manual"
    if case == "no_photo":
        entry.photo = None
    wagon.save()
    entry.save()
    with patch.object(identity, "request_verification") as request:
        identity.process_once()
    request.assert_not_called()
    assert item.wagon_id is None


@pytest.mark.parametrize("change", ["discard", "number"])
def test_manual_change_during_network_call_wins(visit, change):
    wagon, entry, item = visit

    def respond(*_):
        result = verdict(identity.candidates(item))
        if change == "discard":
            UnassignedWeighing.objects.filter(pk=item.pk).update(status="discarded")
        else:
            Wagon.objects.filter(pk=wagon.pk).update(number="999AAA13")
        return result, "resp-test"

    with patch.object(identity, "request_verification", side_effect=respond):
        identity.process_once()
    wagon.refresh_from_db()
    assert wagon.tare_weight_kg is None


def test_no_key_does_not_dispatch(visit, settings):
    settings.OPENAI_API_KEY = ""
    with patch.object(identity, "request_verification") as request:
        identity.process_once()
    request.assert_not_called()
    assert not WeighingIdentityCheck.objects.exists()


def test_http_failure_retries_are_bounded(visit):
    wagon, entry, item = visit
    with patch.object(
        identity,
        "request_verification",
        side_effect=http.client.HTTPException("private"),
    ) as request:
        for _ in range(5):
            identity.process_once()
            WeighingIdentityCheck.objects.filter(weighing=item).update(
                next_attempt_at=timezone.now() - timedelta(seconds=1)
            )
    assert request.call_count == 3
    check = WeighingIdentityCheck.objects.get(weighing=item)
    assert check.status == "review" and "private" not in json.dumps(check.evidence)
    wagon.refresh_from_db()
    assert wagon.tare_weight_kg is None


def test_daily_budget_and_lease_prevent_duplicate_calls(visit, settings):
    settings.WEIGHING_AI_MAX_DAILY_REQUESTS = 1
    assert identity._claim() is not None
    assert identity._claim() is None
    parked()
    assert identity._claim() is None


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
    assert identity.defer_exit(capture) is None


def test_api_request_has_only_images_no_database_answers_or_tools(visit):
    _, _, item = visit
    entries = identity.candidates(item)
    expected = verdict(entries)
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
    with patch.object(identity.http.client, "HTTPSConnection", return_value=conn):
        assert identity.request_verification(item, entries) == (expected, "resp-test")
    body = json.loads(conn.request.call_args.kwargs["body"])
    assert body["store"] is False and "tools" not in body
    assert "449ABC13" not in json.dumps(body) and "149ABC13" not in json.dumps(body)
    assert len(body["input"][0]["content"]) == 4
    assert body["text"]["format"]["strict"] is True


def test_entry_photo_arriving_later_is_retried_without_losing_weight(visit):
    wagon, entry, item = visit
    photo = entry.photo.name
    entry.photo = None
    entry.save()
    with patch.object(identity, "request_verification") as request:
        identity.process_once()
    request.assert_not_called()
    assert item.identity_check.status == "retrying"
    entry.photo = photo
    entry.save()
    WeighingIdentityCheck.objects.filter(weighing=item).update(
        next_attempt_at=timezone.now()
    )
    verify(item)
    wagon.refresh_from_db()
    assert wagon.status == st.COMPLETED


def test_unrelated_plates_do_not_fill_image_budget(visit):
    _, entry, item = visit
    for i in range(8):
        parked(
            number=f"{200 + i}BBB13",
            orientation="front",
            weight=4000,
            at=timezone.now() - timedelta(minutes=15),
        )
    assert [row[0]["key"] for row in identity.candidates(item)] == [
        f"record:{entry.pk}"
    ]


def test_other_candidate_changed_during_check_prevents_booking(visit):
    wagon, _, item = visit
    other = parked(
        number="",
        orientation="front",
        weight=4500,
        at=timezone.now() - timedelta(minutes=15),
    )

    def respond(*_):
        result = verdict(identity.candidates(item))
        for row in result["entries"]:
            if row["key"] == f"unassigned:{other.pk}":
                row["plate"] = "999AAA13"
                row["appearance"] = "different"
        UnassignedWeighing.objects.filter(pk=other.pk).update(vehicle_number="449ABC13")
        return result, "resp-test"

    with patch.object(identity, "request_verification", side_effect=respond):
        identity.process_once()
    wagon.refresh_from_db()
    assert wagon.tare_weight_kg is None


def test_orphan_entry_before_another_completed_visit_is_not_reused(visit):
    wagon, _, item = visit
    Wagon.objects.create(
        number=wagon.number,
        direction=Wagon.PASSAGE,
        status=st.COMPLETED,
        exited_at=timezone.now() - timedelta(minutes=10),
    )
    verify(item)
    wagon.refresh_from_db()
    assert wagon.tare_weight_kg is None
    assert item.identity_check.status == "review"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("KZ 411 BBF 13", "411BBF13"),
        ("KZ854ANB13", "854ANB13"),
        ("X 315 FPM", "X315FPM"),
        ("449-ABC-13", "449ABC13"),
        ("KZ 449 A?C 13", ""),
        ("449ABC13 OR 149ABC13", ""),
        ("9449ABC13", ""),
    ],
)
def test_normalizes_only_plate_layout_not_unclear_characters(raw, expected):
    assert identity.normalized_plate(raw) == expected


def test_country_emblem_does_not_block_a_clear_unique_match(visit):
    _, _, item = visit
    entries = identity.candidates(item)
    result = verdict(entries)
    result["exit"]["plate"] = "KZ 449 ABC 13"
    result["entries"][0]["plate"] = "449-ABC-13"
    assert identity.choose(result, entries, item.vehicle_number)[1] == "449ABC13"


def test_budget_exhaustion_is_visible_and_retries_next_day(visit, settings):
    settings.WEIGHING_AI_MAX_DAILY_REQUESTS = 1
    verify(visit[2])
    another = parked()
    with patch.object(identity, "request_verification") as request:
        identity.process_once()
    request.assert_not_called()
    check = WeighingIdentityCheck.objects.get(weighing=another)
    assert check.reason == "daily_budget_exhausted" and check.attempts == 0
    assert check.next_attempt_at > timezone.now()
    assert identity.public_status(another)["status"] == "waiting_budget"


def test_expired_or_missing_photos_are_not_reported_as_active_vision():
    item = parked(at=timezone.now() - timedelta(hours=25))
    assert identity.public_status(item)["status"] == "review"
    item.stable_weight_at = timezone.now()
    item.photo = None
    assert identity.public_status(item)["status"] == "waiting_photo"
    item.photo_request_id = None
    assert identity.public_status(item)["status"] == "review"


@pytest.mark.parametrize("fresh_clear", [True, False])
def test_old_format_rejection_gets_one_fresh_check_without_resetting_attempts(
    visit, fresh_clear
):
    wagon, _, item = visit
    entries = identity.candidates(item)
    old = verdict(entries, plate="KZ 449 ABC 13")
    check = WeighingIdentityCheck.objects.create(
        weighing=item,
        status="review",
        reason="identity_uncertain",
        attempts=1,
        evidence={"verdict": old, "entries": [row[0] for row in entries]},
    )
    fresh = verdict(entries)
    fresh["exit"]["plate_clear"] = fresh_clear
    with patch.object(
        identity, "request_verification", return_value=(fresh, "resp-new")
    ) as request:
        identity.process_once()
        identity.process_once()
    request.assert_called_once()
    check.refresh_from_db()
    wagon.refresh_from_db()
    assert check.attempts == 2
    assert check.status == ("matched" if fresh_clear else "review")
    assert bool(wagon.tare_weight_kg) == fresh_clear


def test_format_retry_leaves_manually_resolved_weights_alone(visit):
    _, _, item = visit
    entries = identity.candidates(item)
    item.status = "discarded"
    item.save()
    check = WeighingIdentityCheck.objects.create(
        weighing=item,
        status="review",
        reason="identity_uncertain",
        attempts=1,
        evidence={
            "verdict": verdict(entries, plate="KZ 449 ABC 13"),
            "entries": [row[0] for row in entries],
        },
    )
    with patch.object(identity, "request_verification") as request:
        identity.process_once()
    request.assert_not_called()
    check.refresh_from_db()
    assert check.status == "review" and check.attempts == 1
