"""Read-only vehicle-plate diagnostics exposed to the grain camera screen."""

from copy import deepcopy
from unittest.mock import patch

import pytest

from apps.cameras import ai

pytestmark = pytest.mark.django_db


MONITOR = {
    "cam": "cam1",
    "source": "main",
    "status": "online",
    "started_at": "2026-08-28T05:00:00.000+00:00",
    "last_frame_at": "2026-08-28T05:01:00.000+00:00",
    "last_inference_at": "2026-08-28T05:01:00.000+00:00",
    "last_confirmed_at": None,
    "last_vehicle_number": "123ABC02",
    "scanned_frames": 120,
    "plate_detections": 8,
    "stationary_admissions": 3,
    "ocr_attempts": 7,
    "confirmed_events": 1,
    "durable_duplicates": 0,
    "consecutive_errors": 0,
    "last_error": "private camera-PC detail",
    "inference_avg_ms": 21.5,
    "ocr_avg_ms": 13,
    "active_visit": {"vehicle_number": "123ABC02", "event_id": "private"},
    "capture": {"url": "rtsp://secret:password@camera/stream"},
    "stop_gate": {
        "dwell_seconds": 3,
        "min_frames": 6,
        "max_movement_ratio": 0.018,
        "exit_grace_seconds": 5,
    },
}

INFO = {
    "enabled": True,
    "ready": True,
    "task": "stationary_vehicle_plate_recognition",
    "model": {"path": r"C:\mediamtx\ai-service\models\vehicle-license-plate.pt"},
    "ocr_model": {"model_dir": r"C:\mediamtx\ai-service\models\ocr"},
    "automation": {
        "enabled": True,
        "configured_cameras": ["cam1"],
        "source": "main",
        "server_push_configured": True,
        "monitors": {"cam1": MONITOR},
    },
}

ROI = {
    "cam": "cam1",
    "configured": True,
    "enabled": True,
    "source": "main",
    "coordinate_space": "normalized",
    "points": [
        {"x": 0.38, "y": 0.2},
        {"x": 0.63, "y": 0.32},
        {"x": 0.98, "y": 1.0},
        {"x": 0.18, "y": 1.0},
    ],
    "updated_at": "2026-08-28T04:00:00.000+00:00",
}


@pytest.fixture(autouse=True)
def ai_key(monkeypatch):
    monkeypatch.setattr(ai, "AI_KEY", "test-key")


@pytest.fixture
def grain_viewer(user_with_perms):
    return user_with_perms(
        "vehicle-runtime-grain-viewer",
        codes=["grain.view"],
    )


def test_vehicle_runtime_ai_helpers_use_canonical_upstream_paths():
    with patch.object(ai, "_request", return_value=(200, INFO)) as request:
        assert ai.vehicle_number_info() == INFO
    request.assert_called_once_with(
        "GET",
        "/vehicle-number",
        None,
        timeout_seconds=ai.VEHICLE_RUNTIME_PROBE_TIMEOUT,
    )

    with patch.object(ai, "_request", return_value=(200, ROI)) as request:
        assert ai.vehicle_roi("cam1") == ROI
    request.assert_called_once_with(
        "GET",
        "/cameras/cam1/vehicle-roi",
        None,
        timeout_seconds=ai.VEHICLE_RUNTIME_PROBE_TIMEOUT,
    )


def test_vehicle_roi_rejects_noncanonical_camera_before_network():
    with patch.object(ai, "_request") as request, pytest.raises(ai.AiError):
        ai.vehicle_roi("1")
    request.assert_not_called()


def test_vehicle_runtime_requires_grain_view_permission(
    api_client,
    django_user_model,
):
    response = api_client.get("/api/cameras/cam1/vehicle-plate-runtime/")
    assert response.status_code in (401, 403)

    client_user = django_user_model.objects.create_user(
        username="vehicle-runtime-client",
        password="pass12345",
        is_client=True,
    )
    api_client.force_authenticate(client_user)
    response = api_client.get("/api/cameras/cam1/vehicle-plate-runtime/")
    assert response.status_code == 403

    staff_without_permission = django_user_model.objects.create_user(
        username="vehicle-runtime-staff",
        password="pass12345",
    )
    api_client.force_authenticate(staff_without_permission)
    response = api_client.get("/api/cameras/cam1/vehicle-plate-runtime/")
    assert response.status_code == 403


def test_vehicle_runtime_projects_safe_live_status_and_roi(api_client, grain_viewer):
    api_client.force_authenticate(grain_viewer)
    with (
        patch.object(ai, "vehicle_number_info", return_value=deepcopy(INFO)) as info,
        patch.object(ai, "vehicle_roi", return_value=deepcopy(ROI)) as roi,
    ):
        response = api_client.get("/api/cameras/cam1/vehicle-plate-runtime/")

    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    assert response.data["camera"] == "cam1"
    assert response.data["diagnostic"] == "online"
    assert response.data["monitor"]["plate_detections"] == 8
    assert response.data["monitor"]["stationary_admissions"] == 3
    assert response.data["monitor"]["ocr_attempts"] == 7
    assert response.data["monitor"]["stop_gate"]["dwell_seconds"] == 3.0
    assert response.data["roi"] == ROI
    rendered = repr(response.data)
    for private_value in (
        "private camera-PC detail",
        "rtsp://secret:password",
        "vehicle-license-plate.pt",
        "active_visit",
        "last_error",
        "capture",
        "123ABC02",
    ):
        assert private_value not in rendered
    info.assert_called_once_with()
    roi.assert_called_once_with("cam1")


@pytest.mark.parametrize(
    ("mutate", "diagnostic"),
    [
        (lambda value: value.update(enabled=False), "model_disabled"),
        (lambda value: value.update(ready=False), "model_not_ready"),
        (
            lambda value: value["automation"].update(enabled=False),
            "automation_disabled",
        ),
        (
            lambda value: value["automation"].update(configured_cameras=[]),
            "camera_not_configured",
        ),
        (
            lambda value: value["automation"].update(monitors={}),
            "monitor_missing",
        ),
    ],
)
def test_vehicle_runtime_explains_missing_pipeline_stage(
    api_client,
    grain_viewer,
    mutate,
    diagnostic,
):
    info = deepcopy(INFO)
    mutate(info)
    api_client.force_authenticate(grain_viewer)
    with (
        patch.object(ai, "vehicle_number_info", return_value=info),
        patch.object(ai, "vehicle_roi", return_value=deepcopy(ROI)),
    ):
        response = api_client.get("/api/cameras/cam1/vehicle-plate-runtime/")
    assert response.status_code == 200
    assert response.data["diagnostic"] == diagnostic


def test_vehicle_runtime_rejects_malformed_upstream_without_leaking_it(
    api_client,
    grain_viewer,
):
    malformed = deepcopy(INFO)
    malformed["automation"]["monitors"]["cam1"]["scanned_frames"] = "secret-bad-value"
    api_client.force_authenticate(grain_viewer)
    with (
        patch.object(ai, "vehicle_number_info", return_value=malformed),
        patch.object(ai, "vehicle_roi", return_value=deepcopy(ROI)),
    ):
        response = api_client.get("/api/cameras/cam1/vehicle-plate-runtime/")
    assert response.status_code == 502
    assert response["Cache-Control"] == "no-store"
    assert response.data == {
        "detail": "AI-сервис вернул некорректный статус модели",
        "code": "ai_invalid_response",
    }
    assert "secret-bad-value" not in repr(response.data)


@pytest.mark.parametrize(
    ("field", "value"),
    [("source", "sub"), ("cam", "cam2")],
)
def test_vehicle_runtime_rejects_monitor_identity_mismatch(
    api_client,
    grain_viewer,
    field,
    value,
):
    mismatched = deepcopy(INFO)
    mismatched["automation"]["monitors"]["cam1"][field] = value
    api_client.force_authenticate(grain_viewer)
    with (
        patch.object(ai, "vehicle_number_info", return_value=mismatched),
        patch.object(ai, "vehicle_roi", return_value=deepcopy(ROI)),
    ):
        response = api_client.get("/api/cameras/cam1/vehicle-plate-runtime/")

    assert response.status_code == 502
    assert response.data == {
        "detail": "AI-сервис вернул некорректный статус модели",
        "code": "ai_invalid_response",
    }


def test_vehicle_runtime_maps_unavailable_service_to_safe_no_store_error(
    api_client,
    grain_viewer,
):
    api_client.force_authenticate(grain_viewer)
    with patch.object(
        ai, "vehicle_number_info", side_effect=ai.AiUnavailable("secret host")
    ):
        response = api_client.get("/api/cameras/cam1/vehicle-plate-runtime/")
    assert response.status_code == 502
    assert response["Cache-Control"] == "no-store"
    assert response.data == {
        "detail": "AI-сервис камер недоступен",
        "code": "ai_unavailable",
    }
    assert "secret host" not in repr(response.data)


def test_vehicle_runtime_does_not_forward_upstream_error_detail(
    api_client,
    grain_viewer,
):
    api_client.force_authenticate(grain_viewer)
    with patch.object(
        ai,
        "vehicle_number_info",
        side_effect=ai.AiError(503, r"failed at C:\secret\model.pt"),
    ):
        response = api_client.get("/api/cameras/cam1/vehicle-plate-runtime/")
    assert response.status_code == 503
    assert response["Cache-Control"] == "no-store"
    assert response.data == {
        "detail": "Модель номеров временно недоступна",
        "code": "ai_error",
    }
    assert "secret" not in repr(response.data)
