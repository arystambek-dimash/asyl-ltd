import time
import sqlite3
from decimal import Decimal
from concurrent.futures import Future
from io import BytesIO
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.utils import timezone

from apps.cameras import ai
from apps.grain import outbox_importer, weighing_photos
from apps.grain.models import WeighingPhotoDelivery
from apps.grain.scale import ScaleObservation
from weighbridge.collector import Collector
from weighbridge.outbox import Outbox

JPEG = b"\xff\xd8\xff\xe0original capture"


@pytest.fixture(autouse=True)
def config(settings):
    settings.GO2RTC_API_URL = "http://relay.example.test:1984"


def event():
    return {
        "id": str(uuid4()), "version": 1, "weight_kg": 4200,
        "camera": "cam1", "stable_weight_at": timezone.now().isoformat(),
        "scale_age_seconds": "0.1", "scale_updated_at": "sample",
    }


def collector_event(tmp_path):
    box, value = Outbox(tmp_path), event()
    box.put(value)
    collector = Collector(box)
    collector.current = value["id"]
    collector.last_good = time.monotonic()
    return collector, box, value


def test_snapshot_retries_transient_relay_failure_for_same_vehicle(tmp_path):
    collector, box, value = collector_event(tmp_path)
    try:
        with patch("apps.grain.scale._open_request", side_effect=[TimeoutError(), BytesIO(JPEG)]) as read:
            collector.snapshot(value)
        box.finish(value["id"], "ocr")
        assert read.call_count == 2
        assert box.next()["photo"] == JPEG
    finally:
        collector.pool.shutdown()


def test_snapshot_cannot_retry_or_accept_after_vehicle_departure(tmp_path):
    collector, box, value = collector_event(tmp_path)

    def leaving(*args, **kwargs):
        collector.current = None
        return BytesIO(JPEG)

    try:
        with patch("apps.grain.scale._open_request", side_effect=leaving) as read:
            collector.snapshot(value)
        box.finish(value["id"], "ocr")
        assert read.call_count == 1
        assert box.next()["photo"] is None
    finally:
        collector.pool.shutdown()


@pytest.mark.parametrize("previous_photo", [False, True])
def test_slow_previous_ocr_never_skips_next_trucks_photo(tmp_path, previous_photo):
    collector, box, value = collector_event(tmp_path)
    collector.futures["ocr"] = Future()  # prior truck's request is still blocked
    if previous_photo:
        collector.futures["photo:previous"] = Future()
    try:
        with patch("apps.grain.scale._open_request", return_value=BytesIO(JPEG)):
            collector.start_evidence(value)
            collector.futures[f"photo:{value['id']}"].result(timeout=2)
        assert box.next()["photo"] == JPEG
        assert box.next()["recognition_error"] == "evidence_worker_busy"
    finally:
        collector.pool.shutdown()


@pytest.mark.parametrize("weight", [4200, 0])
def test_fresh_moving_truck_retains_photo_episode_until_scale_clears(tmp_path, weight):
    collector, box, value = collector_event(tmp_path)
    moving = ScaleObservation("unstable", Decimal(weight), True, False, False, Decimal(".1"), "fresh")
    try:
        with patch("apps.grain.scale.read_truck_scale_observation", return_value=moving):
            collector.poll()
        assert collector.current == (value["id"] if weight else None)
    finally:
        collector.pool.shutdown()


@pytest.mark.parametrize("departed", [False, True])
def test_failed_ocr_frame_is_bound_only_if_response_precedes_departure(tmp_path, departed):
    collector, box, value = collector_event(tmp_path)

    def recognition(*args, **kwargs):
        if departed:
            collector.current = None
        raise ai.AiError(422, "unreadable", {"orientation": {"label": "rear", "confidence": .9}})

    try:
        with patch.object(ai, "recognize_vehicle_from_camera", side_effect=recognition):
            collector.recognize(value)
        box.finish(value["id"], "photo")
        assert box.next()["recognition_frame_bound"] is (not departed)
        assert outbox_importer._bound_camera_frame(box.next()) is (not departed)
    finally:
        collector.pool.shutdown()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("safe", [True, False])
def test_old_acknowledged_capture_recovers_only_proven_bound_uuid(tmp_path, settings, safe):
    settings.MEDIA_ROOT = tmp_path / "media"
    box, value = Outbox(tmp_path), event()
    box.put(value)
    box.finish(value["id"], "photo")
    box.finish(value["id"], "ocr", updates={
        "orientation": "rear" if safe else "",
        "recognition_error": "recognition_unavailable",
    })
    box.ack(value["id"])
    job = WeighingPhotoDelivery.objects.create(
        request_id=value["id"], camera="cam1", status="unavailable",
        snapshot_attempted=True, error_code="collector_photo_unavailable",
    )
    assert outbox_importer.recover_collector_photos(box) == int(safe)
    job.refresh_from_db()
    assert job.status == ("pending" if safe else "unavailable")
    with patch.object(ai, "camera_frame_jpeg") as live, patch.object(
        ai, "fetch_vehicle_recognition_frame", return_value=JPEG,
    ) as stored:
        assert weighing_photos.deliver_photo(job.pk) is safe
    live.assert_not_called()
    if safe:
        stored.assert_called_once_with("cam1", value["id"])
        job.refresh_from_db()
        assert job.photo.read() == JPEG
    else:
        stored.assert_not_called()
    assert box.counts()["pending"] == 0  # accounting event is never replayed


@pytest.mark.django_db
def test_absent_uuid_frame_becomes_terminal_instead_of_waiting_forever():
    job = WeighingPhotoDelivery.objects.create(
        request_id=uuid4(), camera="cam1", snapshot_attempted=True,
    )
    with patch.object(ai, "camera_frame_jpeg") as live, patch.object(
        ai, "fetch_vehicle_recognition_frame", return_value=None,
    ):
        for _ in range(3):
            WeighingPhotoDelivery.objects.filter(pk=job.pk).update(next_attempt_at=timezone.now())
            assert not weighing_photos.deliver_photo(job.pk)
    job.refresh_from_db()
    assert job.status == "unavailable"
    assert job.error_code == "frame_not_available"
    live.assert_not_called()


def test_outbox_existing_schema_is_not_rewritten_by_importer_or_healthcheck(tmp_path):
    box = Outbox(tmp_path)
    statements = []
    real_connect = sqlite3.connect

    def traced(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    with patch("weighbridge.outbox.sqlite3.connect", side_effect=traced):
        Outbox(tmp_path).counts()
    assert not any("CREATE " in sql or "journal_mode=" in sql for sql in statements)
    with box.connect() as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert db.execute("PRAGMA synchronous").fetchone()[0] == 2  # FULL remains required


def test_outbox_retries_schema_lock_when_setting_full_durability(tmp_path):
    failures = []

    class FirstPragmaBusy(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if sql == "PRAGMA synchronous=FULL" and not failures:
                failures.append(sql)
                raise sqlite3.OperationalError("database is locked")
            return super().execute(sql, *args, **kwargs)

    real_connect = sqlite3.connect
    with patch("weighbridge.outbox.sqlite3.connect", side_effect=lambda *args, **kwargs: real_connect(*args, **kwargs, factory=FirstPragmaBusy)):
        box = Outbox(tmp_path)
        box.put(event())
    assert failures
    assert box.counts()["total"] == 1


def test_database_lock_keeps_existing_occupancy_instead_of_restarting(tmp_path):
    collector, box, value = collector_event(tmp_path)
    collector.lane.armed = False
    try:
        with patch.object(box, "state", side_effect=sqlite3.OperationalError("database is locked")):
            collector.poll()
        assert collector.current == value["id"]
        assert not collector.lane.armed
    finally:
        collector.pool.shutdown()
