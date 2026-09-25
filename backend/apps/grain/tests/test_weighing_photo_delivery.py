from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.utils import timezone

from apps.cameras import ai as camera_ai
from apps.grain import services, weighing_photos as photos
from apps.grain.models import (
    AutomaticPassageCapture as Capture,
    UnassignedWeighing,
    Wagon,
    WeighingPhotoDelivery,
    WeighingRecord,
)
from apps.grain.tests.factories import JPEG

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def config(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    with patch.object(camera_ai, "fetch_vehicle_recognition_frame", return_value=None):
        yield


def saved_capture(**overrides):
    values = dict(
        idempotency_key=uuid4(),
        camera="cam1",
        weight_kg=4000,
        trigger_weight_kg=4000,
        stable_weight_at=timezone.now(),
        scale_age_seconds=Decimal("0.1"),
        stage=Capture.RECOGNIZING,
    )
    values.update(overrides)
    return Capture.objects.create(**values)


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


def test_history_requires_grain_view_and_uses_signed_photos(
    auth_client, user_with_perms
):
    capture = saved_capture()
    job = WeighingPhotoDelivery.objects.create(
        request_id=capture.idempotency_key, camera="cam1", capture=capture
    )
    with patch.object(camera_ai, "fetch_vehicle_recognition_frame", return_value=JPEG):
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


def test_history_photo_status_comes_from_the_latest_delivery(
    auth_client, user_with_perms
):
    capture = saved_capture()
    # Физический порядок строк не совпадает с порядком id: без явной
    # сортировки prefetch вернул бы старую доставку последней.
    WeighingPhotoDelivery.objects.create(
        id=900002, request_id=uuid4(), camera="cam1", capture=capture, status="retrying"
    )
    WeighingPhotoDelivery.objects.create(
        id=900001, request_id=uuid4(), camera="cam1", capture=capture, status="unavailable"
    )
    response = auth_client(user_with_perms("viewer", ["grain.view"])).get(
        "/api/grain/automatic-passage-scale/history/"
    )
    assert response.status_code == 200
    assert response.data["results"][0]["photo_status"] == "retrying"


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

