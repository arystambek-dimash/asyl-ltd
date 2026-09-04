import uuid
from datetime import timedelta
from decimal import Decimal
from importlib import import_module
from threading import Event, Thread
from unittest.mock import patch

import pytest
from apps.cameras.models import VehiclePlateEvent
from apps.grain import scale, services
from apps.grain import statuses as st
from apps.grain.models import Wagon, WeighingRecord
from apps.grain.serializers import WagonSerializer
from django.core.cache import cache
from django.db import IntegrityError, OperationalError, close_old_connections, connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

pytestmark = pytest.mark.django_db

WEBHOOK_URL = "/api/integrations/vehicle-plate-events"
WEBHOOK_TOKEN = "auto-export-webhook-token-long-enough"


@pytest.fixture(autouse=True)
def auto_export_settings(settings):
    settings.VEHICLE_PLATE_AUTO_EXPORT_ENABLED = True
    settings.VEHICLE_PLATE_AUTO_EXPORT_CARGO_NAME = "Отруби"
    settings.VEHICLE_PLATE_AUTO_EXPORT_EVENT_MAX_AGE_SECONDS = 15
    settings.VEHICLE_PLATE_AUTO_EXPORT_MIN_TRIP_SECONDS = 60
    settings.TRUCK_SCALE_TIMEOUT_SECONDS = 3


def event(*, number="123ABC02", detected_at=None, camera="cam1", source="main"):
    return VehiclePlateEvent.objects.create(
        event_id=uuid.uuid4(),
        vehicle_number=number,
        camera=camera,
        source=source,
        detected_at=detected_at or timezone.now() - timedelta(seconds=3),
        stationary_seconds=Decimal("3.400"),
        confirmation_votes=3,
        detector_confidence=Decimal("0.9100"),
        ocr_confidence=Decimal("0.9600"),
        payload_json={},
    )


def reading(weight: str) -> scale.ScaleReading:
    return scale.ScaleReading(
        weight_kg=Decimal(weight),
        age_seconds=Decimal("0.2"),
        updated_at="2026-08-26T10:00:00Z",
    )


def process(plate_event, weight="12000"):
    with patch.object(
        scale,
        "read_truck_scale",
        return_value=reading(weight),
    ) as read_scale:
        result = services.process_vehicle_plate_event(
            plate_event.pk,
            allow_capture=True,
        )
    read_scale.assert_called_once_with(scale.TRUCK_SCALE_KEY)
    return result


def open_automatic_passage(*, number="123ABC02", entry_weight="12000"):
    entry = event(number=number)
    result = process(entry, entry_weight)
    wagon = Wagon.objects.get(pk=result.wagon_id)
    old_arrival = timezone.now() - timedelta(minutes=2)
    Wagon.objects.filter(pk=wagon.pk).update(arrived_at=old_arrival)
    wagon.refresh_from_db()
    return entry, wagon


def test_first_fresh_event_creates_and_weighs_camera_passage_atomically():
    detected_at = timezone.now() - timedelta(seconds=3)
    plate_event = event(detected_at=detected_at)

    result = process(plate_event)

    wagon = Wagon.objects.get(pk=result.wagon_id)
    plate_event.refresh_from_db()
    assert result.status == "processed"
    assert result.action == "entry"
    assert result.weight_kg == 12_000
    assert wagon.number == "123ABC02"
    assert wagon.direction == Wagon.PASSAGE
    assert wagon.status == st.AT_SILO
    assert wagon.arrived_at == detected_at
    assert wagon.entry_weight_kg == 12_000
    assert wagon.exit_weight_kg is None
    assert wagon.vehicle_plate_event == plate_event
    assert wagon.number_camera_source == "cam1"
    assert plate_event.processing_status == VehiclePlateEvent.PROCESSED
    assert plate_event.processing_action == "entry"
    assert plate_event.processing_attempts == 1
    assert plate_event.processed_at is not None
    assert WeighingRecord.objects.get(wagon=wagon).source == "scale"


def test_duplicate_processed_uuid_never_reads_scale_or_creates_second_trip():
    plate_event = event()
    first = process(plate_event)

    with patch.object(scale, "read_truck_scale") as read_scale:
        duplicate = services.process_vehicle_plate_event(plate_event.pk)

    assert duplicate.status == "already_processed"
    assert duplicate.wagon_id == first.wagon_id
    read_scale.assert_not_called()
    assert Wagon.objects.count() == 1
    assert WeighingRecord.objects.count() == 1


def test_second_distinct_fresh_event_completes_same_plate_with_exit_weight():
    _entry, wagon = open_automatic_passage()
    exit_detected_at = timezone.now() - timedelta(seconds=2)
    exit_event = event(detected_at=exit_detected_at)

    result = process(exit_event, "30000")

    wagon.refresh_from_db()
    exit_event.refresh_from_db()
    assert result.action == "exit"
    assert result.wagon_id == wagon.pk
    assert wagon.status == st.COMPLETED
    assert wagon.exit_weight_kg == 30_000
    assert wagon.net_weight_kg == 18_000
    assert wagon.exit_vehicle_plate_event == exit_event
    assert wagon.exited_at == exit_detected_at
    assert wagon.unloading_finished_at == exit_detected_at
    assert exit_event.processing_status == VehiclePlateEvent.PROCESSED
    assert exit_event.processing_action == "exit"
    assert wagon.weighings.count() == 2


def test_scale_failure_is_one_shot_manual_and_duplicate_never_reads_again():
    plate_event = event()
    with patch.object(
        scale,
        "read_truck_scale",
        side_effect=scale.TruckScaleNotReady(),
    ) as read_scale:
        failed_attempt = services.process_vehicle_plate_event(
            plate_event.pk,
            allow_capture=True,
        )

    read_scale.assert_called_once_with(scale.TRUCK_SCALE_KEY)
    plate_event.refresh_from_db()
    assert failed_attempt.retryable is False
    assert failed_attempt.status == "manual_required"
    assert plate_event.processing_status == VehiclePlateEvent.FAILED
    assert plate_event.processing_error == "truck_scale_not_ready"
    assert not Wagon.objects.exists()

    with patch.object(scale, "read_truck_scale") as read_scale:
        duplicate = services.process_vehicle_plate_event(
            plate_event.pk,
            allow_capture=False,
        )
    plate_event.refresh_from_db()
    assert duplicate.status == "manual_required"
    assert duplicate.error == "truck_scale_not_ready"
    assert plate_event.processing_attempts == 1
    read_scale.assert_not_called()
    assert not Wagon.objects.exists()


def test_stale_event_fails_without_reading_scale_or_changing_trips():
    plate_event = event(detected_at=timezone.now() - timedelta(seconds=16))

    with patch.object(scale, "read_truck_scale") as read_scale:
        result = services.process_vehicle_plate_event(
            plate_event.pk,
            allow_capture=True,
        )

    plate_event.refresh_from_db()
    assert result.status == "manual_required"
    assert result.error == "event_stale"
    assert plate_event.processing_status == VehiclePlateEvent.FAILED
    read_scale.assert_not_called()
    assert not Wagon.objects.exists()


def test_wrong_lane_is_store_only_and_never_reads_truck_scale():
    plate_event = event(camera="cam2")

    with patch.object(scale, "read_truck_scale") as read_scale:
        result = services.process_vehicle_plate_event(
            plate_event.pk,
            allow_capture=True,
        )

    plate_event.refresh_from_db()
    assert result.status == "ignored"
    assert result.error == "wrong_lane"
    assert plate_event.processing_action == "ignored"
    read_scale.assert_not_called()
    assert not Wagon.objects.exists()


def test_manual_same_plate_arrived_passage_receives_the_automatic_entry():
    manual = Wagon.objects.create(
        number="123ABC02",
        direction=Wagon.PASSAGE,
        workflow="simple",
        cargo_name="Отруби",
        status=st.ARRIVED,
        number_source="manual",
        arrived_at=timezone.now(),
    )
    plate_event = event()

    with patch.object(scale, "read_truck_scale", return_value=reading("12000")):
        result = services.process_vehicle_plate_event(
            plate_event.pk,
            allow_capture=True,
        )

    manual.refresh_from_db()
    assert (result.status, result.action, result.wagon_id) == (
        "processed",
        "entry",
        manual.pk,
    )
    assert Wagon.objects.count() == 1
    assert manual.status == st.AT_SILO
    assert manual.entry_weight_kg == 12_000
    assert manual.number_source == "manual"
    assert manual.vehicle_plate_event_id == plate_event.pk


def test_blank_active_passage_does_not_block_a_new_automatic_entry():
    Wagon.objects.create(
        number="",
        direction=Wagon.PASSAGE,
        workflow="simple",
        cargo_name="Отруби",
        status=st.AT_SILO,
        number_source="camera",
        arrived_at=timezone.now(),
        gross_weight_kg=11_000,
    )
    plate_event = event()

    with patch.object(scale, "read_truck_scale", return_value=reading("12000")):
        result = services.process_vehicle_plate_event(
            plate_event.pk,
            allow_capture=True,
        )

    assert (result.status, result.action) == ("processed", "entry")
    assert Wagon.objects.count() == 2
    assert Wagon.objects.get(number="123ABC02").entry_weight_kg == 12_000


def test_second_event_before_minimum_gap_fails_closed_without_scale_read():
    entry = event()
    first = process(entry)
    exit_event = event(detected_at=timezone.now() - timedelta(seconds=1))

    with patch.object(scale, "read_truck_scale") as read_scale:
        result = services.process_vehicle_plate_event(
            exit_event.pk,
            allow_capture=True,
        )

    assert result.status == "manual_required"
    assert result.error == "entry_exit_too_close"
    read_scale.assert_not_called()
    wagon = Wagon.objects.get(pk=first.wagon_id)
    assert wagon.status == st.AT_SILO
    assert wagon.exit_weight_kg is None


def test_exit_not_heavier_than_entry_rolls_back_exit_link_and_weighing():
    _entry, wagon = open_automatic_passage(entry_weight="20000")
    exit_event = event()

    result = process(exit_event, "19000")

    wagon.refresh_from_db()
    exit_event.refresh_from_db()
    assert result.status == "manual_required"
    assert result.error == "exit_weight_not_greater"
    assert wagon.status == st.AT_SILO
    assert wagon.exit_vehicle_plate_event_id is None
    assert wagon.exit_weight_kg is None
    assert wagon.weighings.count() == 1
    assert exit_event.processing_status == VehiclePlateEvent.FAILED


def test_recent_completed_trip_blocks_ghost_third_entry():
    _entry, _wagon = open_automatic_passage()
    exit_event = event()
    process(exit_event, "30000")
    third = event(detected_at=timezone.now() - timedelta(seconds=1))

    with patch.object(scale, "read_truck_scale") as read_scale:
        result = services.process_vehicle_plate_event(
            third.pk,
            allow_capture=True,
        )

    assert result.status == "manual_required"
    assert result.error == "recent_completed_passage"
    read_scale.assert_not_called()
    assert Wagon.objects.filter(number="123ABC02").count() == 1


def test_lane_processing_lease_blocks_another_plate_without_parallel_read():
    now = timezone.now()
    event(number="111AAA01").__class__.objects.filter(vehicle_number="111AAA01").update(
        processing_status=VehiclePlateEvent.PROCESSING,
        processing_action="entry",
        processing_started_at=now,
    )
    second = event(number="222BBB02")

    with patch.object(scale, "read_truck_scale") as read_scale:
        result = services.process_vehicle_plate_event(
            second.pk,
            allow_capture=True,
        )

    assert result.status == "manual_required"
    assert result.error == "lane_busy"
    read_scale.assert_not_called()
    second.refresh_from_db()
    assert second.processing_status == VehiclePlateEvent.FAILED


def test_expired_processing_lease_is_terminal_without_another_scale_read():
    plate_event = event()
    VehiclePlateEvent.objects.filter(pk=plate_event.pk).update(
        processing_status=VehiclePlateEvent.PROCESSING,
        processing_action="entry",
        processing_attempts=1,
        processing_started_at=timezone.now() - timedelta(minutes=1),
    )

    with patch.object(scale, "read_truck_scale") as read_scale:
        result = services.process_vehicle_plate_event(plate_event.pk)

    plate_event.refresh_from_db()
    assert result.status == "manual_required"
    assert result.error == "processing_interrupted"
    assert plate_event.processing_status == VehiclePlateEvent.FAILED
    read_scale.assert_not_called()


def test_claim_generation_change_prevents_stale_reader_apply():
    plate_event = event()
    claim = services._begin_vehicle_plate_automation(
        plate_event.pk,
        now=timezone.now(),
        allow_capture=True,
    )
    assert isinstance(claim, services._AutomationClaim)
    VehiclePlateEvent.objects.filter(pk=plate_event.pk).update(
        processing_attempts=claim.attempt + 1
    )

    result = services._apply_vehicle_plate_automation(
        claim,
        reading=reading("12000"),
        weight_kg=12_000,
        user=None,
    )

    assert result.status == "manual_required"
    assert result.error == "automation_state_changed"
    assert not Wagon.objects.exists()


def test_authoritative_scale_busy_fails_auto_event_without_live_read():
    plate_event = event()
    lock_key = scale.authoritative_capture_lock_key(scale.TRUCK_SCALE_KEY)
    assert cache.add(lock_key, "manual-owner", timeout=30)
    try:
        with patch.object(scale, "read_truck_scale") as read_scale:
            result = services.process_vehicle_plate_event(
                plate_event.pk,
                allow_capture=True,
            )
    finally:
        cache.delete(lock_key)

    plate_event.refresh_from_db()
    assert result.status == "manual_required"
    assert result.error == "truck_scale_capture_busy"
    assert plate_event.processing_status == VehiclePlateEvent.FAILED
    read_scale.assert_not_called()


def test_authoritative_scale_busy_blocks_manual_capture_before_read():
    wagon = Wagon.objects.create(
        number="MANUAL-1",
        direction=Wagon.PASSAGE,
        workflow="simple",
        cargo_name="Отруби",
        status=st.ARRIVED,
    )
    lock_key = scale.authoritative_capture_lock_key(scale.TRUCK_SCALE_KEY)
    assert cache.add(lock_key, "auto-owner", timeout=30)
    try:
        with (
            patch.object(scale, "read_truck_scale") as read_scale,
            pytest.raises(scale.TruckScaleCaptureBusy),
        ):
            services.record_scale_weight(wagon, "entry", None)
    finally:
        cache.delete(lock_key)

    wagon.refresh_from_db()
    assert wagon.status == st.ARRIVED
    read_scale.assert_not_called()


def test_manual_apply_operational_error_returns_safe_503_without_persisting_sample():
    wagon = Wagon.objects.create(
        number="MANUAL-DB",
        direction=Wagon.PASSAGE,
        workflow="simple",
        cargo_name="Отруби",
        status=st.ARRIVED,
    )

    with (
        patch.object(scale, "read_truck_scale", return_value=reading("12000")),
        patch.object(
            services,
            "_store_scale_weight",
            side_effect=OperationalError("lock timeout"),
        ),
        pytest.raises(scale.TruckScaleApplyUnavailable) as raised,
    ):
        services.record_scale_weight(wagon, "entry", None)

    wagon.refresh_from_db()
    assert raised.value.status_code == 503
    assert raised.value.get_codes() == "truck_scale_apply_unavailable"
    assert wagon.status == st.ARRIVED
    assert wagon.entry_weight_kg is None
    assert not WeighingRecord.objects.filter(wagon=wagon).exists()


def test_auto_apply_operational_error_is_terminal_and_never_rereads_uuid():
    plate_event = event()
    with (
        patch.object(scale, "read_truck_scale", return_value=reading("12000")),
        patch.object(
            services,
            "_apply_vehicle_plate_automation",
            side_effect=OperationalError("statement timeout"),
        ),
    ):
        result = services.process_vehicle_plate_event(
            plate_event.pk,
            allow_capture=True,
        )

    plate_event.refresh_from_db()
    assert result.status == "manual_required"
    assert result.error == "truck_scale_apply_unavailable"
    assert plate_event.processing_status == VehiclePlateEvent.FAILED
    assert plate_event.processing_error == "truck_scale_apply_unavailable"
    assert not Wagon.objects.exists()

    with patch.object(scale, "read_truck_scale") as read_scale:
        duplicate = services.process_vehicle_plate_event(plate_event.pk)
    assert duplicate.status == "manual_required"
    read_scale.assert_not_called()


def webhook_payload(event_id, detected_at):
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


def test_webhook_scale_failure_is_terminal_201_then_duplicate_200_without_read(
    api_client,
    settings,
):
    settings.VEHICLE_PLATE_WEBHOOK_TOKEN = WEBHOOK_TOKEN
    settings.VEHICLE_PLATE_WEBHOOK_MAX_BODY_BYTES = 64 * 1024
    event_id = uuid.uuid4()
    payload = webhook_payload(event_id, timezone.now() - timedelta(seconds=2))
    headers = {
        "HTTP_AUTHORIZATION": f"Bearer {WEBHOOK_TOKEN}",
        "HTTP_IDEMPOTENCY_KEY": str(event_id),
    }

    with patch.object(
        scale,
        "read_truck_scale",
        side_effect=scale.TruckScaleNotReady(),
    ):
        first = api_client.post(
            WEBHOOK_URL,
            payload,
            format="json",
            secure=True,
            **headers,
        )
    assert first.status_code == 201
    assert first.data["duplicate"] is False
    assert first.data["ok"] is True
    assert first.data["automation"]["status"] == "manual_required"
    assert first.data["automation"]["error"] == "truck_scale_not_ready"
    assert VehiclePlateEvent.objects.count() == 1
    assert not Wagon.objects.exists()

    with patch.object(scale, "read_truck_scale") as read_scale:
        retry = api_client.post(
            WEBHOOK_URL,
            payload,
            format="json",
            secure=True,
            **headers,
        )

    assert retry.status_code == 200
    assert retry.data["duplicate"] is True
    assert retry.data["automation"]["status"] == "manual_required"
    assert retry.data["automation"]["action"] == "entry"
    assert retry.data["automation"]["error"] == "truck_scale_not_ready"
    read_scale.assert_not_called()
    assert not Wagon.objects.exists()


def test_duplicate_preexisting_received_event_misses_capture_without_scale_read(
    api_client,
    settings,
):
    settings.VEHICLE_PLATE_WEBHOOK_TOKEN = WEBHOOK_TOKEN
    settings.VEHICLE_PLATE_WEBHOOK_MAX_BODY_BYTES = 64 * 1024
    stored = event()
    payload = webhook_payload(stored.event_id, stored.detected_at)

    with patch.object(scale, "read_truck_scale") as read_scale:
        response = api_client.post(
            WEBHOOK_URL,
            payload,
            format="json",
            secure=True,
            HTTP_AUTHORIZATION=f"Bearer {WEBHOOK_TOKEN}",
            HTTP_IDEMPOTENCY_KEY=str(stored.event_id),
        )

    stored.refresh_from_db()
    assert response.status_code == 200
    assert response.data["duplicate"] is True
    assert response.data["automation"]["status"] == "manual_required"
    assert response.data["automation"]["error"] == "capture_window_missed"
    assert stored.processing_status == VehiclePlateEvent.FAILED
    read_scale.assert_not_called()


def test_duplicate_fresh_processing_event_returns_503_without_second_read(
    api_client,
    settings,
):
    settings.VEHICLE_PLATE_WEBHOOK_TOKEN = WEBHOOK_TOKEN
    settings.VEHICLE_PLATE_WEBHOOK_MAX_BODY_BYTES = 64 * 1024
    stored = event()
    VehiclePlateEvent.objects.filter(pk=stored.pk).update(
        processing_status=VehiclePlateEvent.PROCESSING,
        processing_action="entry",
        processing_attempts=1,
        processing_started_at=timezone.now(),
    )
    payload = webhook_payload(stored.event_id, stored.detected_at)

    with patch.object(scale, "read_truck_scale") as read_scale:
        response = api_client.post(
            WEBHOOK_URL,
            payload,
            format="json",
            secure=True,
            HTTP_AUTHORIZATION=f"Bearer {WEBHOOK_TOKEN}",
            HTTP_IDEMPOTENCY_KEY=str(stored.event_id),
        )

    assert response.status_code == 503
    assert response.data["duplicate"] is True
    assert response.data["code"] == "vehicle_plate_automation_retry"
    assert response.data["automation"]["error"] == "automation_busy"
    read_scale.assert_not_called()


def test_integrity_error_is_not_masked_and_duplicate_never_rereads(
    api_client,
    settings,
):
    settings.VEHICLE_PLATE_WEBHOOK_TOKEN = WEBHOOK_TOKEN
    settings.VEHICLE_PLATE_WEBHOOK_MAX_BODY_BYTES = 64 * 1024
    event_id = uuid.uuid4()
    payload = webhook_payload(event_id, timezone.now() - timedelta(seconds=2))
    headers = {
        "HTTP_AUTHORIZATION": f"Bearer {WEBHOOK_TOKEN}",
        "HTTP_IDEMPOTENCY_KEY": str(event_id),
    }

    with (
        patch.object(scale, "read_truck_scale", return_value=reading("12000")) as read,
        patch.object(
            services,
            "_apply_vehicle_plate_automation",
            side_effect=IntegrityError("unexpected integrity failure"),
        ),
    ):
        first = api_client.post(
            WEBHOOK_URL,
            payload,
            format="json",
            secure=True,
            **headers,
        )

    assert first.status_code == 503
    assert first.data["code"] == "temporary_automation_error"
    read.assert_called_once_with(scale.TRUCK_SCALE_KEY)
    stored = VehiclePlateEvent.objects.get(event_id=event_id)
    assert stored.processing_status == VehiclePlateEvent.PROCESSING

    with patch.object(scale, "read_truck_scale") as duplicate_read:
        duplicate = api_client.post(
            WEBHOOK_URL,
            payload,
            format="json",
            secure=True,
            **headers,
        )

    assert duplicate.status_code == 503
    assert duplicate.data["duplicate"] is True
    assert duplicate.data["code"] == "vehicle_plate_automation_retry"
    duplicate_read.assert_not_called()


def test_enabled_webhook_returns_201_with_processed_entry(api_client, settings):
    settings.VEHICLE_PLATE_WEBHOOK_TOKEN = WEBHOOK_TOKEN
    settings.VEHICLE_PLATE_WEBHOOK_MAX_BODY_BYTES = 64 * 1024
    event_id = uuid.uuid4()
    payload = webhook_payload(event_id, timezone.now() - timedelta(seconds=2))

    with patch.object(scale, "read_truck_scale", return_value=reading("12000")):
        response = api_client.post(
            WEBHOOK_URL,
            payload,
            format="json",
            secure=True,
            HTTP_AUTHORIZATION=f"Bearer {WEBHOOK_TOKEN}",
            HTTP_IDEMPOTENCY_KEY=str(event_id),
        )

    assert response.status_code == 201
    assert response.data["duplicate"] is False
    assert response.data["vehicle_event_id"] == VehiclePlateEvent.objects.get().pk
    assert response.data["automation"] == {
        "status": "processed",
        "action": "entry",
        "error": None,
        "wagon_id": Wagon.objects.get().pk,
        "weight_kg": 12_000,
    }


def test_disabled_webhook_preserves_original_response_and_does_not_read_scale(
    api_client,
    settings,
):
    settings.VEHICLE_PLATE_AUTO_EXPORT_ENABLED = False
    settings.VEHICLE_PLATE_WEBHOOK_TOKEN = WEBHOOK_TOKEN
    settings.VEHICLE_PLATE_WEBHOOK_MAX_BODY_BYTES = 64 * 1024
    event_id = uuid.uuid4()
    payload = webhook_payload(event_id, timezone.now() - timedelta(seconds=2))

    with patch.object(scale, "read_truck_scale") as read_scale:
        response = api_client.post(
            WEBHOOK_URL,
            payload,
            format="json",
            secure=True,
            HTTP_AUTHORIZATION=f"Bearer {WEBHOOK_TOKEN}",
            HTTP_IDEMPOTENCY_KEY=str(event_id),
        )

    stored = VehiclePlateEvent.objects.get()
    assert response.status_code == 201
    assert response.data == {
        "ok": True,
        "duplicate": False,
        "event_id": str(event_id),
        "vehicle_event_id": stored.pk,
    }
    assert stored.processing_status == VehiclePlateEvent.RECEIVED
    read_scale.assert_not_called()
    assert not Wagon.objects.exists()


def test_manual_plate_is_compacted_and_active_duplicate_is_rejected(
    auth_client,
    user_with_perms,
):
    operator = user_with_perms("auto-export-manual", codes=["grain.arrive"])
    client = auth_client(operator)
    first = client.post(
        "/api/grain/wagons/passage/",
        {"number": "123 abc 02", "cargo_name": "Отруби"},
        format="json",
    )
    second = client.post(
        "/api/grain/wagons/passage/",
        {"number": "123-ABC-02", "cargo_name": "Отруби"},
        format="json",
    )

    assert first.status_code == 201
    assert first.data["number"] == "123ABC02"
    assert second.status_code == 400
    assert second.data["code"] == "passage_already_on_site"
    assert Wagon.objects.count() == 1


class _CurrentMigrationApps:
    @staticmethod
    def get_model(app_label, model_name):
        assert (app_label, model_name) == ("grain", "Wagon")
        return Wagon


class _CurrentSchemaEditor:
    connection = connection


def test_migration_canonicalizes_legacy_active_kz_plate_before_constraint():
    wagon = Wagon.objects.create(
        number="123 abc-02",
        direction=Wagon.PASSAGE,
        workflow="simple",
        status=st.ARRIVED,
    )
    migration = import_module(
        "apps.grain.migrations.0006_auto_export_plate_events"
    )

    migration.canonicalize_active_passage_numbers(
        _CurrentMigrationApps(),
        _CurrentSchemaEditor(),
    )

    wagon.refresh_from_db()
    assert wagon.number == "123ABC02"


def test_migration_canonicalizes_multiple_whitespace_numbers_without_collision():
    first = Wagon.objects.create(
        number="   ",
        direction=Wagon.PASSAGE,
        workflow="simple",
        status=st.ARRIVED,
    )
    second = Wagon.objects.create(
        number="\t",
        direction=Wagon.PASSAGE,
        workflow="simple",
        status=st.AT_SILO,
    )
    migration = import_module(
        "apps.grain.migrations.0006_auto_export_plate_events"
    )

    migration.canonicalize_active_passage_numbers(
        _CurrentMigrationApps(),
        _CurrentSchemaEditor(),
    )

    first.refresh_from_db()
    second.refresh_from_db()
    assert first.number == ""
    assert second.number == ""


def test_migration_fails_before_writes_on_canonical_active_plate_collision():
    first = Wagon.objects.create(
        number="123 ABC 02",
        direction=Wagon.PASSAGE,
        workflow="simple",
        status=st.ARRIVED,
    )
    second = Wagon.objects.create(
        number="123-ABC-02",
        direction=Wagon.PASSAGE,
        workflow="simple",
        status=st.AT_SILO,
    )
    migration = import_module(
        "apps.grain.migrations.0006_auto_export_plate_events"
    )

    with pytest.raises(RuntimeError, match="collision"):
        migration.canonicalize_active_passage_numbers(
            _CurrentMigrationApps(),
            _CurrentSchemaEditor(),
        )

    first.refresh_from_db()
    second.refresh_from_db()
    assert first.number == "123 ABC 02"
    assert second.number == "123-ABC-02"


def test_capture_lock_release_never_deletes_another_owner():
    lock_key = scale.authoritative_capture_lock_key(scale.TRUCK_SCALE_KEY)
    cache.set(lock_key, "new-owner", timeout=30)

    scale._release_capture_lock(lock_key, "stale-owner")

    assert cache.get(lock_key) == "new-owner"
    cache.delete(lock_key)


def test_capture_lock_lease_outlives_request_timeout_and_grace(settings):
    settings.TRUCK_SCALE_TIMEOUT_SECONDS = 3

    assert scale._capture_lock_seconds() >= 90
    assert scale._capture_lock_seconds() > 60
    remaining_ms = (
        scale._capture_lock_seconds()
        - int(settings.TRUCK_SCALE_TIMEOUT_SECONDS)
    ) * 1000
    assert scale.authoritative_db_timeout_ms() < remaining_ms

    deadline = scale.monotonic() + 8
    token = scale._CAPTURE_LEASE_DEADLINE.set(deadline)
    try:
        elapsed_budget_ms = scale.authoritative_db_timeout_ms()
    finally:
        scale._CAPTURE_LEASE_DEADLINE.reset(token)
    assert elapsed_budget_ms < (deadline - scale.monotonic()) * 1000


def test_auto_apply_sets_local_timeouts_while_advisory_lock_is_held():
    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL advisory lock contract")
    plate_event = event()

    with CaptureQueriesContext(connection) as queries:
        result = process(plate_event)

    assert result.status == "processed"
    sql = "\n".join(query["sql"] for query in queries.captured_queries)
    claim_index = sql.index("pg_try_advisory_lock")
    lock_timeout_index = sql.index("SET LOCAL lock_timeout")
    statement_timeout_index = sql.index("SET LOCAL statement_timeout")
    release_index = sql.index("pg_advisory_unlock")
    assert claim_index < lock_timeout_index
    assert lock_timeout_index < statement_timeout_index < release_index


def test_manual_apply_sets_local_timeouts_inside_advisory_capture():
    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL advisory lock contract")
    wagon = Wagon.objects.create(
        number="MANUAL-SQL",
        direction=Wagon.PASSAGE,
        workflow="simple",
        cargo_name="Отруби",
        status=st.ARRIVED,
    )

    with (
        CaptureQueriesContext(connection) as queries,
        patch.object(scale, "read_truck_scale", return_value=reading("12000")),
    ):
        services.record_scale_weight(wagon, "entry", None)

    sql = "\n".join(query["sql"] for query in queries.captured_queries)
    claim_index = sql.index("pg_try_advisory_lock")
    lock_timeout_index = sql.index("SET LOCAL lock_timeout")
    statement_timeout_index = sql.index("SET LOCAL statement_timeout")
    release_index = sql.index("pg_advisory_unlock")
    assert claim_index < lock_timeout_index
    assert lock_timeout_index < statement_timeout_index < release_index


@pytest.mark.django_db(transaction=True)
def test_postgres_advisory_lock_blocks_race_after_redis_lease_expires():
    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL advisory lock contract")
    lock_key = scale.authoritative_capture_lock_key(scale.TRUCK_SCALE_KEY)
    acquired = Event()
    release = Event()
    errors = []

    def hold_capture():
        close_old_connections()
        try:
            with scale.authoritative_capture(scale.TRUCK_SCALE_KEY):
                acquired.set()
                if not release.wait(timeout=10):
                    raise TimeoutError("test did not release advisory holder")
        except BaseException as exc:  # pragma: no cover - asserted in parent
            errors.append(exc)
        finally:
            close_old_connections()

    holder = Thread(target=hold_capture, daemon=True)
    holder.start()
    try:
        assert acquired.wait(timeout=5), errors
        # Simulate the finite Redis lease expiring while the first worker is
        # still alive. The PostgreSQL session lock must remain authoritative.
        cache.delete(lock_key)
        with pytest.raises(scale.TruckScaleCaptureBusy):
            with scale.authoritative_capture(scale.TRUCK_SCALE_KEY):
                pytest.fail("second capture entered after Redis lease expiry")
    finally:
        release.set()
        holder.join(timeout=10)

    assert not holder.is_alive()
    assert errors == []


def test_passage_status_label_reports_inside_loading():
    wagon = Wagon.objects.create(
        number="123ABC02",
        direction=Wagon.PASSAGE,
        status=st.AT_SILO,
    )

    assert WagonSerializer(wagon).data["status_label"] == ("На территории · погрузка")
