from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Event
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.db import close_old_connections
from django.utils import timezone

from apps.cameras import ai as camera_ai
from apps.grain import (
    passage_monitor as monitor,
    passage_scale_automation as automation,
    scale,
    services,
    weighing_photos as photos,
)
from apps.grain.models import (
    AutomaticPassageCapture as Capture,
    PassageScaleAutomationState as Lane,
    UnassignedWeighing,
    Wagon,
    WeighingPhotoDelivery,
    WeighingRecord,
)

pytestmark = pytest.mark.django_db(transaction=True)
JPEG = b"\xff\xd8\xff\xe0" + b"test" * 20


@pytest.fixture(autouse=True)
def config(settings, tmp_path):
    Lane.objects.all().delete()
    settings.MEDIA_ROOT = tmp_path
    settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED = True
    settings.VEHICLE_PLATE_WEIGHT_FIRST_CAMERA = "cam1"
    settings.VEHICLE_PLATE_WEIGHT_FIRST_SOURCE = "main"
    settings.VEHICLE_PLATE_AUTO_SCALE_CLEAR_CONFIRM_POLLS = 2
    settings.VEHICLE_PLATE_AUTO_SCALE_EMPTY_MAX_KG = 500
    with patch.object(camera_ai, "camera_frame_jpeg", return_value=None), patch.object(
        camera_ai, "fetch_vehicle_recognition_frame", return_value=None
    ):
        yield


def observation(weight):
    return scale.ScaleObservation(
        state="ready",
        weight_kg=Decimal(weight),
        connected=True,
        stable=True,
        stale=False,
        age_seconds=Decimal("0.1"),
        updated_at="2026-09-08T08:00:00Z",
    )


def tick(weight, at):
    with patch.object(
        scale, "read_truck_scale_observation", return_value=observation(weight)
    ), patch.object(
        scale,
        "read_truck_scale",
        return_value=scale.ScaleReading(
            Decimal(weight), Decimal("0.1"), "2026-09-08T08:00:00Z"
        ),
    ):
        return monitor.poll_once(now=at)


def saved_capture(**overrides):
    values = dict(
        idempotency_key=uuid4(),
        camera="cam1",
        weight_kg=4000,
        trigger_weight_kg=4000,
        stable_weight_at=timezone.now(),
        scale_age_seconds=Decimal("0.1"),
        stage=Capture.RECOGNIZING,
        recognition_dispatched=False,
    )
    values.update(overrides)
    capture = Capture.objects.create(**values)
    Lane.objects.create(
        current_capture=capture, phase=Lane.PROCESSING, stable_weight_seconds=2
    )
    return capture


def recognized(key, stable_at, at):
    return dict(
        ok=True,
        status="recognized",
        request_id=str(key),
        camera="cam1",
        source="main",
        stable_weight_at=stable_at,
        recognized_at=at.isoformat(),
        vehicle_number="934PPB13",
        confirmation=dict(votes=3, detector_confidence=0.9, ocr_confidence=0.9),
        orientation={"label": "front", "confidence": 0.99},
    )


def test_next_equal_weight_truck_is_saved_while_previous_ocr_is_blocked():
    first = saved_capture()
    started, release = Event(), Event()
    at = timezone.now()

    def slow_camera(_camera, key, stable_at):
        started.set()
        assert release.wait(5)
        return recognized(key, stable_at, at)

    def work():
        close_old_connections()
        try:
            monitor.process_once()
        finally:
            close_old_connections()

    with patch.object(
        camera_ai, "recognize_vehicle_from_camera", side_effect=slow_camera
    ), ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(work)
        try:
            assert started.wait(3)
            tick(0, at + timedelta(seconds=1))
            tick(0, at + timedelta(seconds=2))
            tick(4000, at + timedelta(seconds=3))
            tick(4000, at + timedelta(seconds=6))
            second = Capture.objects.exclude(pk=first.pk).get()
            assert second.weight_kg == 4000
            assert second.status == Capture.PROCESSING
            assert Lane.objects.get().current_capture_id == second.pk
        finally:
            release.set()
        task.result(timeout=5)
    first.refresh_from_db()
    assert first.status == Capture.COMPLETED
    assert first.cleared_at is not None
    assert Lane.objects.get().current_capture_id == second.pk


def test_departure_fences_fresh_ocr_even_if_next_truck_has_the_same_weight():
    capture = saved_capture(
        recognition_dispatched=True,
        needs_new_attempt=True,
        retryable=True,
        recognition_attempts=1,
    )
    at = timezone.now()
    Capture.objects.filter(pk=capture.pk).update(updated_at=at - timedelta(seconds=5))
    tick(0, at)
    tick(0, at + timedelta(seconds=1))
    Capture.objects.filter(pk=capture.pk).update(
        updated_at=timezone.now() - timedelta(seconds=5)
    )
    with patch.object(
        camera_ai, "recognize_vehicle_from_camera"
    ) as camera, patch.object(scale, "read_truck_scale") as read:
        monitor.process_once()
    camera.assert_not_called()
    read.assert_not_called()
    assert UnassignedWeighing.objects.get().weight_kg == 4000


def test_late_camera_result_cannot_assign_a_plate_after_departure():
    capture = saved_capture(departure_observed_at=timezone.now() - timedelta(seconds=1))
    automation._persist_recognition(
        capture.pk,
        recognized(
            capture.idempotency_key,
            capture.stable_weight_at.isoformat(),
            timezone.now(),
        ),
    )
    capture.refresh_from_db()
    assert capture.plate_unresolved
    assert capture.vehicle_plate_event_id is None


def test_restart_preserves_saved_weight_but_fences_new_camera_requests():
    capture = saved_capture()
    monitor.prepare_start()
    with patch.object(camera_ai, "recognize_vehicle_from_camera") as recognize:
        monitor.process_once()
    recognize.assert_not_called()
    assert UnassignedWeighing.objects.get().weight_kg == capture.weight_kg
    assert Lane.objects.get().phase == Lane.UNARMED


def test_unstable_departure_appears_in_history_without_inventing_a_weight():
    Lane.objects.create(phase=Lane.ARMED, stable_weight_seconds=10)
    at = timezone.now()
    tick(4000, at)
    tick(0, at + timedelta(seconds=3))
    capture = Capture.objects.get()
    assert capture.error_code == "automatic_scale_not_stable"
    assert capture.weight_kg is None
    assert not Wagon.objects.exists()


def test_disabled_automation_preserves_detached_pending_weight(settings):
    capture = saved_capture()
    monitor.prepare_start()
    settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED = False
    monitor.poll_once()
    assert UnassignedWeighing.objects.get().capture_id == capture.pk
    assert not Capture.objects.filter(status=Capture.PROCESSING).exists()


def test_photo_download_retries_after_assignment_without_changing_the_weight():
    key = uuid4()
    item = UnassignedWeighing.objects.create(
        weight_kg=8800,
        stable_weight_at=timezone.now(),
        camera="cam1",
        photo_request_id=key,
    )
    assert not photos.attach_photo("cam1", key)
    job = WeighingPhotoDelivery.objects.get()
    assert job.status == "retrying"
    wagon = Wagon.objects.create(
        number="934PPB13",
        direction="passage",
        workflow="simple",
        status="at_silo",
        gross_weight_kg=3900,
        arrived_at=timezone.now() - timedelta(hours=1),
    )
    services.assign_unassigned_weighing(item, wagon, None)
    WeighingPhotoDelivery.objects.filter(pk=job.pk).update(
        next_attempt_at=timezone.now()
    )
    with patch.object(camera_ai, "fetch_vehicle_recognition_frame", return_value=JPEG):
        photos.retry_due_photos()
    record = WeighingRecord.objects.get(kind="tare")
    assert record.weight_kg == 8800
    assert record.photo.read() == JPEG
    wagon.refresh_from_db()
    assert wagon.net_weight_kg == 4900


def test_snapshot_is_saved_before_ocr_or_trip_exists():
    capture = saved_capture()
    job = photos.queue_photo("cam1", capture.idempotency_key, capture=capture)
    with patch.object(
        camera_ai, "camera_frame_jpeg", return_value=JPEG
    ) as snapshot, patch.object(camera_ai, "fetch_vehicle_recognition_frame") as fetch:
        assert photos.deliver_photo(job.pk)
    snapshot.assert_called_once_with("cam1main")
    fetch.assert_not_called()
    job.refresh_from_db()
    assert job.photo.read() == JPEG
    assert not Wagon.objects.exists()


def test_snapshot_crossing_departure_is_discarded():
    capture = saved_capture()
    job = photos.queue_photo("cam1", capture.idempotency_key, capture=capture)

    def snapshot(_stream):
        Capture.objects.filter(pk=capture.pk).update(
            departure_observed_at=timezone.now()
        )
        return JPEG

    with patch.object(
        camera_ai, "camera_frame_jpeg", side_effect=snapshot
    ), patch.object(camera_ai, "fetch_vehicle_recognition_frame", return_value=JPEG):
        assert not photos.deliver_photo(job.pk)
    job.refresh_from_db()
    assert not job.photo


def test_old_photo_retry_never_takes_a_new_live_snapshot():
    capture = saved_capture(stable_weight_at=timezone.now() - timedelta(minutes=1))
    job = photos.queue_photo("cam1", capture.idempotency_key, capture=capture)
    with patch.object(camera_ai, "camera_frame_jpeg") as snapshot, patch.object(
        camera_ai, "fetch_vehicle_recognition_frame", return_value=JPEG
    ):
        assert photos.deliver_photo(job.pk)
    snapshot.assert_not_called()


def test_photo_is_linked_when_weighing_is_created_after_delivery():
    key = uuid4()
    with patch.object(camera_ai, "fetch_vehicle_recognition_frame", return_value=JPEG):
        assert photos.attach_photo("cam1", key)
    item = UnassignedWeighing.objects.create(
        weight_kg=8000,
        stable_weight_at=timezone.now(),
        camera="cam1",
        photo_request_id=key,
    )
    with patch.object(camera_ai, "fetch_vehicle_recognition_frame") as fetch:
        assert photos.attach_photo("cam1", key)
    fetch.assert_not_called()
    item.refresh_from_db()
    assert item.photo.read() == JPEG


def test_photo_lease_prevents_concurrent_downloads():
    job = photos.queue_photo("cam1", uuid4())
    assert photos._claim(job.pk, timezone.now()) is not None
    with patch.object(camera_ai, "fetch_vehicle_recognition_frame") as fetch:
        assert not photos.deliver_photo(job.pk)
    fetch.assert_not_called()


def test_terminal_processing_error_keeps_weight_assignable():
    capture = saved_capture(
        status=Capture.FAILED, error_code="vehicle_recognition_malformed"
    )
    monitor.process_once()
    monitor.process_once()
    item = UnassignedWeighing.objects.get()
    assert item.capture_id == capture.pk
    assert item.weight_kg == 4000
    assert item.reason == "vehicle_recognition_malformed"
    assert not Wagon.objects.exists()


def test_observation_outage_fences_camera_retries():
    capture = saved_capture(
        recognition_dispatched=True, needs_new_attempt=True, retryable=True
    )
    with patch.object(
        scale,
        "read_truck_scale_observation",
        return_value=scale.ScaleObservation(
            state="stale",
            weight_kg=None,
            connected=True,
            stable=False,
            stale=True,
            age_seconds=None,
            updated_at=None,
        ),
    ):
        monitor.poll_once()
    Capture.objects.filter(pk=capture.pk).update(
        updated_at=timezone.now() - timedelta(seconds=5)
    )
    with patch.object(camera_ai, "recognize_vehicle_from_camera") as camera:
        monitor.process_once()
    camera.assert_not_called()
    assert UnassignedWeighing.objects.get().weight_kg == 4000


def test_history_requires_grain_view_and_uses_signed_photos(
    auth_client, user_with_perms
):
    capture = saved_capture()
    job = photos.queue_photo("cam1", capture.idempotency_key, capture=capture)
    with patch.object(camera_ai, "camera_frame_jpeg", return_value=JPEG):
        photos.deliver_photo(job.pk)
    assert (
        auth_client(user_with_perms("denied", []))
        .get("/api/grain/automatic-passage-scale/history/")
        .status_code
        == 403
    )
    response = auth_client(user_with_perms("viewer", ["grain.view"])).get(
        "/api/grain/automatic-passage-scale/history/"
    )
    assert response.status_code == 200
    row = response.data["results"][0]
    assert row["weight_kg"] == 4000
    assert "token=" in row["photo_url"]
    assert row["photo_status"] == "saved"


def test_historical_photo_backfill_is_idempotent_and_keeps_capture_link():
    from importlib import import_module
    from types import SimpleNamespace
    from django.apps import apps
    from django.db import connection

    capture = saved_capture(status=Capture.COMPLETED)
    UnassignedWeighing.objects.create(
        capture=capture,
        weight_kg=4000,
        stable_weight_at=timezone.now(),
        camera="cam1",
        photo_request_id=capture.idempotency_key,
    )
    migration = import_module(
        "apps.grain.migrations.0015_queue_missing_weighing_photos"
    )
    with patch.object(camera_ai, "fetch_vehicle_recognition_frame") as fetch:
        migration.queue_existing(apps, SimpleNamespace(connection=connection))
        migration.queue_existing(apps, SimpleNamespace(connection=connection))
    fetch.assert_not_called()
    job = WeighingPhotoDelivery.objects.get()
    assert job.capture_id == capture.pk
    assert job.snapshot_attempted
    assert job.status == "pending"
