from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import timedelta
from decimal import Decimal
from threading import Event
from unittest.mock import patch
from uuid import uuid4

import pytest
from apps.cameras import ai as camera_ai
from apps.cameras.models import VehiclePlateEvent
from apps.eventlog.models import EventLog
from apps.grain import passage_scale_automation as automation
from apps.grain import scale, services, vehicle_weight_capture
from apps.grain import statuses as st
from apps.grain.models import (
    AutomaticPassageCapture,
    PassageScaleAutomationState,
    PassageWeightCapture,
    Wagon,
    WeighingRecord,
)
from django.core.cache import cache
from django.db import close_old_connections, connection, connections
from django.utils import timezone
from rest_framework.exceptions import ValidationError

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def automatic_scale_settings(settings):
    PassageScaleAutomationState.objects.all().delete()
    settings.VEHICLE_PLATE_AUTO_EXPORT_ENABLED = False
    settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED = True
    settings.VEHICLE_PLATE_WEIGHT_FIRST_ENABLED = False
    settings.VEHICLE_PLATE_WEIGHT_FIRST_CAMERA = "cam1"
    settings.VEHICLE_PLATE_WEIGHT_FIRST_SOURCE = "main"
    settings.VEHICLE_PLATE_WEIGHT_FIRST_TIMEOUT_SECONDS = 12
    settings.VEHICLE_PLATE_AUTO_SCALE_POLL_SECONDS = 1
    settings.VEHICLE_PLATE_AUTO_SCALE_EMPTY_MAX_KG = 500
    settings.VEHICLE_PLATE_AUTO_SCALE_STABLE_CONFIRM_POLLS = 2
    settings.VEHICLE_PLATE_AUTO_SCALE_CLEAR_CONFIRM_POLLS = 2
    settings.VEHICLE_PLATE_AUTO_SCALE_STABLE_TOLERANCE_KG = 50
    settings.VEHICLE_PLATE_AUTO_SCALE_MAX_RECOGNITION_ATTEMPTS = 3
    settings.VEHICLE_PLATE_AUTO_SCALE_HEARTBEAT_MAX_AGE_SECONDS = 60
    settings.VEHICLE_PLATE_AUTO_EXPORT_CARGO_NAME = "Отруби"
    settings.VEHICLE_PLATE_AUTO_EXPORT_MIN_TRIP_SECONDS = 60
    settings.TRUCK_SCALE_TIMEOUT_SECONDS = 3
    cache.delete(automation.RUNTIME_CACHE_KEY)
    yield
    cache.delete(automation.RUNTIME_CACHE_KEY)


def _observation(weight: str) -> scale.ScaleObservation:
    return scale.ScaleObservation(
        state="ready",
        weight_kg=Decimal(weight),
        connected=True,
        stable=True,
        stale=False,
        age_seconds=Decimal("0.200"),
        updated_at="2026-09-03T07:30:00Z",
    )


def _stale_observation() -> scale.ScaleObservation:
    return scale.ScaleObservation(
        state="stale",
        weight_kg=None,
        connected=True,
        stable=True,
        stale=True,
        age_seconds=Decimal("10.000"),
        updated_at="2026-09-03T07:29:50Z",
    )


def _reading(weight: str) -> scale.ScaleReading:
    return scale.ScaleReading(
        weight_kg=Decimal(weight),
        age_seconds=Decimal("0.200"),
        updated_at="2026-09-03T07:30:00Z",
    )


def _recognized(request_id, stable_weight_at, *, number="123ABC02") -> dict:
    return {
        "ok": True,
        "status": "recognized",
        "request_id": str(request_id),
        "camera": "cam1",
        "source": "main",
        "stable_weight_at": stable_weight_at,
        "recognized_at": timezone.now().isoformat(),
        "vehicle_number": number,
        "confirmation": {
            "votes": 3,
            "detector_confidence": 0.91,
            "ocr_confidence": 0.96,
        },
    }


def test_startup_arms_only_after_consecutive_fresh_empty_observations():
    with (
        patch.object(
            scale,
            "read_truck_scale_observation",
            side_effect=[_observation("0"), _observation("100")],
        ),
        patch.object(scale, "read_truck_scale") as strict_read,
        patch.object(camera_ai, "recognize_vehicle_from_camera") as recognize,
    ):
        first = automation.monitor_once()
        second = automation.monitor_once()

    state = PassageScaleAutomationState.objects.get(scale_number=scale.TRUCK_SCALE_KEY)
    assert first.state == "candidate"
    assert second.state == "idle"
    assert state.phase == PassageScaleAutomationState.ARMED
    assert state.clear_streak == 0
    assert not AutomaticPassageCapture.objects.exists()
    strict_read.assert_not_called()
    recognize.assert_not_called()


@pytest.mark.parametrize("manual_number", ["", "999XYZ01"])
def test_manual_create_fences_an_occupied_snapshot_taken_while_armed(
    manual_number,
):
    state = PassageScaleAutomationState.objects.create(
        scale_number=scale.TRUCK_SCALE_KEY,
        phase=PassageScaleAutomationState.ARMED,
    )
    occupied_before_create = _observation("12000")

    wagon = services.create_passage(
        None,
        number=manual_number,
        cargo_name="Отруби",
    )
    work = automation._advance_lane(
        occupied_before_create,
        now=timezone.now(),
    )

    state.refresh_from_db()
    assert wagon.number == manual_number
    assert Wagon.objects.count() == 1
    assert work is None
    assert state.phase == PassageScaleAutomationState.UNARMED
    assert state.clear_streak == 0
    assert state.stable_streak == 0
    assert state.candidate_weight_kg is None
    assert state.current_capture_id is None
    assert not AutomaticPassageCapture.objects.exists()


def test_manual_create_resets_nearly_confirmed_clear_snapshot():
    state = PassageScaleAutomationState.objects.create(
        scale_number=scale.TRUCK_SCALE_KEY,
        phase=PassageScaleAutomationState.UNARMED,
        clear_streak=1,
    )
    empty_before_create = _observation("0")

    services.create_passage(
        None,
        number="999XYZ01",
        cargo_name="Отруби",
    )
    automation._advance_lane(empty_before_create, now=timezone.now())

    state.refresh_from_db()
    assert state.phase == PassageScaleAutomationState.UNARMED
    assert state.clear_streak == 0
    assert Wagon.objects.count() == 1
    assert not AutomaticPassageCapture.objects.exists()


def test_pending_manual_passage_reserves_lane_until_entry_weight_is_recorded():
    wagon = services.create_passage(
        None,
        number="999XYZ01",
        cargo_name="Отруби",
    )
    observations = [
        _observation("0"),
        _observation("0"),
        _observation("0"),
        _observation("12000"),
        _observation("12010"),
    ]
    with (
        patch.object(
            scale,
            "read_truck_scale_observation",
            side_effect=observations,
        ),
        patch.object(scale, "read_truck_scale") as strict_read,
        patch.object(camera_ai, "recognize_vehicle_from_camera") as recognize,
    ):
        results = [automation.monitor_once() for _ in observations]

    state = PassageScaleAutomationState.objects.get(
        scale_number=scale.TRUCK_SCALE_KEY
    )
    assert {result.state for result in results} == {"candidate"}
    assert state.phase == PassageScaleAutomationState.UNARMED
    assert state.clear_streak == 0
    assert Wagon.objects.count() == 1
    assert not AutomaticPassageCapture.objects.exists()
    strict_read.assert_not_called()
    recognize.assert_not_called()

    services.record_passage_entry_weight(
        wagon,
        12_000,
        None,
        source="manual",
        manual_reason="Контрольное ручное взвешивание",
    )
    with patch.object(
        scale,
        "read_truck_scale_observation",
        side_effect=[
            _observation("12000"),
            _observation("0"),
            _observation("0"),
        ],
    ):
        automation.monitor_once()
        automation.monitor_once()
        released = automation.monitor_once()

    state.refresh_from_db()
    assert released.state == "idle"
    assert state.phase == PassageScaleAutomationState.ARMED
    assert not AutomaticPassageCapture.objects.exists()


@pytest.mark.parametrize(
    ("phase", "capture_status"),
    [
        (PassageScaleAutomationState.STABILIZING, None),
        (
            PassageScaleAutomationState.PROCESSING,
            AutomaticPassageCapture.PROCESSING,
        ),
        (
            PassageScaleAutomationState.AWAITING_CLEAR,
            AutomaticPassageCapture.COMPLETED,
        ),
    ],
)
def test_manual_create_cannot_bypass_active_automatic_episode(
    phase,
    capture_status,
):
    capture = None
    if capture_status is not None:
        capture = AutomaticPassageCapture.objects.create(
            idempotency_key=uuid4(),
            camera="cam1",
            status=capture_status,
            stage=(
                AutomaticPassageCapture.DONE
                if capture_status == AutomaticPassageCapture.COMPLETED
                else AutomaticPassageCapture.RECOGNIZING
            ),
        )
    PassageScaleAutomationState.objects.create(
        scale_number=scale.TRUCK_SCALE_KEY,
        phase=phase,
        current_capture=capture,
    )

    with pytest.raises(ValidationError) as exc_info:
        services.create_passage(
            None,
            number="999XYZ01",
            cargo_name="Отруби",
        )

    assert exc_info.value.detail["code"] == "passage_capture_in_progress"
    assert not Wagon.objects.exists()


def test_failed_automatic_episode_keeps_manual_create_available():
    capture = AutomaticPassageCapture.objects.create(
        idempotency_key=uuid4(),
        camera="cam1",
        status=AutomaticPassageCapture.FAILED,
        stage=AutomaticPassageCapture.DONE,
    )
    PassageScaleAutomationState.objects.create(
        scale_number=scale.TRUCK_SCALE_KEY,
        phase=PassageScaleAutomationState.AWAITING_CLEAR,
        current_capture=capture,
    )

    wagon = services.create_passage(
        None,
        number="999XYZ01",
        cargo_name="Отруби",
    )

    state = PassageScaleAutomationState.objects.get(
        scale_number=scale.TRUCK_SCALE_KEY
    )
    assert wagon.pk is not None
    assert state.phase == PassageScaleAutomationState.AWAITING_CLEAR
    assert state.current_capture_id == capture.pk


def test_plain_manual_passage_weight_fences_automatic_lane(settings):
    settings.VEHICLE_PLATE_WEIGHT_FIRST_ENABLED = False
    state = PassageScaleAutomationState.objects.create(
        scale_number=scale.TRUCK_SCALE_KEY,
        phase=PassageScaleAutomationState.ARMED,
    )
    wagon = Wagon.objects.create(
        number="999XYZ01",
        direction=Wagon.PASSAGE,
        workflow="simple",
        cargo_name="Отруби",
        status=st.ARRIVED,
        arrived_at=timezone.now(),
    )
    occupied_before_manual_weight = _observation("12000")

    with patch.object(
        scale,
        "read_truck_scale",
        return_value=_reading("12000"),
    ):
        result = services.record_scale_weight(wagon, "entry", None)
    work = automation._advance_lane(
        occupied_before_manual_weight,
        now=timezone.now(),
    )

    state.refresh_from_db()
    assert result.status == st.AT_SILO
    assert result.entry_weight_kg == 12_000
    assert work is None
    assert state.phase == PassageScaleAutomationState.UNARMED
    assert state.clear_streak == 0
    assert not AutomaticPassageCapture.objects.exists()


def test_weight_first_manual_passage_capture_fences_automatic_lane(settings):
    settings.VEHICLE_PLATE_WEIGHT_FIRST_ENABLED = True
    state = PassageScaleAutomationState.objects.create(
        scale_number=scale.TRUCK_SCALE_KEY,
        phase=PassageScaleAutomationState.ARMED,
    )
    wagon = Wagon.objects.create(
        number="",
        direction=Wagon.PASSAGE,
        workflow="simple",
        cargo_name="Отруби",
        status=st.ARRIVED,
        arrived_at=timezone.now(),
    )
    occupied_before_manual_weight = _observation("12000")

    with (
        patch.object(
            scale,
            "read_truck_scale",
            return_value=_reading("12000"),
        ),
        patch.object(
            camera_ai,
            "recognize_vehicle_from_camera",
            side_effect=lambda _camera, key, stable_at: _recognized(key, stable_at),
        ),
    ):
        result = vehicle_weight_capture.capture_passage_weight_and_plate(
            wagon,
            "entry",
            None,
            idempotency_key=uuid4(),
        )
    work = automation._advance_lane(
        occupied_before_manual_weight,
        now=timezone.now(),
    )

    state.refresh_from_db()
    assert result.status == st.AT_SILO
    assert result.number == "123ABC02"
    assert result.entry_weight_kg == 12_000
    assert work is None
    assert state.phase == PassageScaleAutomationState.UNARMED
    assert state.clear_streak == 0
    assert not AutomaticPassageCapture.objects.exists()


def test_processing_manual_exit_capture_keeps_automatic_lane_unarmed():
    wagon = Wagon.objects.create(
        number="123ABC02",
        direction=Wagon.PASSAGE,
        workflow="simple",
        cargo_name="Отруби",
        status=st.AT_SILO,
        arrived_at=timezone.now() - timedelta(minutes=2),
        gross_weight_kg=12_000,
        number_source="camera",
    )
    PassageWeightCapture.objects.create(
        idempotency_key=uuid4(),
        wagon=wagon,
        wagon_id_snapshot=wagon.pk,
        action=PassageWeightCapture.EXIT,
        wagon_status_before=st.AT_SILO,
        stage=PassageWeightCapture.RECOGNIZING,
        camera="cam1",
        stable_weight_at=timezone.now(),
        weight_kg=8_000,
    )
    PassageScaleAutomationState.objects.create(
        scale_number=scale.TRUCK_SCALE_KEY,
        phase=PassageScaleAutomationState.ARMED,
    )
    observations = [
        _observation("0"),
        _observation("0"),
        _observation("12000"),
        _observation("12010"),
    ]

    with (
        patch.object(
            scale,
            "read_truck_scale_observation",
            side_effect=observations,
        ),
        patch.object(scale, "read_truck_scale") as strict_read,
        patch.object(camera_ai, "recognize_vehicle_from_camera") as recognize,
    ):
        results = [automation.monitor_once() for _ in observations]

    state = PassageScaleAutomationState.objects.get(
        scale_number=scale.TRUCK_SCALE_KEY
    )
    assert {result.state for result in results} == {"candidate"}
    assert state.phase == PassageScaleAutomationState.UNARMED
    assert state.clear_streak == 0
    assert state.stable_streak == 0
    assert state.current_capture_id is None
    assert not AutomaticPassageCapture.objects.exists()
    strict_read.assert_not_called()
    recognize.assert_not_called()


@pytest.mark.parametrize("weight_first", [False, True])
def test_manual_passage_weight_rejects_active_automatic_capture(
    settings,
    weight_first,
):
    settings.VEHICLE_PLATE_WEIGHT_FIRST_ENABLED = weight_first
    automatic_capture = AutomaticPassageCapture.objects.create(
        idempotency_key=uuid4(),
        camera="cam1",
        stage=AutomaticPassageCapture.RECOGNIZING,
    )
    PassageScaleAutomationState.objects.create(
        scale_number=scale.TRUCK_SCALE_KEY,
        phase=PassageScaleAutomationState.PROCESSING,
        current_capture=automatic_capture,
    )
    wagon = Wagon.objects.create(
        number="999XYZ01",
        direction=Wagon.PASSAGE,
        workflow="simple",
        cargo_name="Отруби",
        status=st.ARRIVED,
        arrived_at=timezone.now(),
    )

    with (
        patch.object(scale, "read_truck_scale") as strict_read,
        pytest.raises(ValidationError) as exc_info,
    ):
        if weight_first:
            vehicle_weight_capture.capture_passage_weight_and_plate(
                wagon,
                "entry",
                None,
                idempotency_key=uuid4(),
            )
        else:
            services.record_scale_weight(wagon, "entry", None)

    assert exc_info.value.detail["code"] == "passage_capture_in_progress"
    strict_read.assert_not_called()
    wagon.refresh_from_db()
    assert wagon.status == st.ARRIVED


def test_one_occupied_episode_creates_one_entry_even_while_vehicle_stays_on_scale():
    observations = [
        _observation("0"),
        _observation("0"),
        _observation("12000"),
        _observation("12020"),
        _observation("12010"),
        _observation("12010"),
    ]

    with (
        patch.object(
            scale,
            "read_truck_scale_observation",
            side_effect=observations,
        ),
        patch.object(
            scale,
            "read_truck_scale",
            return_value=_reading("12010"),
        ) as strict_read,
        patch.object(
            camera_ai,
            "recognize_vehicle_from_camera",
            side_effect=lambda _camera, key, stable_at: _recognized(key, stable_at),
        ) as recognize,
    ):
        results = [automation.monitor_once() for _ in observations]

    capture = AutomaticPassageCapture.objects.get()
    state = PassageScaleAutomationState.objects.get(scale_number=scale.TRUCK_SCALE_KEY)
    wagon = Wagon.objects.get()
    weighing = WeighingRecord.objects.get(wagon=wagon)

    assert results[3].action == "entry"
    assert capture.status == AutomaticPassageCapture.COMPLETED
    assert capture.stage == AutomaticPassageCapture.DONE
    assert capture.action == "entry"
    assert capture.weight_kg == 12_010
    assert capture.cleared_at is None
    assert state.phase == PassageScaleAutomationState.AWAITING_CLEAR
    assert state.current_capture_id == capture.pk
    assert wagon.status == st.AT_SILO
    assert wagon.entry_weight_kg == 12_010
    assert (weighing.kind, weighing.weight_kg, weighing.source) == (
        "gross",
        12_010,
        "scale",
    )
    strict_read.assert_called_once_with(scale.TRUCK_SCALE_KEY)
    assert recognize.call_count == 1
    assert AutomaticPassageCapture.objects.count() == 1


def test_minimal_production_camera_response_completes_automatic_entry():
    observations = [
        _observation("0"),
        _observation("0"),
        _observation("12000"),
        _observation("12010"),
    ]
    production_response = {
        "status": "recognized",
        "vehicle_number": "123ABC02",
        "confirmation": {
            "votes": 3,
            "detector_confidence": 0.91,
            "ocr_confidence": 0.96,
        },
        "frames_scanned": 3,
    }

    with (
        patch.object(
            scale,
            "read_truck_scale_observation",
            side_effect=observations,
        ),
        patch.object(
            scale,
            "read_truck_scale",
            return_value=_reading("12010"),
        ),
        patch.object(
            camera_ai,
            "_request",
            return_value=(200, production_response),
        ) as camera_request,
    ):
        results = [automation.monitor_once() for _ in observations]

    capture = AutomaticPassageCapture.objects.get()
    wagon = Wagon.objects.get()
    assert results[-1].action == "entry"
    assert capture.status == AutomaticPassageCapture.COMPLETED
    assert capture.vehicle_number == "123ABC02"
    assert capture.camera_source == "main"
    assert capture.ai_payload_json["frames_scanned"] == 3
    assert wagon.status == st.AT_SILO
    assert wagon.entry_weight_kg == 12_010
    camera_request.assert_called_once_with(
        "POST",
        "/cameras/cam1/vehicle-recognition",
        {"stable_weight_at": automation._canonical_timestamp(capture.stable_weight_at)},
        timeout_seconds=12,
        idempotency_key=str(capture.idempotency_key),
    )


def test_wrong_manual_plate_cannot_become_duplicate_automatic_entry():
    manual_wagon = Wagon.objects.create(
        number="999XYZ01",
        direction=Wagon.PASSAGE,
        workflow="simple",
        cargo_name="Отруби",
        status=st.AT_SILO,
        arrived_at=timezone.now() - timedelta(minutes=2),
        gross_weight_kg=12_000,
        number_source="manual",
    )
    observations = [
        _observation("0"),
        _observation("0"),
        _observation("30000"),
        _observation("30010"),
    ]

    with (
        patch.object(
            scale,
            "read_truck_scale_observation",
            side_effect=observations,
        ),
        patch.object(
            scale,
            "read_truck_scale",
            return_value=_reading("30010"),
        ) as strict_read,
        patch.object(
            camera_ai,
            "recognize_vehicle_from_camera",
            side_effect=lambda _camera, key, stable_at: _recognized(
                key,
                stable_at,
                number="123ABC02",
            ),
        ) as recognize,
    ):
        results = [automation.monitor_once() for _ in observations]

    capture = AutomaticPassageCapture.objects.get()
    manual_wagon.refresh_from_db()
    state = PassageScaleAutomationState.objects.get(
        scale_number=scale.TRUCK_SCALE_KEY
    )
    assert results[-1].state == "manual_required"
    assert capture.status == AutomaticPassageCapture.FAILED
    assert capture.error_code == "manual_active_passage"
    assert state.phase == PassageScaleAutomationState.AWAITING_CLEAR
    assert state.current_capture_id == capture.pk
    assert Wagon.objects.count() == 1
    assert manual_wagon.number == "999XYZ01"
    assert manual_wagon.status == st.AT_SILO
    assert manual_wagon.exit_weight_kg is None
    strict_read.assert_called_once_with(scale.TRUCK_SCALE_KEY)
    recognize.assert_called_once()


def test_unexpected_manual_event_action_terminalizes_capture_without_constraint_error():
    now = timezone.now()
    event = VehiclePlateEvent.objects.create(
        event_id=uuid4(),
        vehicle_number="123ABC02",
        camera="cam1",
        source="main",
        detected_at=now,
        stationary_seconds=Decimal(0),
        confirmation_votes=3,
        detector_confidence=Decimal("0.91"),
        ocr_confidence=Decimal("0.96"),
        processing_status=VehiclePlateEvent.PROCESSED,
        processing_action=services.AUTO_ACTION_MANUAL_ENTRY,
        processed_at=now,
    )
    Wagon.objects.create(
        number=event.vehicle_number,
        direction=Wagon.PASSAGE,
        workflow="simple",
        cargo_name="Отруби",
        status=st.AT_SILO,
        arrived_at=now,
        gross_weight_kg=12_000,
        number_source="camera",
        vehicle_plate_event=event,
    )
    capture = AutomaticPassageCapture.objects.create(
        idempotency_key=event.event_id,
        scale_number=scale.TRUCK_SCALE_KEY,
        stage=AutomaticPassageCapture.APPLYING,
        camera="cam1",
        stable_weight_at=now,
        weight_kg=12_000,
        scale_age_seconds=Decimal("0.2"),
        vehicle_plate_event=event,
    )
    PassageScaleAutomationState.objects.create(
        scale_number=scale.TRUCK_SCALE_KEY,
        phase=PassageScaleAutomationState.PROCESSING,
        current_capture=capture,
    )

    result = automation._apply_recognized_capture(capture.pk)

    capture.refresh_from_db()
    state = PassageScaleAutomationState.objects.get(
        scale_number=scale.TRUCK_SCALE_KEY
    )
    assert result.status == AutomaticPassageCapture.FAILED
    assert capture.status == AutomaticPassageCapture.FAILED
    assert capture.stage == AutomaticPassageCapture.DONE
    assert capture.action == ""
    assert capture.error_code == "automatic_passage_apply_state_changed"
    assert state.phase == PassageScaleAutomationState.AWAITING_CLEAR


@pytest.mark.django_db(transaction=True)
def test_disable_cannot_split_business_apply_from_capture_completion(settings):
    if connection.vendor != "postgresql":
        pytest.skip("row-lock contract requires PostgreSQL")
    now = timezone.now()
    event = VehiclePlateEvent.objects.create(
        event_id=uuid4(),
        vehicle_number="123ABC02",
        camera="cam1",
        source="main",
        detected_at=now,
        stationary_seconds=Decimal(0),
        confirmation_votes=3,
        detector_confidence=Decimal("0.91"),
        ocr_confidence=Decimal("0.96"),
    )
    capture = AutomaticPassageCapture.objects.create(
        idempotency_key=event.event_id,
        scale_number=scale.TRUCK_SCALE_KEY,
        stage=AutomaticPassageCapture.APPLYING,
        camera="cam1",
        stable_weight_at=now,
        weight_kg=12_000,
        scale_age_seconds=Decimal("0.2"),
        vehicle_plate_event=event,
    )
    PassageScaleAutomationState.objects.create(
        scale_number=scale.TRUCK_SCALE_KEY,
        phase=PassageScaleAutomationState.PROCESSING,
        current_capture=capture,
    )
    disable_started = Event()
    original_apply = services.apply_automatic_passage_scale_sample

    def run_disable():
        close_old_connections()
        disable_started.set()
        try:
            return automation.monitor_once(now=now + timedelta(seconds=1)).state
        finally:
            connections.close_all()

    pool = ThreadPoolExecutor(max_workers=1)
    disable_futures = []

    def apply_while_disable_waits(*args, **kwargs):
        settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED = False
        disable_future = pool.submit(run_disable)
        disable_futures.append(disable_future)
        assert disable_started.wait(timeout=5)
        with pytest.raises(FutureTimeoutError):
            disable_future.result(timeout=0.25)
        return original_apply(*args, **kwargs)

    try:
        with patch.object(
            services,
            "apply_automatic_passage_scale_sample",
            side_effect=apply_while_disable_waits,
        ):
            apply_status = automation._apply_recognized_capture(capture.pk).status
        assert apply_status == AutomaticPassageCapture.COMPLETED
        assert disable_futures[0].result(timeout=5) == "disabled"
    finally:
        pool.shutdown(wait=True)

    capture.refresh_from_db()
    state = PassageScaleAutomationState.objects.get(
        scale_number=scale.TRUCK_SCALE_KEY
    )
    wagon = Wagon.objects.get()
    assert capture.status == AutomaticPassageCapture.COMPLETED
    assert capture.action == services.AUTO_ACTION_ENTRY
    assert capture.wagon_id == wagon.pk
    assert capture.error_code == ""
    assert state.phase == PassageScaleAutomationState.UNARMED
    assert state.current_capture_id is None
    assert wagon.entry_weight_kg == 12_000


def test_confirmed_clear_rearms_lane_and_next_episode_completes_exit():
    entry_observations = [
        _observation("0"),
        _observation("0"),
        _observation("12000"),
        _observation("12010"),
    ]
    exit_observations = [
        _observation("0"),
        _observation("0"),
        _observation("30000"),
        _observation("30020"),
    ]

    with (
        patch.object(
            scale,
            "read_truck_scale_observation",
            side_effect=entry_observations + exit_observations,
        ),
        patch.object(
            scale,
            "read_truck_scale",
            side_effect=[_reading("12000"), _reading("30000")],
        ) as strict_read,
        patch.object(
            camera_ai,
            "recognize_vehicle_from_camera",
            side_effect=lambda _camera, key, stable_at: _recognized(key, stable_at),
        ) as recognize,
    ):
        entry_results = [automation.monitor_once() for _ in entry_observations]
        wagon = Wagon.objects.get(pk=entry_results[-1].wagon_id)
        Wagon.objects.filter(pk=wagon.pk).update(
            arrived_at=timezone.now() - timedelta(minutes=2)
        )
        exit_results = [automation.monitor_once() for _ in exit_observations]

    wagon.refresh_from_db()
    captures = list(AutomaticPassageCapture.objects.order_by("id"))
    state = PassageScaleAutomationState.objects.get(scale_number=scale.TRUCK_SCALE_KEY)

    assert entry_results[-1].action == "entry"
    assert exit_results[-1].action == "exit"
    assert [capture.action for capture in captures] == ["entry", "exit"]
    assert captures[0].cleared_at is not None
    assert captures[1].cleared_at is None
    assert state.phase == PassageScaleAutomationState.AWAITING_CLEAR
    assert state.current_capture_id == captures[1].pk
    assert wagon.status == st.COMPLETED
    assert wagon.entry_weight_kg == 12_000
    assert wagon.exit_weight_kg == 30_000
    assert wagon.net_weight_kg == 18_000
    assert wagon.weighings.count() == 2
    assert strict_read.call_count == recognize.call_count == 2


def test_restart_retries_persisted_camera_request_without_second_scale_capture():
    request_id = uuid4()
    stable_at = timezone.now() - timedelta(seconds=1)
    capture = AutomaticPassageCapture.objects.create(
        idempotency_key=request_id,
        scale_number=scale.TRUCK_SCALE_KEY,
        status=AutomaticPassageCapture.PROCESSING,
        stage=AutomaticPassageCapture.RECOGNIZING,
        camera="cam1",
        stable_weight_at=stable_at,
        weight_kg=12_000,
        scale_age_seconds=Decimal("0.200"),
        scale_updated_at="2026-09-03T07:30:00Z",
        recognition_attempts=1,
        retryable=True,
    )
    PassageScaleAutomationState.objects.create(
        scale_number=scale.TRUCK_SCALE_KEY,
        phase=PassageScaleAutomationState.PROCESSING,
        current_capture=capture,
    )
    AutomaticPassageCapture.objects.filter(pk=capture.pk).update(
        updated_at=timezone.now() - timedelta(seconds=5)
    )

    with (
        patch.object(
            scale,
            "read_truck_scale_observation",
            return_value=_observation("12000"),
        ) as read_observation,
        patch.object(scale, "read_truck_scale") as strict_read,
        patch.object(camera_ai, "recognize_vehicle_from_camera") as create_request,
        patch.object(
            camera_ai,
            "retry_vehicle_recognition_from_camera",
            side_effect=lambda _camera, key, timestamp: _recognized(key, timestamp),
        ) as retry_request,
    ):
        result = automation.monitor_once(now=timezone.now())

    capture.refresh_from_db()
    state = PassageScaleAutomationState.objects.get(scale_number=scale.TRUCK_SCALE_KEY)
    retry_args = retry_request.call_args.args

    assert result.action == "entry"
    assert capture.status == AutomaticPassageCapture.COMPLETED
    assert capture.recognition_attempts == 2
    assert capture.idempotency_key == request_id
    assert retry_args[0] == "cam1"
    assert retry_args[1] == request_id
    assert retry_args[2] == automation._canonical_timestamp(stable_at)
    assert state.phase == PassageScaleAutomationState.AWAITING_CLEAR
    assert Wagon.objects.count() == 1
    assert WeighingRecord.objects.count() == 1
    read_observation.assert_not_called()
    strict_read.assert_not_called()
    create_request.assert_not_called()
    retry_request.assert_called_once()


def test_changed_weight_at_authoritative_read_fails_closed_before_camera():
    observations = [
        _observation("0"),
        _observation("0"),
        _observation("12000"),
        _observation("12010"),
    ]

    with (
        patch.object(
            scale,
            "read_truck_scale_observation",
            side_effect=observations,
        ),
        patch.object(
            scale,
            "read_truck_scale",
            return_value=_reading("13000"),
        ),
        patch.object(camera_ai, "recognize_vehicle_from_camera") as recognize,
    ):
        results = [automation.monitor_once() for _ in observations]

    capture = AutomaticPassageCapture.objects.get()
    state = PassageScaleAutomationState.objects.get(scale_number=scale.TRUCK_SCALE_KEY)
    assert results[-1].state == "manual_required"
    assert results[-1].error_code == "automatic_scale_candidate_changed"
    assert capture.status == AutomaticPassageCapture.FAILED
    assert capture.error_code == "automatic_scale_candidate_changed"
    assert capture.trigger_weight_kg == Decimal(12010)
    assert capture.weight_kg is None
    assert state.phase == PassageScaleAutomationState.AWAITING_CLEAR
    assert not Wagon.objects.exists()
    recognize.assert_not_called()


def test_stale_observation_never_counts_toward_confirmed_clear():
    capture = AutomaticPassageCapture.objects.create(
        idempotency_key=uuid4(),
        scale_number=scale.TRUCK_SCALE_KEY,
        status=AutomaticPassageCapture.COMPLETED,
        stage=AutomaticPassageCapture.DONE,
        camera="cam1",
        action="entry",
        completed_at=timezone.now(),
    )
    PassageScaleAutomationState.objects.create(
        scale_number=scale.TRUCK_SCALE_KEY,
        phase=PassageScaleAutomationState.AWAITING_CLEAR,
        current_capture=capture,
    )
    observations = [
        _observation("0"),
        _stale_observation(),
        _observation("0"),
    ]

    with patch.object(
        scale,
        "read_truck_scale_observation",
        side_effect=observations,
    ):
        results = [automation.monitor_once() for _ in observations]

    state = PassageScaleAutomationState.objects.get(scale_number=scale.TRUCK_SCALE_KEY)
    capture.refresh_from_db()
    assert [result.state for result in results] == [
        "awaiting_clear",
        "unavailable",
        "awaiting_clear",
    ]
    assert state.phase == PassageScaleAutomationState.AWAITING_CLEAR
    assert state.clear_streak == 1
    assert state.current_capture_id == capture.pk
    assert capture.cleared_at is None


def test_failed_capture_stays_latched_after_clear_until_operator_acknowledges(
    auth_client,
    user_with_perms,
):
    operator = user_with_perms("auto-scale-ack", codes=["grain.weigh"])
    capture = AutomaticPassageCapture.objects.create(
        idempotency_key=uuid4(),
        scale_number=scale.TRUCK_SCALE_KEY,
        status=AutomaticPassageCapture.FAILED,
        stage=AutomaticPassageCapture.DONE,
        camera="cam1",
        error_code="vehicle_recognition_unavailable",
        completed_at=timezone.now(),
    )
    PassageScaleAutomationState.objects.create(
        scale_number=scale.TRUCK_SCALE_KEY,
        phase=PassageScaleAutomationState.AWAITING_CLEAR,
        current_capture=capture,
    )

    with patch.object(
        scale,
        "read_truck_scale_observation",
        side_effect=[_observation("0"), _observation("0"), _observation("0")],
    ):
        results = [automation.monitor_once() for _ in range(3)]

    state = PassageScaleAutomationState.objects.get(scale_number=scale.TRUCK_SCALE_KEY)
    capture.refresh_from_db()
    assert [result.state for result in results] == [
        "manual_required",
        "manual_required",
        "manual_required",
    ]
    assert capture.cleared_at is not None
    assert capture.acknowledged_at is None
    assert state.phase == PassageScaleAutomationState.AWAITING_CLEAR
    assert state.current_capture_id == capture.pk

    response = auth_client(operator).post(
        "/api/grain/automatic-passage-scale/acknowledge/",
        {"request_id": str(capture.idempotency_key), "resolved": True},
        format="json",
    )

    assert response.status_code == 200, response.data
    assert response.data["acknowledged"] is True
    assert response.data["scale_automation"]["state"] == "candidate"
    capture.refresh_from_db()
    state.refresh_from_db()
    assert capture.acknowledged_at is not None
    assert capture.acknowledged_by_id == operator.pk
    assert state.phase == PassageScaleAutomationState.UNARMED
    assert state.current_capture_id is None
    audit = EventLog.objects.get(event_type="grain_automatic_scale_acknowledged")
    assert audit.user_id == operator.pk
    assert audit.payload == {
        "capture_id": capture.pk,
        "request_id": str(capture.idempotency_key),
        "error_code": "vehicle_recognition_unavailable",
    }


def test_failure_acknowledgement_requires_weigh_permission(
    auth_client,
    user_with_perms,
):
    viewer = user_with_perms("auto-scale-viewer", codes=["grain.view"])

    response = auth_client(viewer).post(
        "/api/grain/automatic-passage-scale/acknowledge/",
        {"request_id": str(uuid4()), "resolved": True},
        format="json",
    )

    assert response.status_code == 403


def test_scale_runtime_requires_grain_view_permission(
    api_client,
    user_with_perms,
):
    response = api_client.get("/api/grain/automatic-passage-scale/runtime/")
    assert response.status_code in (401, 403)

    operator = user_with_perms(
        "auto-scale-runtime-no-view",
        codes=["grain.weigh"],
    )
    api_client.force_authenticate(operator)
    response = api_client.get("/api/grain/automatic-passage-scale/runtime/")
    assert response.status_code == 403


def test_latched_scale_failure_runtime_remains_available_when_camera_pc_is_down(
    auth_client,
    user_with_perms,
):
    viewer = user_with_perms("auto-scale-runtime-viewer", codes=["grain.view"])
    request_id = uuid4()
    capture = AutomaticPassageCapture.objects.create(
        idempotency_key=request_id,
        scale_number=scale.TRUCK_SCALE_KEY,
        status=AutomaticPassageCapture.FAILED,
        stage=AutomaticPassageCapture.DONE,
        camera="cam1",
        error_code="vehicle_recognition_unavailable",
        completed_at=timezone.now(),
    )
    PassageScaleAutomationState.objects.create(
        scale_number=scale.TRUCK_SCALE_KEY,
        phase=PassageScaleAutomationState.AWAITING_CLEAR,
        current_capture=capture,
    )
    automation._publish_runtime(now=timezone.now())
    client = auth_client(viewer)

    with (
        patch.object(camera_ai, "enabled", return_value=True),
        patch.object(
            camera_ai,
            "vehicle_number_info",
            side_effect=camera_ai.AiUnavailable("secret Camera-PC address"),
        ),
    ):
        camera_response = client.get("/api/cameras/vehicle-plate-runtime/")
        scale_response = client.get(
            "/api/grain/automatic-passage-scale/runtime/"
        )

    assert camera_response.status_code == 502
    assert scale_response.status_code == 200, scale_response.data
    assert scale_response["Cache-Control"] == "no-store"
    assert scale_response.data["state"] == "manual_required"
    assert scale_response.data["heartbeat_stale"] is False
    assert scale_response.data["active"] == {
        "request_id": str(request_id),
        "stage": "done",
        "action": None,
        "wagon_id": None,
        "retryable": False,
        "error_code": "vehicle_recognition_unavailable",
    }
    assert "weight_kg" not in repr(scale_response.data)
    assert "vehicle_number" not in repr(scale_response.data)
    assert "secret Camera-PC address" not in repr(scale_response.data)


def test_runtime_uses_durable_recognition_stage_over_previous_idle_cache():
    request_id = uuid4()
    capture = AutomaticPassageCapture.objects.create(
        idempotency_key=request_id,
        scale_number=scale.TRUCK_SCALE_KEY,
        status=AutomaticPassageCapture.PROCESSING,
        stage=AutomaticPassageCapture.RECOGNIZING,
        camera="cam1",
        stable_weight_at=timezone.now(),
        weight_kg=12_000,
        scale_age_seconds=Decimal("0.2"),
    )
    PassageScaleAutomationState.objects.create(
        scale_number=scale.TRUCK_SCALE_KEY,
        phase=PassageScaleAutomationState.PROCESSING,
        current_capture=capture,
    )
    checked_at = timezone.now().isoformat()
    cache.set(
        automation.RUNTIME_CACHE_KEY,
        {
            "enabled": True,
            "state": "idle",
            "last_checked_at": checked_at,
            "heartbeat_stale": False,
            "active": None,
        },
    )

    runtime = automation.scale_automation_runtime()

    assert runtime == {
        "enabled": True,
        "state": "recognizing",
        "last_checked_at": checked_at,
        "heartbeat_stale": False,
        "active": {
            "request_id": str(request_id),
            "stage": "recognizing",
            "action": None,
            "wagon_id": None,
            "retryable": False,
            "error_code": None,
        },
    }


def test_awaiting_clear_does_not_hide_fresh_scale_unavailable_runtime():
    capture = AutomaticPassageCapture.objects.create(
        idempotency_key=uuid4(),
        scale_number=scale.TRUCK_SCALE_KEY,
        status=AutomaticPassageCapture.COMPLETED,
        stage=AutomaticPassageCapture.DONE,
        camera="cam1",
        completed_at=timezone.now(),
    )
    PassageScaleAutomationState.objects.create(
        scale_number=scale.TRUCK_SCALE_KEY,
        phase=PassageScaleAutomationState.AWAITING_CLEAR,
        current_capture=capture,
    )
    checked_at = timezone.now().isoformat()
    cache.set(
        automation.RUNTIME_CACHE_KEY,
        {
            "enabled": True,
            "state": "unavailable",
            "last_checked_at": checked_at,
            "heartbeat_stale": False,
            "active": {"request_id": str(capture.idempotency_key)},
        },
    )

    runtime = automation.scale_automation_runtime()

    assert runtime["state"] == "unavailable"
    assert runtime["last_checked_at"] == checked_at


def test_disabled_ack_disarms_instead_of_rearming_an_already_clear_lane(
    auth_client,
    user_with_perms,
    settings,
):
    operator = user_with_perms(
        "auto-scale-disabled-ack",
        codes=["grain.view", "grain.weigh"],
    )
    capture = AutomaticPassageCapture.objects.create(
        idempotency_key=uuid4(),
        scale_number=scale.TRUCK_SCALE_KEY,
        status=AutomaticPassageCapture.FAILED,
        stage=AutomaticPassageCapture.DONE,
        camera="cam1",
        error_code="recognition_failed",
        completed_at=timezone.now(),
        cleared_at=timezone.now(),
    )
    state = PassageScaleAutomationState.objects.create(
        scale_number=scale.TRUCK_SCALE_KEY,
        phase=PassageScaleAutomationState.AWAITING_CLEAR,
        current_capture=capture,
    )
    settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED = False

    response = auth_client(operator).post(
        "/api/grain/automatic-passage-scale/acknowledge/",
        {"request_id": str(capture.idempotency_key), "resolved": True},
        format="json",
    )

    state.refresh_from_db()
    capture.refresh_from_db()
    assert response.status_code == 200
    assert response.data["scale_automation"]["state"] == "disabled"
    assert state.phase == PassageScaleAutomationState.UNARMED
    assert state.current_capture_id is None
    assert capture.acknowledged_at is not None


@pytest.mark.parametrize(
    ("cache_mode", "expected_heartbeat_stale"),
    [
        ("missing", True),
        ("error", True),
        ("invalid", True),
        ("stale", True),
        ("fresh_but_outdated", False),
    ],
)
def test_runtime_recovers_latched_failure_from_durable_state_and_allows_ack(
    auth_client,
    user_with_perms,
    cache_mode,
    expected_heartbeat_stale,
):
    operator = user_with_perms(
        f"auto-scale-cache-{cache_mode}",
        codes=["grain.view", "grain.weigh"],
    )
    request_id = uuid4()
    capture = AutomaticPassageCapture.objects.create(
        idempotency_key=request_id,
        scale_number=scale.TRUCK_SCALE_KEY,
        status=AutomaticPassageCapture.FAILED,
        stage=AutomaticPassageCapture.DONE,
        camera="cam1",
        error_code="vehicle_recognition_unavailable",
        completed_at=timezone.now(),
        cleared_at=timezone.now(),
    )
    PassageScaleAutomationState.objects.create(
        scale_number=scale.TRUCK_SCALE_KEY,
        phase=PassageScaleAutomationState.AWAITING_CLEAR,
        current_capture=capture,
    )
    stale_checked_at = timezone.now() - timedelta(minutes=5)
    cache_values = {
        "missing": None,
        "invalid": {"state": "private_invalid_state"},
        "stale": {
            "enabled": True,
            "state": "awaiting_clear",
            "last_checked_at": stale_checked_at.isoformat(),
            "heartbeat_stale": False,
            "active": None,
        },
        "fresh_but_outdated": {
            "enabled": True,
            "state": "idle",
            "last_checked_at": timezone.now().isoformat(),
            "heartbeat_stale": False,
            "active": None,
        },
    }
    cache_get = (
        patch.object(automation.cache, "get", side_effect=RuntimeError("cache down"))
        if cache_mode == "error"
        else patch.object(
            automation.cache,
            "get",
            return_value=cache_values[cache_mode],
        )
    )
    client = auth_client(operator)

    with cache_get:
        response = client.get("/api/grain/automatic-passage-scale/runtime/")

    assert response.status_code == 200, response.data
    assert response.data["state"] == "manual_required"
    assert response.data["heartbeat_stale"] is expected_heartbeat_stale
    assert response.data["active"] == {
        "request_id": str(request_id),
        "stage": "done",
        "action": None,
        "wagon_id": None,
        "retryable": False,
        "error_code": "vehicle_recognition_unavailable",
    }
    assert "weight_kg" not in repr(response.data)
    assert "vehicle_number" not in repr(response.data)

    acknowledgement = client.post(
        "/api/grain/automatic-passage-scale/acknowledge/",
        {"request_id": str(request_id), "resolved": True},
        format="json",
    )
    assert acknowledgement.status_code == 200, acknowledgement.data
    assert acknowledgement.data["acknowledged"] is True
    capture.refresh_from_db()
    assert capture.acknowledged_by_id == operator.pk


def test_acknowledgement_before_clear_still_requires_fresh_empty_streak(
    auth_client,
    user_with_perms,
):
    operator = user_with_perms("auto-scale-early-ack", codes=["grain.weigh"])
    capture = AutomaticPassageCapture.objects.create(
        idempotency_key=uuid4(),
        scale_number=scale.TRUCK_SCALE_KEY,
        status=AutomaticPassageCapture.FAILED,
        stage=AutomaticPassageCapture.DONE,
        camera="cam1",
        error_code="vehicle_recognition_unavailable",
        completed_at=timezone.now(),
    )
    PassageScaleAutomationState.objects.create(
        scale_number=scale.TRUCK_SCALE_KEY,
        phase=PassageScaleAutomationState.AWAITING_CLEAR,
        current_capture=capture,
    )

    response = auth_client(operator).post(
        "/api/grain/automatic-passage-scale/acknowledge/",
        {"request_id": str(capture.idempotency_key), "resolved": True},
        format="json",
    )

    assert response.status_code == 200, response.data
    assert response.data["scale_automation"]["state"] == "candidate"
    with patch.object(
        scale,
        "read_truck_scale_observation",
        side_effect=[
            _observation("12000"),
            _stale_observation(),
            _observation("0"),
            _observation("0"),
        ],
    ):
        results = [automation.monitor_once() for _ in range(4)]

    capture.refresh_from_db()
    state = PassageScaleAutomationState.objects.get(scale_number=scale.TRUCK_SCALE_KEY)
    assert [result.state for result in results] == [
        "candidate",
        "unavailable",
        "candidate",
        "idle",
    ]
    assert capture.cleared_at is None
    assert state.phase == PassageScaleAutomationState.ARMED
    assert state.current_capture_id is None


def test_acknowledgement_does_not_reuse_old_clear_after_manual_fallback(
    auth_client,
    user_with_perms,
):
    operator = user_with_perms("auto-scale-manual-ack", codes=["grain.weigh"])
    capture = AutomaticPassageCapture.objects.create(
        idempotency_key=uuid4(),
        scale_number=scale.TRUCK_SCALE_KEY,
        status=AutomaticPassageCapture.FAILED,
        stage=AutomaticPassageCapture.DONE,
        camera="cam1",
        error_code="vehicle_recognition_unavailable",
        completed_at=timezone.now(),
        cleared_at=timezone.now(),
    )
    PassageScaleAutomationState.objects.create(
        scale_number=scale.TRUCK_SCALE_KEY,
        phase=PassageScaleAutomationState.AWAITING_CLEAR,
        current_capture=capture,
    )
    wagon = services.create_passage(
        operator,
        number="999XYZ01",
        cargo_name="Отруби",
    )
    services.record_passage_entry_weight(
        wagon,
        12_000,
        operator,
        source="manual",
        manual_reason="Ручная обработка после сбоя автоматики",
    )

    response = auth_client(operator).post(
        "/api/grain/automatic-passage-scale/acknowledge/",
        {"request_id": str(capture.idempotency_key), "resolved": True},
        format="json",
    )

    assert response.status_code == 200, response.data
    assert response.data["scale_automation"]["state"] == "candidate"
    state = PassageScaleAutomationState.objects.get(
        scale_number=scale.TRUCK_SCALE_KEY
    )
    assert state.phase == PassageScaleAutomationState.UNARMED
    assert state.current_capture_id is None

    observations = [
        _observation("12000"),
        _observation("12010"),
        _observation("0"),
        _observation("0"),
    ]
    with (
        patch.object(
            scale,
            "read_truck_scale_observation",
            side_effect=observations,
        ),
        patch.object(scale, "read_truck_scale") as strict_read,
        patch.object(camera_ai, "recognize_vehicle_from_camera") as recognize,
    ):
        results = [automation.monitor_once() for _ in observations]

    state.refresh_from_db()
    assert [result.state for result in results] == [
        "candidate",
        "candidate",
        "candidate",
        "idle",
    ]
    assert state.phase == PassageScaleAutomationState.ARMED
    assert not AutomaticPassageCapture.objects.exclude(pk=capture.pk).exists()
    strict_read.assert_not_called()
    recognize.assert_not_called()


def test_unknown_final_camera_outcome_gets_one_idempotent_retrieval():
    stable_at = timezone.now() - timedelta(seconds=1)
    capture = AutomaticPassageCapture.objects.create(
        idempotency_key=uuid4(),
        scale_number=scale.TRUCK_SCALE_KEY,
        status=AutomaticPassageCapture.PROCESSING,
        stage=AutomaticPassageCapture.RECOGNIZING,
        camera="cam1",
        stable_weight_at=stable_at,
        weight_kg=12_000,
        scale_age_seconds=Decimal("0.200"),
        recognition_attempts=3,
        retryable=False,
        processing_started_at=timezone.now() - timedelta(minutes=3),
    )
    PassageScaleAutomationState.objects.create(
        scale_number=scale.TRUCK_SCALE_KEY,
        phase=PassageScaleAutomationState.PROCESSING,
        current_capture=capture,
    )

    with (
        patch.object(scale, "read_truck_scale_observation") as read_observation,
        patch.object(scale, "read_truck_scale") as strict_read,
        patch.object(
            camera_ai,
            "retry_vehicle_recognition_from_camera",
            side_effect=lambda _camera, key, timestamp: _recognized(key, timestamp),
        ) as retry_request,
    ):
        result = automation.monitor_once(now=timezone.now())

    capture.refresh_from_db()
    assert result.action == "entry"
    assert capture.status == AutomaticPassageCapture.COMPLETED
    assert capture.recognition_attempts == 3
    retry_request.assert_called_once()
    read_observation.assert_not_called()
    strict_read.assert_not_called()


def test_exhausted_explicit_camera_retries_do_not_call_dependencies_again():
    capture = AutomaticPassageCapture.objects.create(
        idempotency_key=uuid4(),
        scale_number=scale.TRUCK_SCALE_KEY,
        status=AutomaticPassageCapture.PROCESSING,
        stage=AutomaticPassageCapture.RECOGNIZING,
        camera="cam1",
        stable_weight_at=timezone.now() - timedelta(seconds=1),
        weight_kg=12_000,
        scale_age_seconds=Decimal("0.200"),
        recognition_attempts=3,
        final_lookup_attempted=True,
        retryable=True,
    )
    PassageScaleAutomationState.objects.create(
        scale_number=scale.TRUCK_SCALE_KEY,
        phase=PassageScaleAutomationState.PROCESSING,
        current_capture=capture,
    )
    AutomaticPassageCapture.objects.filter(pk=capture.pk).update(
        updated_at=timezone.now() - timedelta(seconds=5)
    )

    with (
        patch.object(scale, "read_truck_scale_observation") as read_observation,
        patch.object(scale, "read_truck_scale") as strict_read,
        patch.object(camera_ai, "recognize_vehicle_from_camera") as recognize,
        patch.object(
            camera_ai,
            "retry_vehicle_recognition_from_camera",
        ) as retry_request,
    ):
        result = automation.monitor_once(now=timezone.now())

    capture.refresh_from_db()
    assert result.state == "manual_required"
    assert capture.status == AutomaticPassageCapture.FAILED
    assert capture.error_code == "vehicle_recognition_attempts_exhausted"
    read_observation.assert_not_called()
    strict_read.assert_not_called()
    recognize.assert_not_called()
    retry_request.assert_not_called()


def test_unknown_final_lookup_timeout_becomes_terminal_and_is_not_repeated():
    capture = AutomaticPassageCapture.objects.create(
        idempotency_key=uuid4(),
        scale_number=scale.TRUCK_SCALE_KEY,
        status=AutomaticPassageCapture.PROCESSING,
        stage=AutomaticPassageCapture.RECOGNIZING,
        camera="cam1",
        stable_weight_at=timezone.now() - timedelta(seconds=1),
        weight_kg=12_000,
        scale_age_seconds=Decimal("0.200"),
        recognition_attempts=3,
        retryable=False,
        processing_started_at=timezone.now() - timedelta(minutes=3),
    )
    PassageScaleAutomationState.objects.create(
        scale_number=scale.TRUCK_SCALE_KEY,
        phase=PassageScaleAutomationState.PROCESSING,
        current_capture=capture,
    )

    with (
        patch.object(
            scale,
            "read_truck_scale_observation",
            return_value=_observation("12000"),
        ) as read_observation,
        patch.object(scale, "read_truck_scale") as strict_read,
        patch.object(
            camera_ai,
            "retry_vehicle_recognition_from_camera",
            side_effect=camera_ai.AiUnavailable("timeout"),
        ) as retry_request,
    ):
        first = automation.monitor_once(now=timezone.now())
        second = automation.monitor_once(now=timezone.now() + timedelta(seconds=1))

    capture.refresh_from_db()
    assert first.state == second.state == "manual_required"
    assert capture.status == AutomaticPassageCapture.FAILED
    assert capture.final_lookup_attempted is True
    assert capture.error_code == "vehicle_recognition_unavailable"
    retry_request.assert_called_once()
    assert read_observation.call_count == 1
    strict_read.assert_not_called()


@pytest.mark.parametrize(
    "phase",
    [
        PassageScaleAutomationState.ARMED,
        PassageScaleAutomationState.STABILIZING,
    ],
)
def test_disabled_iteration_invalidates_idle_edge_before_reenable(
    settings,
    phase,
):
    state = PassageScaleAutomationState.objects.create(
        scale_number=scale.TRUCK_SCALE_KEY,
        phase=phase,
        stable_streak=(1 if phase == PassageScaleAutomationState.STABILIZING else 0),
        candidate_weight_kg=(
            Decimal(12000)
            if phase == PassageScaleAutomationState.STABILIZING
            else None
        ),
    )
    settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED = False

    with patch.object(scale, "read_truck_scale_observation") as disabled_read:
        disabled = automation.monitor_once()

    state.refresh_from_db()
    assert disabled.state == "disabled"
    assert state.phase == PassageScaleAutomationState.UNARMED
    assert state.clear_streak == 0
    assert state.stable_streak == 0
    assert state.candidate_weight_kg is None
    assert state.current_capture_id is None
    disabled_read.assert_not_called()

    settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED = True
    with (
        patch.object(
            scale,
            "read_truck_scale_observation",
            return_value=_observation("12000"),
        ),
        patch.object(scale, "read_truck_scale") as strict_read,
        patch.object(camera_ai, "recognize_vehicle_from_camera") as recognize,
    ):
        reenabled = automation.monitor_once()

    state.refresh_from_db()
    assert reenabled.state == "candidate"
    assert state.phase == PassageScaleAutomationState.UNARMED
    assert not AutomaticPassageCapture.objects.exists()
    strict_read.assert_not_called()
    recognize.assert_not_called()


def test_disabled_monitor_terminalizes_processing_before_manual_fallback(settings):
    settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED = False
    capture = AutomaticPassageCapture.objects.create(
        idempotency_key=uuid4(),
        scale_number=scale.TRUCK_SCALE_KEY,
        status=AutomaticPassageCapture.PROCESSING,
        stage=AutomaticPassageCapture.RECOGNIZING,
        camera="cam1",
        stable_weight_at=timezone.now() - timedelta(seconds=1),
        weight_kg=12_000,
        scale_age_seconds=Decimal("0.200"),
        recognition_attempts=2,
        retryable=True,
    )
    state = PassageScaleAutomationState.objects.create(
        scale_number=scale.TRUCK_SCALE_KEY,
        phase=PassageScaleAutomationState.PROCESSING,
        current_capture=capture,
    )

    with (
        patch.object(scale, "read_truck_scale_observation") as read_observation,
        patch.object(scale, "read_truck_scale") as strict_read,
        patch.object(camera_ai, "recognize_vehicle_from_camera") as recognize,
        patch.object(
            camera_ai,
            "retry_vehicle_recognition_from_camera",
        ) as retry_request,
        patch.object(services, "apply_automatic_passage_scale_sample") as apply,
    ):
        result = automation.monitor_once()

    assert result.state == "disabled"
    capture.refresh_from_db()
    state.refresh_from_db()
    assert capture.status == AutomaticPassageCapture.FAILED
    assert capture.stage == AutomaticPassageCapture.DONE
    assert capture.retryable is False
    assert capture.error_code == "automatic_scale_disabled"
    assert capture.processing_started_at is None
    assert capture.completed_at is not None
    assert state.phase == PassageScaleAutomationState.AWAITING_CLEAR
    assert state.current_capture_id == capture.pk
    disabled_runtime = automation.scale_automation_runtime()
    assert disabled_runtime["enabled"] is False
    assert disabled_runtime["state"] == "manual_required"
    assert disabled_runtime["active"]["request_id"] == str(
        capture.idempotency_key
    )
    read_observation.assert_not_called()
    strict_read.assert_not_called()
    recognize.assert_not_called()
    retry_request.assert_not_called()
    apply.assert_not_called()

    manual_wagon = services.create_passage(
        None,
        number="999XYZ01",
        cargo_name="Отруби",
    )
    settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED = True
    with (
        patch.object(
            scale,
            "read_truck_scale_observation",
            return_value=_observation("12000"),
        ),
        patch.object(scale, "read_truck_scale") as strict_read,
        patch.object(camera_ai, "recognize_vehicle_from_camera") as recognize,
        patch.object(
            camera_ai,
            "retry_vehicle_recognition_from_camera",
        ) as retry_request,
        patch.object(services, "apply_automatic_passage_scale_sample") as apply,
    ):
        reenabled = automation.monitor_once()

    assert reenabled.state == "manual_required"
    assert Wagon.objects.filter(pk=manual_wagon.pk).count() == 1
    assert Wagon.objects.count() == 1
    strict_read.assert_not_called()
    recognize.assert_not_called()
    retry_request.assert_not_called()
    apply.assert_not_called()


def test_disabling_after_success_releases_manual_mode_but_requires_new_clear(settings):
    capture = AutomaticPassageCapture.objects.create(
        idempotency_key=uuid4(),
        scale_number=scale.TRUCK_SCALE_KEY,
        status=AutomaticPassageCapture.COMPLETED,
        stage=AutomaticPassageCapture.DONE,
        camera="cam1",
        action=services.AUTO_ACTION_ENTRY,
        completed_at=timezone.now(),
    )
    state = PassageScaleAutomationState.objects.create(
        scale_number=scale.TRUCK_SCALE_KEY,
        phase=PassageScaleAutomationState.AWAITING_CLEAR,
        current_capture=capture,
    )
    settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED = False

    manual_wagon = services.create_passage(
        None,
        number="999XYZ01",
        cargo_name="Отруби",
    )

    state.refresh_from_db()
    assert manual_wagon.pk is not None
    assert state.phase == PassageScaleAutomationState.UNARMED
    assert state.current_capture_id is None

    settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED = True
    with (
        patch.object(
            scale,
            "read_truck_scale_observation",
            return_value=_observation("12000"),
        ),
        patch.object(scale, "read_truck_scale") as strict_read,
        patch.object(camera_ai, "recognize_vehicle_from_camera") as recognize,
    ):
        reenabled = automation.monitor_once()

    state.refresh_from_db()
    assert reenabled.state == "candidate"
    assert state.phase == PassageScaleAutomationState.UNARMED
    assert AutomaticPassageCapture.objects.count() == 1
    strict_read.assert_not_called()
    recognize.assert_not_called()


def test_disabled_monitor_does_not_touch_scale_or_persistent_lane(settings):
    settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED = False

    with patch.object(scale, "read_truck_scale_observation") as read_observation:
        result = automation.monitor_once()

    assert result.state == "disabled"
    assert automation.scale_automation_runtime() == {
        "enabled": False,
        "state": "disabled",
        "last_checked_at": None,
        "heartbeat_stale": False,
        "active": None,
    }
    assert not PassageScaleAutomationState.objects.exists()
    assert not AutomaticPassageCapture.objects.exists()
    read_observation.assert_not_called()
