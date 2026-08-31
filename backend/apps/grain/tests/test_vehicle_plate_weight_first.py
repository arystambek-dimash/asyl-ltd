from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest
from apps.cameras import ai as camera_ai
from apps.grain import scale, vehicle_weight_capture
from apps.grain import statuses as st
from apps.grain.models import PassageWeightCapture, Wagon, WeighingRecord
from django.utils import timezone

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def weight_first_settings(settings):
    settings.VEHICLE_PLATE_AUTO_EXPORT_ENABLED = False
    settings.VEHICLE_PLATE_WEIGHT_FIRST_ENABLED = True
    settings.VEHICLE_PLATE_WEIGHT_FIRST_CAMERA = "cam1"
    settings.VEHICLE_PLATE_WEIGHT_FIRST_TIMEOUT_SECONDS = 12


@pytest.fixture
def gate_operator(user_with_perms):
    return user_with_perms(
        "weight-first-gate",
        codes=["grain.view", "grain.weigh"],
    )


def _passage(*, number="", status=st.ARRIVED, entry_weight=None):
    return Wagon.objects.create(
        number=number,
        number_source="manual",
        direction=Wagon.PASSAGE,
        workflow="simple",
        cargo_name="Отруби",
        status=status,
        gross_weight_kg=entry_weight,
        arrived_at=timezone.now(),
    )


def _reading(weight="12000"):
    return scale.ScaleReading(
        weight_kg=Decimal(weight),
        age_seconds=Decimal("0.400"),
        updated_at="2026-08-30T10:21:14Z",
    )


def _recognized(request_id, stable_weight_at, *, number="123ABC02"):
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


def _post(auth_client, user, wagon, action, request_id=None):
    request_id = request_id or uuid4()
    response = auth_client(user).post(
        f"/api/grain/wagons/{wagon.pk}/{action}-weight/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(request_id),
    )
    return response, request_id


def test_entry_reads_scale_then_recognizes_and_atomically_saves_plate_weight_status(
    auth_client,
    gate_operator,
):
    wagon = _passage()

    def recognize(camera, request_id, stable_weight_at):
        assert camera == "cam1"
        return _recognized(request_id, stable_weight_at)

    with (
        patch.object(scale, "read_truck_scale", return_value=_reading()) as read_scale,
        patch.object(
            camera_ai,
            "recognize_vehicle_from_camera",
            side_effect=recognize,
        ) as recognize_call,
    ):
        response, request_id = _post(
            auth_client, gate_operator, wagon, "entry"
        )

    assert response.status_code == 200, response.data
    assert response.data["number"] == "123ABC02"
    assert response.data["number_source"] == "camera"
    assert response.data["entry_weight_kg"] == 12_000
    assert response.data["status"] == st.AT_SILO
    read_scale.assert_called_once_with(scale.TRUCK_SCALE_KEY)
    assert recognize_call.call_count == 1
    capture = PassageWeightCapture.objects.get(idempotency_key=request_id)
    assert capture.status == PassageWeightCapture.COMPLETED
    assert capture.stage == PassageWeightCapture.DONE
    assert capture.wagon_id_snapshot == wagon.pk
    assert capture.weight_kg == 12_000
    assert capture.vehicle_number == "123ABC02"
    assert capture.confirmation_votes == 3
    weighing = WeighingRecord.objects.get(wagon=wagon)
    assert (weighing.kind, weighing.weight_kg, weighing.source) == (
        "gross",
        12_000,
        "scale",
    )


def test_exit_requires_same_plate_and_completes_net_weight(
    auth_client,
    gate_operator,
):
    wagon = _passage(number="123ABC02", status=st.AT_SILO, entry_weight=12_000)

    def recognize(_camera, request_id, stable_weight_at):
        return _recognized(request_id, stable_weight_at)

    with (
        patch.object(scale, "read_truck_scale", return_value=_reading("30000")),
        patch.object(
            camera_ai,
            "recognize_vehicle_from_camera",
            side_effect=recognize,
        ),
    ):
        response, _request_id = _post(auth_client, gate_operator, wagon, "exit")

    assert response.status_code == 200, response.data
    assert response.data["exit_weight_kg"] == 30_000
    assert response.data["net_weight_kg"] == 18_000
    assert response.data["status"] == st.COMPLETED


def test_stable_timestamp_is_fixed_before_scale_network_call(
    auth_client,
    gate_operator,
):
    wagon = _passage()
    observed: dict[str, object] = {}

    def read_scale(_scale_key):
        observed["read_started_at"] = timezone.now()
        return scale.ScaleReading(
            weight_kg=Decimal("12000"),
            age_seconds=Decimal("0"),
            updated_at="2026-08-30T10:21:14Z",
        )

    def recognize(_camera, request_id, stable_weight_at):
        return _recognized(request_id, stable_weight_at)

    with (
        patch.object(scale, "read_truck_scale", side_effect=read_scale),
        patch.object(
            camera_ai,
            "recognize_vehicle_from_camera",
            side_effect=recognize,
        ),
    ):
        response, request_id = _post(auth_client, gate_operator, wagon, "entry")

    assert response.status_code == 200
    capture = PassageWeightCapture.objects.get(idempotency_key=request_id)
    assert capture.stable_weight_at <= observed["read_started_at"]


def test_completed_same_key_is_replayed_without_scale_or_camera(
    auth_client,
    gate_operator,
):
    wagon = _passage()
    request_id = uuid4()

    def recognize(_camera, key, stable_weight_at):
        return _recognized(key, stable_weight_at)

    with (
        patch.object(scale, "read_truck_scale", return_value=_reading()) as read_scale,
        patch.object(
            camera_ai,
            "recognize_vehicle_from_camera",
            side_effect=recognize,
        ) as recognize_call,
    ):
        first, _ = _post(auth_client, gate_operator, wagon, "entry", request_id)
        second, _ = _post(auth_client, gate_operator, wagon, "entry", request_id)

    assert first.status_code == second.status_code == 200
    assert second.data["entry_weight_kg"] == 12_000
    assert read_scale.call_count == recognize_call.call_count == 1
    assert WeighingRecord.objects.filter(wagon=wagon).count() == 1


def test_lost_camera_response_retries_same_cv_request_without_second_scale_read(
    auth_client,
    gate_operator,
):
    wagon = _passage()
    request_id = uuid4()
    def replay(_camera, key, stable_weight_at):
        return _recognized(key, stable_weight_at)

    with (
        patch.object(scale, "read_truck_scale", return_value=_reading()) as read_scale,
        patch.object(
            camera_ai,
            "recognize_vehicle_from_camera",
            side_effect=camera_ai.AiUnavailable("connection reset"),
        ) as create_call,
        patch.object(
            camera_ai,
            "retry_vehicle_recognition_from_camera",
            side_effect=replay,
        ) as retry_call,
    ):
        first, _ = _post(auth_client, gate_operator, wagon, "entry", request_id)
        second, _ = _post(auth_client, gate_operator, wagon, "entry", request_id)

    assert first.status_code == 503
    assert first.data["retryable"] is True
    assert second.status_code == 200, second.data
    assert read_scale.call_count == 1
    create_call.assert_called_once()
    retry_call.assert_called_once()
    assert WeighingRecord.objects.filter(wagon=wagon).count() == 1


def test_retry_never_creates_cv_claim_when_initial_post_was_not_delivered(
    auth_client,
    gate_operator,
):
    wagon = _passage()
    request_id = uuid4()
    missing_payload = {
        "ok": False,
        "status": "capture_window_missed",
        "request_id": str(request_id),
        "camera": "cam1",
        "retryable": False,
        "error": "the original request did not create a durable claim",
        "error_code": "recognition_request_not_found",
    }

    with (
        patch.object(scale, "read_truck_scale", return_value=_reading()) as read_scale,
        patch.object(
            camera_ai,
            "recognize_vehicle_from_camera",
            side_effect=camera_ai.AiUnavailable("connect failed before delivery"),
        ) as create_call,
        patch.object(
            camera_ai,
            "retry_vehicle_recognition_from_camera",
            side_effect=camera_ai.AiError(
                409,
                missing_payload["error"],
                missing_payload,
            ),
        ) as retry_call,
    ):
        first, _ = _post(auth_client, gate_operator, wagon, "entry", request_id)
        second, _ = _post(auth_client, gate_operator, wagon, "entry", request_id)
        third, _ = _post(auth_client, gate_operator, wagon, "entry", request_id)

    assert first.status_code == 503
    assert first.data["retryable"] is True
    assert second.status_code == third.status_code == 409
    assert second.data["code"] == "vehicle_capture_window_missed"
    assert second.data["retryable"] is False
    read_scale.assert_called_once()
    create_call.assert_called_once()
    retry_call.assert_called_once()
    capture = PassageWeightCapture.objects.get(idempotency_key=request_id)
    assert capture.status == PassageWeightCapture.FAILED
    assert not WeighingRecord.objects.filter(wagon=wagon).exists()


def test_gateway_502_without_retry_hint_reuses_saved_scale_sample(
    auth_client,
    gate_operator,
):
    wagon = _passage()
    request_id = uuid4()
    def replay(_camera, key, stable_weight_at):
        return _recognized(key, stable_weight_at)

    with (
        patch.object(scale, "read_truck_scale", return_value=_reading()) as read_scale,
        patch.object(
            camera_ai,
            "recognize_vehicle_from_camera",
            side_effect=camera_ai.AiError(502, "bad gateway", {}),
        ) as create_call,
        patch.object(
            camera_ai,
            "retry_vehicle_recognition_from_camera",
            side_effect=replay,
        ) as retry_call,
    ):
        first, _ = _post(auth_client, gate_operator, wagon, "entry", request_id)
        second, _ = _post(auth_client, gate_operator, wagon, "entry", request_id)

    assert first.status_code == 502
    assert first.data["retryable"] is True
    assert second.status_code == 200, second.data
    assert read_scale.call_count == 1
    create_call.assert_called_once()
    retry_call.assert_called_once()
    assert WeighingRecord.objects.filter(wagon=wagon).count() == 1


def test_terminal_no_match_keeps_wagon_untouched_and_same_key_is_cached(
    auth_client,
    gate_operator,
):
    wagon = _passage()
    request_id = uuid4()
    payload = {
        "ok": False,
        "status": "no_match",
        "error": "vehicle number was not confirmed inside the ROI",
        "retryable": False,
    }
    with (
        patch.object(scale, "read_truck_scale", return_value=_reading()) as read_scale,
        patch.object(
            camera_ai,
            "recognize_vehicle_from_camera",
            side_effect=camera_ai.AiError(422, payload["error"], payload),
        ) as recognize_call,
    ):
        first, _ = _post(auth_client, gate_operator, wagon, "entry", request_id)
        second, _ = _post(auth_client, gate_operator, wagon, "entry", request_id)

    assert first.status_code == second.status_code == 422
    assert first.data["code"] == "vehicle_plate_not_confirmed"
    assert read_scale.call_count == recognize_call.call_count == 1
    wagon.refresh_from_db()
    assert wagon.status == st.ARRIVED
    assert wagon.number == ""
    assert wagon.gross_weight_kg is None
    assert not wagon.weighings.exists()
    capture = PassageWeightCapture.objects.get(idempotency_key=request_id)
    assert capture.status == PassageWeightCapture.FAILED


def test_processing_response_retries_same_post_without_second_scale_read(
    auth_client,
    gate_operator,
):
    wagon = _passage()
    request_id = uuid4()
    processing_payload = {
        "ok": False,
        "status": "processing",
        "request_id": str(request_id),
        "retryable": True,
    }

    def replay(_camera, key, stable_weight_at):
        return _recognized(key, stable_weight_at)

    with (
        patch.object(scale, "read_truck_scale", return_value=_reading()) as read_scale,
        patch.object(
            camera_ai,
            "recognize_vehicle_from_camera",
            side_effect=camera_ai.AiError(
                202, "processing", processing_payload
            ),
        ) as create_call,
        patch.object(
            camera_ai,
            "retry_vehicle_recognition_from_camera",
            side_effect=replay,
        ) as retry_call,
    ):
        first, _ = _post(auth_client, gate_operator, wagon, "entry", request_id)
        second, _ = _post(auth_client, gate_operator, wagon, "entry", request_id)

    assert first.status_code == 409
    assert first.data["code"] == "vehicle_recognition_pending"
    assert first.data["retryable"] is True
    assert second.status_code == 200
    assert read_scale.call_count == 1
    create_call.assert_called_once()
    retry_call.assert_called_once()


def test_plate_mismatch_rolls_back_weight_and_number(
    auth_client,
    gate_operator,
):
    wagon = _passage(number="777XYZ01")

    def recognize(_camera, key, stable_weight_at):
        return _recognized(key, stable_weight_at, number="123ABC02")

    with (
        patch.object(scale, "read_truck_scale", return_value=_reading()),
        patch.object(
            camera_ai,
            "recognize_vehicle_from_camera",
            side_effect=recognize,
        ),
    ):
        response, request_id = _post(auth_client, gate_operator, wagon, "entry")

    assert response.status_code == 409
    assert response.data["code"] == "vehicle_plate_mismatch"
    wagon.refresh_from_db()
    assert wagon.number == "777XYZ01"
    assert wagon.status == st.ARRIVED
    assert wagon.gross_weight_kg is None
    assert not wagon.weighings.exists()
    assert PassageWeightCapture.objects.get(
        idempotency_key=request_id
    ).status == PassageWeightCapture.FAILED


def test_exit_weight_rule_rolls_back_weighing_record(
    auth_client,
    gate_operator,
):
    wagon = _passage(number="123ABC02", status=st.AT_SILO, entry_weight=20_000)

    def recognize(_camera, key, stable_weight_at):
        return _recognized(key, stable_weight_at)

    with (
        patch.object(scale, "read_truck_scale", return_value=_reading("19000")),
        patch.object(
            camera_ai,
            "recognize_vehicle_from_camera",
            side_effect=recognize,
        ),
    ):
        response, _request_id = _post(auth_client, gate_operator, wagon, "exit")

    assert response.status_code == 400
    assert response.data["code"] == "bad_exit_weight"
    wagon.refresh_from_db()
    assert wagon.status == st.AT_SILO
    assert wagon.tare_weight_kg is None
    assert not wagon.weighings.exists()


def test_missing_or_noncanonical_idempotency_key_fails_before_hardware(
    auth_client,
    gate_operator,
):
    wagon = _passage()
    client = auth_client(gate_operator)
    with (
        patch.object(scale, "read_truck_scale") as read_scale,
        patch.object(camera_ai, "recognize_vehicle_from_camera") as recognize,
    ):
        missing = client.post(
            f"/api/grain/wagons/{wagon.pk}/entry-weight/", {}, format="json"
        )
        uppercase = client.post(
            f"/api/grain/wagons/{wagon.pk}/entry-weight/",
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid4()).upper(),
        )
        malformed = client.post(
            f"/api/grain/wagons/{wagon.pk}/entry-weight/",
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY="not-a-uuid",
        )

    assert missing.status_code == uppercase.status_code == malformed.status_code == 400
    assert missing.data["code"] == "idempotency_key_required"
    assert uppercase.data["code"] == "idempotency_key_invalid"
    assert malformed.data["code"] == "idempotency_key_invalid"
    read_scale.assert_not_called()
    recognize.assert_not_called()


def test_new_browser_request_is_told_to_resume_the_server_capture(
    auth_client,
    gate_operator,
):
    wagon = _passage()
    active_request_id = uuid4()
    PassageWeightCapture.objects.create(
        idempotency_key=active_request_id,
        wagon=wagon,
        wagon_id_snapshot=wagon.pk,
        action=PassageWeightCapture.ENTRY,
        wagon_status_before=wagon.status,
        stage=PassageWeightCapture.RECOGNIZING,
        camera="cam1",
        stable_weight_at=timezone.now(),
        weight_kg=12_000,
        retryable=True,
    )

    with (
        patch.object(scale, "read_truck_scale") as read_scale,
        patch.object(camera_ai, "recognize_vehicle_from_camera") as recognize,
    ):
        response, new_request_id = _post(
            auth_client,
            gate_operator,
            wagon,
            "entry",
        )

    assert new_request_id != active_request_id
    assert response.status_code == 409
    assert response.data["code"] == "passage_capture_resume_required"
    assert response.data["request_id"] == str(active_request_id)
    assert response.data["retryable"] is True
    read_scale.assert_not_called()
    recognize.assert_not_called()


def test_upstream_service_auth_error_is_terminal_502_not_user_401(
    auth_client,
    gate_operator,
):
    wagon = _passage()
    request_id = uuid4()
    payload = {
        "ok": False,
        "status": "failed",
        "error": "unauthorized",
        "retryable": False,
    }
    with (
        patch.object(scale, "read_truck_scale", return_value=_reading()) as read_scale,
        patch.object(
            camera_ai,
            "recognize_vehicle_from_camera",
            side_effect=camera_ai.AiError(401, "unauthorized", payload),
        ) as recognize,
    ):
        first, _ = _post(auth_client, gate_operator, wagon, "entry", request_id)
        second, _ = _post(auth_client, gate_operator, wagon, "entry", request_id)

    assert first.status_code == second.status_code == 502
    assert first.data["code"] == "vehicle_recognition_auth_failed"
    assert first.data["retryable"] is False
    assert read_scale.call_count == recognize.call_count == 1
    capture = PassageWeightCapture.objects.get(idempotency_key=request_id)
    assert capture.status == PassageWeightCapture.FAILED


def test_malformed_success_is_terminal_and_never_replays_hardware(
    auth_client,
    gate_operator,
):
    wagon = _passage()
    request_id = uuid4()
    with (
        patch.object(scale, "read_truck_scale", return_value=_reading()) as read_scale,
        patch.object(
            camera_ai,
            "recognize_vehicle_from_camera",
            side_effect=camera_ai.AiProtocolError("bad success payload"),
        ) as recognize,
    ):
        first, _ = _post(auth_client, gate_operator, wagon, "entry", request_id)
        second, _ = _post(auth_client, gate_operator, wagon, "entry", request_id)

    assert first.status_code == second.status_code == 502
    assert first.data["code"] == "vehicle_recognition_malformed"
    assert first.data["retryable"] is False
    assert read_scale.call_count == recognize.call_count == 1
    assert not wagon.weighings.exists()


def test_failure_stores_only_bounded_ai_diagnostics(
    auth_client,
    gate_operator,
):
    wagon = _passage()
    request_id = uuid4()
    payload = {
        "ok": False,
        "status": "camera_unavailable",
        "camera": "cam1",
        "source": "main",
        "error": "camera offline",
        "retryable": False,
        "frames_scanned": 10**9,
        "ambiguous_frames": 4,
        "private_frame_dump": "must-not-be-stored",
    }
    with (
        patch.object(scale, "read_truck_scale", return_value=_reading()),
        patch.object(
            camera_ai,
            "recognize_vehicle_from_camera",
            side_effect=camera_ai.AiError(503, "camera offline", payload),
        ),
    ):
        response, _ = _post(auth_client, gate_operator, wagon, "entry", request_id)

    assert response.status_code == 503
    assert response.data["retryable"] is False
    capture = PassageWeightCapture.objects.get(idempotency_key=request_id)
    assert capture.ai_payload_json["status"] == "camera_unavailable"
    assert capture.ai_payload_json["frames_scanned"] == 1_000_000
    assert capture.ai_payload_json["ambiguous_frames"] == 4
    assert "private_frame_dump" not in capture.ai_payload_json


def test_wagon_detail_limits_capture_audit_to_latest_ten(
    auth_client,
    gate_operator,
):
    wagon = _passage()
    request_ids = []
    for index in range(12):
        request_id = uuid4()
        request_ids.append(str(request_id))
        PassageWeightCapture.objects.create(
            idempotency_key=request_id,
            wagon=wagon,
            wagon_id_snapshot=wagon.pk,
            action=PassageWeightCapture.ENTRY,
            wagon_status_before=wagon.status,
            status=PassageWeightCapture.FAILED,
            stage=PassageWeightCapture.DONE,
            camera="cam1",
            error_code=f"failure_{index}",
            completed_at=timezone.now(),
        )

    response = auth_client(gate_operator).get(f"/api/grain/wagons/{wagon.pk}/")

    assert response.status_code == 200
    captures = response.data["vehicle_recognition_captures"]
    assert len(captures) == 10
    assert captures[0]["request_id"] == request_ids[-1]
    assert captures[-1]["request_id"] == request_ids[-10]


def test_stale_claim_without_scale_sample_becomes_terminal_without_hardware(
    auth_client,
    gate_operator,
):
    wagon = _passage()
    request_id = uuid4()
    capture = PassageWeightCapture.objects.create(
        idempotency_key=request_id,
        wagon=wagon,
        wagon_id_snapshot=wagon.pk,
        action=PassageWeightCapture.ENTRY,
        wagon_status_before=wagon.status,
        camera="cam1",
    )
    PassageWeightCapture.objects.filter(pk=capture.pk).update(
        updated_at=timezone.now() - timedelta(minutes=3)
    )

    with (
        patch.object(scale, "read_truck_scale") as read_scale,
        patch.object(camera_ai, "recognize_vehicle_from_camera") as recognize,
    ):
        response, _ = _post(auth_client, gate_operator, wagon, "entry", request_id)

    assert response.status_code == 409
    assert response.data["code"] == "passage_capture_interrupted"
    assert response.data["retryable"] is False
    read_scale.assert_not_called()
    recognize.assert_not_called()


def test_stale_recognition_resumes_saved_sample_without_second_scale_read(
    auth_client,
    gate_operator,
):
    wagon = _passage()
    request_id = uuid4()
    stable_weight_at = timezone.now()
    capture = PassageWeightCapture.objects.create(
        idempotency_key=request_id,
        wagon=wagon,
        wagon_id_snapshot=wagon.pk,
        action=PassageWeightCapture.ENTRY,
        wagon_status_before=wagon.status,
        stage=PassageWeightCapture.RECOGNIZING,
        camera="cam1",
        stable_weight_at=stable_weight_at,
        weight_kg=12_000,
    )
    PassageWeightCapture.objects.filter(pk=capture.pk).update(
        updated_at=timezone.now() - timedelta(minutes=3)
    )

    def recognize(_camera, key, trigger):
        return _recognized(key, trigger)

    with (
        patch.object(scale, "read_truck_scale") as read_scale,
        patch.object(
            camera_ai,
            "retry_vehicle_recognition_from_camera",
            side_effect=recognize,
        ) as recognize_call,
    ):
        response, _ = _post(auth_client, gate_operator, wagon, "entry", request_id)

    assert response.status_code == 200, response.data
    read_scale.assert_not_called()
    recognize_call.assert_called_once()
    assert WeighingRecord.objects.filter(wagon=wagon).count() == 1


def test_applying_resume_skips_scale_and_ocr(
    auth_client,
    gate_operator,
):
    wagon = _passage()
    request_id = uuid4()
    capture = PassageWeightCapture.objects.create(
        idempotency_key=request_id,
        wagon=wagon,
        wagon_id_snapshot=wagon.pk,
        action=PassageWeightCapture.ENTRY,
        wagon_status_before=wagon.status,
        stage=PassageWeightCapture.APPLYING,
        camera="cam1",
        camera_source="main",
        stable_weight_at=timezone.now(),
        weight_kg=12_000,
        vehicle_number="123ABC02",
        recognized_at=timezone.now(),
        confirmation_votes=3,
        detector_confidence=Decimal("0.91"),
        ocr_confidence=Decimal("0.96"),
    )
    PassageWeightCapture.objects.filter(pk=capture.pk).update(
        updated_at=timezone.now() - timedelta(minutes=3)
    )

    with (
        patch.object(scale, "read_truck_scale") as read_scale,
        patch.object(camera_ai, "recognize_vehicle_from_camera") as recognize,
    ):
        response, _ = _post(auth_client, gate_operator, wagon, "entry", request_id)

    assert response.status_code == 200, response.data
    assert response.data["number"] == "123ABC02"
    read_scale.assert_not_called()
    recognize.assert_not_called()
    assert WeighingRecord.objects.filter(wagon=wagon).count() == 1


def test_concurrent_completion_wins_over_local_camera_error(
    auth_client,
    gate_operator,
):
    wagon = _passage()
    request_id = uuid4()

    def complete_elsewhere(capture_id, **_kwargs):
        capture = PassageWeightCapture.objects.get(pk=capture_id)
        capture.stage = PassageWeightCapture.APPLYING
        capture.vehicle_number = "123ABC02"
        capture.camera_source = "main"
        capture.recognized_at = timezone.now()
        capture.confirmation_votes = 3
        capture.detector_confidence = Decimal("0.91")
        capture.ocr_confidence = Decimal("0.96")
        capture.save(
            update_fields=[
                "stage",
                "vehicle_number",
                "camera_source",
                "recognized_at",
                "confirmation_votes",
                "detector_confidence",
                "ocr_confidence",
                "updated_at",
            ]
        )
        vehicle_weight_capture._apply_capture(capture_id, gate_operator)
        return PassageWeightCapture.objects.get(pk=capture_id)

    with (
        patch.object(scale, "read_truck_scale", return_value=_reading()) as read_scale,
        patch.object(
            camera_ai,
            "recognize_vehicle_from_camera",
            side_effect=camera_ai.AiUnavailable("connection reset"),
        ),
        patch.object(
            vehicle_weight_capture,
            "_finish_capture_error",
            side_effect=complete_elsewhere,
        ),
    ):
        response, _ = _post(auth_client, gate_operator, wagon, "entry", request_id)

    assert response.status_code == 200, response.data
    assert response.data["number"] == "123ABC02"
    assert response.data["status"] == st.AT_SILO
    read_scale.assert_called_once()
    assert WeighingRecord.objects.filter(wagon=wagon).count() == 1
