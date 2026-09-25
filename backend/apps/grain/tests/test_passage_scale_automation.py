from datetime import timedelta
from decimal import Decimal
from functools import partial
from unittest.mock import patch
from uuid import uuid4

import pytest
from apps.cameras import ai as camera_ai
from apps.cameras.models import VehiclePlateEvent
from apps.eventlog.models import EventLog
from apps.grain import outbox_importer
from apps.grain import passage_scale_automation as automation
from apps.grain import scale, services, vehicle_weight_capture
from apps.grain import statuses as st
from apps.grain.models import (
    AutomaticPassageCapture,
    PassageScaleAutomationState,
    UnassignedWeighing,
    Wagon,
)
from apps.grain.tests.factories import (
    outbox_event,
    passage_trip,
    recognized,
    scale_reading,
    vehicle_plate_event,
)
from django.core.cache import cache
from django.utils import timezone
from rest_framework.exceptions import ValidationError

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def automatic_scale_settings(settings):
    PassageScaleAutomationState.objects.all().delete()
    settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED = True
    settings.VEHICLE_PLATE_WEIGHT_FIRST_ENABLED = False
    settings.VEHICLE_PLATE_WEIGHT_FIRST_CAMERA = "cam1"
    settings.VEHICLE_PLATE_WEIGHT_FIRST_SOURCE = "main"
    settings.VEHICLE_PLATE_WEIGHT_FIRST_TIMEOUT_SECONDS = 12
    settings.VEHICLE_PLATE_AUTO_SCALE_HEARTBEAT_MAX_AGE_SECONDS = 60
    settings.VEHICLE_PLATE_AUTO_EXPORT_CARGO_NAME = "Отруби"
    settings.VEHICLE_PLATE_AUTO_EXPORT_MIN_TRIP_SECONDS = 60
    settings.TRUCK_SCALE_TIMEOUT_SECONDS = 3
    settings.WEIGHING_AI_ENABLED = False
    cache.delete(automation.RUNTIME_CACHE_KEY)
    with patch.object(camera_ai, "fetch_vehicle_recognition_frame", return_value=None):
        yield
    cache.delete(automation.RUNTIME_CACHE_KEY)


def _collector_event(weight, *, number="", orientation="front"):
    """A weighing the weighbridge collector left in its outbox."""

    event = outbox_event(weight_kg=weight, orientation=orientation)
    stable_at = event["stable_weight_at"]
    if number:
        event["recognition"] = {
            "vehicle_number": number,
            "source": "main",
            "stable_weight_at": stable_at,
            "recognized_at": stable_at,
            "orientation": {"label": orientation, "confidence": 0.99},
            "confirmation": {
                "votes": 3,
                "detector_confidence": 0.91,
                "ocr_confidence": 0.96,
            },
        }
    return event


@pytest.mark.parametrize("manual_number", ["", "999XYZ01"])
def test_manual_create_fences_a_lane_snapshot_taken_while_armed(
    manual_number,
):
    state = PassageScaleAutomationState.objects.create(
        scale_number=scale.TRUCK_SCALE_KEY,
        phase=PassageScaleAutomationState.ARMED,
    )

    wagon = services.create_passage(
        None,
        number=manual_number,
        cargo_name="Отруби",
    )

    state.refresh_from_db()
    assert wagon.number == manual_number
    assert Wagon.objects.count() == 1
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

    services.create_passage(
        None,
        number="999XYZ01",
        cargo_name="Отруби",
    )

    state.refresh_from_db()
    # The pre-create snapshot (streak 1) was discarded by the manual create.
    assert state.phase == PassageScaleAutomationState.UNARMED
    assert state.clear_streak == 0
    assert Wagon.objects.count() == 1
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
    wagon = passage_trip("999XYZ01", number_source="manual")

    with patch.object(
        scale,
        "read_truck_scale",
        return_value=scale_reading("12000"),
    ):
        result = services.record_scale_weight(wagon, "entry", None)

    state.refresh_from_db()
    assert result.status == st.AT_SILO
    assert result.entry_weight_kg == 12_000
    assert state.phase == PassageScaleAutomationState.UNARMED
    assert state.clear_streak == 0
    assert not AutomaticPassageCapture.objects.exists()


def test_weight_first_manual_passage_capture_fences_automatic_lane(settings):
    settings.VEHICLE_PLATE_WEIGHT_FIRST_ENABLED = True
    state = PassageScaleAutomationState.objects.create(
        scale_number=scale.TRUCK_SCALE_KEY,
        phase=PassageScaleAutomationState.ARMED,
    )
    wagon = passage_trip(number_source="manual")

    with (
        patch.object(
            scale,
            "read_truck_scale",
            return_value=scale_reading("12000"),
        ),
        patch.object(
            camera_ai,
            "recognize_vehicle_from_camera",
            side_effect=partial(recognized, orientation="front"),
        ),
    ):
        result = vehicle_weight_capture.capture_passage_weight_and_plate(
            wagon,
            "entry",
            None,
            idempotency_key=uuid4(),
        )

    state.refresh_from_db()
    assert result.status == st.AT_SILO
    assert result.number == "123ABC02"
    assert result.entry_weight_kg == 12_000
    assert state.phase == PassageScaleAutomationState.UNARMED
    assert state.clear_streak == 0
    assert not AutomaticPassageCapture.objects.exists()


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
    wagon = passage_trip("999XYZ01", number_source="manual")

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


def test_unexpected_manual_event_action_terminalizes_capture_without_constraint_error():
    now = timezone.now()
    event = vehicle_plate_event(
        detected_at=now,
        processing_status=VehiclePlateEvent.PROCESSED,
        processing_action=services.AUTO_ACTION_MANUAL_ENTRY,
        processed_at=now,
    )
    passage_trip(event.vehicle_number, status=st.AT_SILO, entry=12_000, arrived_at=now, vehicle_plate_event=event)
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
    PassageScaleAutomationState.objects.create(scale_number=scale.TRUCK_SCALE_KEY)

    result = automation._apply_recognized_capture(capture.pk)

    capture.refresh_from_db()
    assert result.status == AutomaticPassageCapture.FAILED
    assert capture.status == AutomaticPassageCapture.FAILED
    assert capture.stage == AutomaticPassageCapture.DONE
    assert capture.action == ""
    assert capture.error_code == "automatic_passage_apply_state_changed"


def test_latched_failure_is_released_only_by_operator_acknowledgement(
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

    state = PassageScaleAutomationState.objects.get(scale_number=scale.TRUCK_SCALE_KEY)
    assert automation.scale_automation_runtime()["state"] == "manual_required"

    response = auth_client(operator).post(
        "/api/grain/automatic-passage-scale/acknowledge/",
        {"request_id": str(capture.idempotency_key), "resolved": True},
        format="json",
    )

    assert response.status_code == 200, response.data
    assert response.data["acknowledged"] is True
    assert response.data["scale_automation"]["state"] == "unavailable"
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


def test_scale_settings_are_readable_by_grain_view_and_superuser_only_mutable(
    api_client,
    auth_client,
    user_with_perms,
):
    viewer = user_with_perms("auto-scale-settings-viewer", codes=["grain.view"])
    response = auth_client(viewer).get("/api/grain/automatic-passage-scale/settings/")
    assert response.status_code == 200
    assert response.data == {"stable_weight_seconds": 10}
    assert response["Cache-Control"] == "no-store"

    denied = auth_client(viewer).patch(
        "/api/grain/automatic-passage-scale/settings/",
        {"stable_weight_seconds": 15},
        format="json",
    )
    assert denied.status_code == 403

    no_view = user_with_perms("auto-scale-settings-no-view", codes=["grain.weigh"])
    api_client.force_authenticate(no_view)
    assert (
        api_client.get("/api/grain/automatic-passage-scale/settings/").status_code
        == 403
    )


def test_scale_settings_update_resets_stabilizing_candidate_and_is_audited(
    auth_client,
    user_with_perms,
):
    admin = user_with_perms("auto-scale-settings-admin", codes=[])
    admin.is_superuser = True
    admin.save(update_fields=["is_superuser"])
    started_at = timezone.now() - timedelta(seconds=9)
    state = PassageScaleAutomationState.objects.create(
        scale_number=scale.TRUCK_SCALE_KEY,
        phase=PassageScaleAutomationState.STABILIZING,
        stable_streak=10,
        stability_started_at=started_at,
        candidate_weight_kg=Decimal("12000"),
    )

    response = auth_client(admin).patch(
        "/api/grain/automatic-passage-scale/settings/",
        {"stable_weight_seconds": 15},
        format="json",
    )

    assert response.status_code == 200, response.data
    assert response.data == {"stable_weight_seconds": 15}
    assert response["Cache-Control"] == "no-store"
    state.refresh_from_db()
    assert state.stable_weight_seconds == 15
    assert state.phase == PassageScaleAutomationState.ARMED
    assert state.stable_streak == 0
    assert state.stability_started_at is None
    assert state.candidate_weight_kg is None
    audit = EventLog.objects.get(event_type="grain_auto_scale_settings_updated")
    assert audit.user_id == admin.pk
    assert audit.payload == {
        "scale_number": scale.TRUCK_SCALE_KEY,
        "previous_stable_weight_seconds": 10,
        "stable_weight_seconds": 15,
    }
    runtime = auth_client(admin).get("/api/grain/automatic-passage-scale/runtime/")
    assert runtime.status_code == 200
    assert runtime.data["stable_weight_seconds"] == 15


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"stable_weight_seconds": True},
        {"stable_weight_seconds": 1},
        {"stable_weight_seconds": 61},
        {"stable_weight_seconds": 10.5},
        {"stable_weight_seconds": "10"},
        {"stable_weight_seconds": 10, "unexpected": 1},
    ],
)
def test_scale_settings_reject_noncanonical_or_out_of_range_payload(
    auth_client,
    user_with_perms,
    payload,
):
    admin = user_with_perms(
        f"auto-scale-settings-invalid-{len(str(payload))}", codes=[]
    )
    admin.is_superuser = True
    admin.save(update_fields=["is_superuser"])

    response = auth_client(admin).patch(
        "/api/grain/automatic-passage-scale/settings/",
        payload,
        format="json",
    )

    assert response.status_code == 400
    assert not EventLog.objects.filter(
        event_type="grain_auto_scale_settings_updated"
    ).exists()


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
    assert scale_response.data["heartbeat_stale"] is True
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


@pytest.mark.parametrize("cache_mode", ["missing", "error", "invalid", "stale"])
def test_runtime_recovers_latched_failure_from_durable_state_and_allows_ack(
    auth_client,
    user_with_perms,
    cache_mode,
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
    assert response.data["heartbeat_stale"] is True
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


def test_acknowledgement_after_manual_fallback_disarms_the_lane(
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
    state = PassageScaleAutomationState.objects.get(
        scale_number=scale.TRUCK_SCALE_KEY
    )
    assert state.phase == PassageScaleAutomationState.UNARMED
    assert state.current_capture_id is None


def test_preregistered_manual_passage_is_weighed_by_automation_when_plate_matches():
    wagon = services.create_passage(
        None,
        number="999XYZ01",
        cargo_name="Отруби",
    )

    capture = outbox_importer.import_event(
        _collector_event(12_010, number="999XYZ01")
    )

    wagon.refresh_from_db()
    # The dispatcher's passage does not block the lane: automation recognizes
    # its plate and records the empty weight into that same trip.
    assert capture.action == "entry"
    assert capture.status == AutomaticPassageCapture.COMPLETED
    assert capture.wagon_id == wagon.pk
    assert Wagon.objects.count() == 1
    assert wagon.status == st.AT_SILO
    assert wagon.entry_weight_kg == 12_010
    assert wagon.number == "999XYZ01"
    assert wagon.number_source == "manual"
    assert wagon.vehicle_plate_event_id is not None


def test_unknown_plate_is_a_new_entry_even_while_manual_passage_is_on_site():
    manual_wagon = passage_trip(
        "999XYZ01", status=st.AT_SILO, entry=12_000, ago=timedelta(minutes=2), number_source="manual",
    )

    capture = outbox_importer.import_event(
        _collector_event(30_010, number="123ABC02")
    )

    manual_wagon.refresh_from_db()
    new_wagon = Wagon.objects.get(number="123ABC02")
    # Automation never waits for a human: an unknown plate opens its own
    # trip and the manually registered one is left exactly as it was.
    assert capture.action == "entry"
    assert capture.status == AutomaticPassageCapture.COMPLETED
    assert capture.wagon_id == new_wagon.pk
    assert Wagon.objects.count() == 2
    assert new_wagon.status == st.AT_SILO
    assert new_wagon.entry_weight_kg == 30_010
    assert manual_wagon.number == "999XYZ01"
    assert manual_wagon.status == st.AT_SILO
    assert manual_wagon.exit_weight_kg is None


def test_rear_weighing_of_the_same_plate_completes_the_exit():
    entry = outbox_importer.import_event(
        _collector_event(12_000, number="123ABC02", orientation="front")
    )
    Wagon.objects.filter(pk=entry.wagon_id).update(
        arrived_at=timezone.now() - timedelta(minutes=2)
    )

    exit_capture = outbox_importer.import_event(
        _collector_event(30_000, number="123ABC02", orientation="rear")
    )

    wagon = Wagon.objects.get(pk=entry.wagon_id)
    assert [entry.action, exit_capture.action] == ["entry", "exit"]
    assert exit_capture.wagon_id == wagon.pk
    assert wagon.status == st.COMPLETED
    assert wagon.entry_weight_kg == 12_000
    assert wagon.exit_weight_kg == 30_000
    assert wagon.net_weight_kg == 18_000
    assert wagon.weighings.count() == 2


def test_weight_without_plate_opens_a_blank_passage_when_nothing_is_on_site():
    capture = outbox_importer.import_event(_collector_event(12_010))

    wagon = Wagon.objects.get()
    assert capture.status == AutomaticPassageCapture.COMPLETED
    assert capture.action == "entry"
    assert capture.plate_unresolved is True
    assert capture.acknowledged_at is None
    assert wagon.number == ""
    assert wagon.number_source == "camera"
    assert wagon.status == st.AT_SILO
    assert wagon.entry_weight_kg == 12_010
    assert wagon.cargo_name == "Отруби"


def test_weight_without_plate_or_orientation_parks_while_a_passage_is_open():
    open_passage = passage_trip("999XYZ01", status=st.AT_SILO, entry=12_000, number_source="manual")

    capture = outbox_importer.import_event(
        _collector_event(30_010, orientation="")
    )

    item = UnassignedWeighing.objects.get()
    open_passage.refresh_from_db()
    assert capture.status == AutomaticPassageCapture.COMPLETED
    assert capture.action == "unassigned"
    assert capture.wagon_id is None
    assert item.capture_id == capture.pk
    assert item.weight_kg == 30_010
    assert item.status == UnassignedWeighing.OPEN
    assert item.reason == "orientation_unknown"
    assert item.photo_request_id == capture.idempotency_key
    assert Wagon.objects.count() == 1
    assert open_passage.exit_weight_kg is None


def test_two_letter_kazakhstan_series_is_a_valid_recognized_plate():
    capture = outbox_importer.import_event(
        _collector_event(9_740, number="160AL17")
    )

    wagon = Wagon.objects.get()
    assert capture.action == "entry"
    assert wagon.number == "160AL17"
    assert wagon.entry_weight_kg == 9_740
