from datetime import timedelta
from threading import Event, Thread
from unittest.mock import patch

import pytest
from apps.cameras.models import VehiclePlateEvent
from apps.grain import scale, services
from apps.grain import statuses as st
from apps.grain.models import Wagon, WeighingRecord
from apps.grain.serializers import WagonSerializer
from apps.grain.tests.factories import scale_reading, vehicle_plate_event
from django.core.cache import cache
from django.db import OperationalError, close_old_connections, connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def auto_export_settings(settings):
    settings.VEHICLE_PLATE_WEIGHT_FIRST_CAMERA = "cam1"
    settings.VEHICLE_PLATE_WEIGHT_FIRST_SOURCE = "main"
    settings.VEHICLE_PLATE_AUTO_EXPORT_CARGO_NAME = "Отруби"
    settings.VEHICLE_PLATE_AUTO_EXPORT_MIN_TRIP_SECONDS = 60
    settings.TRUCK_SCALE_TIMEOUT_SECONDS = 3


def process(plate_event, weight="12000", *, orientation="front"):
    return services.apply_automatic_passage_scale_sample(
        plate_event.pk,
        reading=scale_reading(weight),
        orientation=orientation,
    )


def open_automatic_passage(*, number="123ABC02", entry_weight="12000"):
    entry = vehicle_plate_event(vehicle_number=number)
    result = process(entry, entry_weight)
    wagon = Wagon.objects.get(pk=result.wagon_id)
    old_arrival = timezone.now() - timedelta(minutes=2)
    Wagon.objects.filter(pk=wagon.pk).update(arrived_at=old_arrival)
    wagon.refresh_from_db()
    return entry, wagon


def test_first_event_creates_and_weighs_camera_passage_atomically():
    detected_at = timezone.now() - timedelta(seconds=3)
    plate_event = vehicle_plate_event(detected_at=detected_at)

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


def test_duplicate_processed_event_never_creates_second_trip():
    plate_event = vehicle_plate_event()
    first = process(plate_event)

    duplicate = process(plate_event)

    assert duplicate.status == "already_processed"
    assert duplicate.wagon_id == first.wagon_id
    assert Wagon.objects.count() == 1
    assert WeighingRecord.objects.count() == 1


def test_second_distinct_event_completes_same_plate_with_exit_weight():
    _entry, wagon = open_automatic_passage()
    exit_detected_at = timezone.now() - timedelta(seconds=2)
    exit_event = vehicle_plate_event(detected_at=exit_detected_at)

    result = process(exit_event, "30000", orientation="rear")

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


def test_new_entry_without_camera_verdict_fails_closed():
    plate_event = vehicle_plate_event()

    result = process(plate_event, orientation="")

    plate_event.refresh_from_db()
    assert result.status == "manual_required"
    assert result.error == "orientation_unknown"
    assert plate_event.processing_status == VehiclePlateEvent.FAILED
    assert not Wagon.objects.exists()


def test_wrong_lane_is_store_only():
    plate_event = vehicle_plate_event(camera="cam2")

    result = process(plate_event)

    plate_event.refresh_from_db()
    assert result.status == "ignored"
    assert result.error == "wrong_lane"
    assert plate_event.processing_action == "ignored"
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
    plate_event = vehicle_plate_event()

    result = process(plate_event)

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
    plate_event = vehicle_plate_event()

    result = process(plate_event)

    assert (result.status, result.action) == ("processed", "entry")
    assert Wagon.objects.count() == 2
    assert Wagon.objects.get(number="123ABC02").entry_weight_kg == 12_000


def test_second_event_before_minimum_gap_fails_closed():
    entry = vehicle_plate_event()
    first = process(entry)
    exit_event = vehicle_plate_event(detected_at=timezone.now() - timedelta(seconds=1))

    result = process(exit_event, orientation="rear")

    assert result.status == "manual_required"
    assert result.error == "entry_exit_too_close"
    wagon = Wagon.objects.get(pk=first.wagon_id)
    assert wagon.status == st.AT_SILO
    assert wagon.exit_weight_kg is None


def test_exit_not_heavier_than_entry_rolls_back_exit_link_and_weighing():
    _entry, wagon = open_automatic_passage(entry_weight="20000")
    exit_event = vehicle_plate_event()

    result = process(exit_event, "19000", orientation="rear")

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
    exit_event = vehicle_plate_event()
    process(exit_event, "30000", orientation="rear")
    third = vehicle_plate_event(detected_at=timezone.now() - timedelta(seconds=1))

    result = process(third)

    assert result.status == "manual_required"
    assert result.error == "recent_completed_passage"
    assert Wagon.objects.filter(number="123ABC02").count() == 1


def test_lane_processing_lease_blocks_another_plate():
    now = timezone.now()
    VehiclePlateEvent.objects.filter(pk=vehicle_plate_event(vehicle_number="111AAA01").pk).update(
        processing_status=VehiclePlateEvent.PROCESSING,
        processing_action="entry",
        processing_started_at=now,
    )
    second = vehicle_plate_event(vehicle_number="222BBB02")

    result = process(second)

    assert result.status == "manual_required"
    assert result.error == "lane_busy"
    second.refresh_from_db()
    assert second.processing_status == VehiclePlateEvent.FAILED


def test_fresh_processing_lease_asks_the_coordinator_to_retry():
    plate_event = vehicle_plate_event()
    VehiclePlateEvent.objects.filter(pk=plate_event.pk).update(
        processing_status=VehiclePlateEvent.PROCESSING,
        processing_action="entry",
        processing_attempts=1,
        processing_started_at=timezone.now(),
    )

    result = process(plate_event)

    plate_event.refresh_from_db()
    assert (result.status, result.error, result.retryable) == (
        "retry",
        "automation_busy",
        True,
    )
    assert plate_event.processing_status == VehiclePlateEvent.PROCESSING
    assert not Wagon.objects.exists()


def test_expired_processing_lease_reclaims_the_persisted_sample():
    plate_event = vehicle_plate_event()
    VehiclePlateEvent.objects.filter(pk=plate_event.pk).update(
        processing_status=VehiclePlateEvent.PROCESSING,
        processing_action="entry",
        processing_attempts=1,
        processing_started_at=timezone.now() - timedelta(minutes=1),
    )

    result = process(plate_event)

    plate_event.refresh_from_db()
    assert (result.status, result.action) == ("processed", "entry")
    assert plate_event.processing_status == VehiclePlateEvent.PROCESSED
    assert plate_event.processing_attempts == 2
    assert Wagon.objects.get().entry_weight_kg == 12_000


def test_expired_processing_lease_without_action_is_terminal():
    plate_event = vehicle_plate_event()
    VehiclePlateEvent.objects.filter(pk=plate_event.pk).update(
        processing_status=VehiclePlateEvent.PROCESSING,
        processing_action="",
        processing_attempts=1,
        processing_started_at=timezone.now() - timedelta(minutes=1),
    )

    result = process(plate_event)

    plate_event.refresh_from_db()
    assert result.status == "manual_required"
    assert result.error == "processing_interrupted"
    assert plate_event.processing_status == VehiclePlateEvent.FAILED
    assert not Wagon.objects.exists()


def test_claim_generation_change_prevents_stale_reader_apply():
    plate_event = vehicle_plate_event()
    claim = services._begin_vehicle_plate_automation(
        plate_event.pk,
        now=timezone.now(),
        orientation="front",
    )
    assert isinstance(claim, services._AutomationClaim)
    VehiclePlateEvent.objects.filter(pk=plate_event.pk).update(
        processing_attempts=claim.attempt + 1
    )

    result = services._apply_vehicle_plate_automation(
        claim,
        reading=scale_reading("12000"),
        weight_kg=12_000,
        user=None,
        orientation="front",
    )

    assert result.status == "manual_required"
    assert result.error == "automation_state_changed"
    assert not Wagon.objects.exists()


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
        patch.object(scale, "read_truck_scale", return_value=scale_reading("12000")),
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


def test_manual_plate_is_compacted_and_active_duplicate_is_rejected(
    auth_client,
    user_with_perms,
):
    operator = user_with_perms("auto-export-manual", codes=["grain.arrive"])
    client = auth_client(operator)
    first = client.post(
        "/api/grain/passages/",
        {"number": "123 abc 02", "cargo_name": "Отруби"},
        format="json",
    )
    second = client.post(
        "/api/grain/passages/",
        {"number": "123-ABC-02", "cargo_name": "Отруби"},
        format="json",
    )

    assert first.status_code == 201
    assert first.data["number"] == "123ABC02"
    assert second.status_code == 400
    assert second.data["code"] == "passage_already_on_site"
    assert Wagon.objects.count() == 1


def test_capture_lock_lease_outlives_request_timeout_and_grace(settings):
    assert scale._capture_lock_seconds() >= 90
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


def assert_advisory_timeouts_order(queries):
    """Локальные таймауты ставятся после захвата advisory-лока и до его снятия."""
    sql = "\n".join(query["sql"] for query in queries.captured_queries)
    claim_index = sql.index("pg_try_advisory_lock")
    lock_timeout_index = sql.index("SET LOCAL lock_timeout")
    statement_timeout_index = sql.index("SET LOCAL statement_timeout")
    release_index = sql.index("pg_advisory_unlock")
    assert claim_index < lock_timeout_index
    assert lock_timeout_index < statement_timeout_index < release_index


def test_manual_apply_sets_local_timeouts_inside_advisory_capture():
    wagon = Wagon.objects.create(
        number="MANUAL-SQL",
        direction=Wagon.PASSAGE,
        workflow="simple",
        cargo_name="Отруби",
        status=st.ARRIVED,
    )

    with (
        CaptureQueriesContext(connection) as queries,
        patch.object(scale, "read_truck_scale", return_value=scale_reading("12000")),
    ):
        services.record_scale_weight(wagon, "entry", None)

    assert_advisory_timeouts_order(queries)


@pytest.mark.django_db(transaction=True)
def test_postgres_advisory_lock_blocks_race_after_redis_lease_expires():
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
