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
    "on_demand": {
        "enabled": True,
        "cameras": ["cam1"],
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

SAVED_ROI = {
    "ok": True,
    "saved": True,
    "applied_to_monitor": True,
    **ROI,
    "private_debug": r"C:\secret\vehicle-rois.json",
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

    update = {"points": ROI["points"], "enabled": True, "source": "main"}
    with patch.object(ai, "_request", return_value=(200, SAVED_ROI)) as request:
        assert ai.save_vehicle_roi("cam1", update) == (200, SAVED_ROI)
    request.assert_called_once_with(
        "PUT",
        "/cameras/cam1/vehicle-roi",
        update,
        timeout_seconds=ai.VEHICLE_RUNTIME_PROBE_TIMEOUT,
    )


def test_vehicle_roi_rejects_noncanonical_camera_before_network():
    with patch.object(ai, "_request") as request, pytest.raises(ai.AiError):
        ai.vehicle_roi("1")
    request.assert_not_called()

    with patch.object(ai, "_request") as request, pytest.raises(ai.AiError):
        ai.save_vehicle_roi("1", {"points": ROI["points"]})
    request.assert_not_called()


def test_vehicle_runtime_requires_grain_view_permission(
    api_client,
    django_user_model,
):
    response = api_client.get("/api/cameras/vehicle-plate-runtime/")
    assert response.status_code in (401, 403)

    client_user = django_user_model.objects.create_user(
        username="vehicle-runtime-client",
        password="pass12345",
        is_client=True,
    )
    api_client.force_authenticate(client_user)
    response = api_client.get("/api/cameras/vehicle-plate-runtime/")
    assert response.status_code == 403

    staff_without_permission = django_user_model.objects.create_user(
        username="vehicle-runtime-staff",
        password="pass12345",
    )
    api_client.force_authenticate(staff_without_permission)
    response = api_client.get("/api/cameras/vehicle-plate-runtime/")
    assert response.status_code == 403


def test_vehicle_runtime_routes_split_read_and_roi_save(
    api_client,
    grain_viewer,
    admin_user,
):
    """Bootstrap URL only reads the configured camera; the <cam> URL only saves its ROI."""
    api_client.force_authenticate(admin_user)
    with patch.object(ai, "save_vehicle_roi") as save:
        response = api_client.put(
            "/api/cameras/vehicle-plate-runtime/",
            {"points": ROI["points"], "enabled": True, "source": "main"},
            format="json",
        )
    assert response.status_code == 405
    save.assert_not_called()

    api_client.force_authenticate(grain_viewer)
    with patch.object(ai, "vehicle_number_info") as info:
        response = api_client.get("/api/cameras/cam1/vehicle-plate-runtime/")
    assert response.status_code == 405
    info.assert_not_called()


def test_vehicle_runtime_projects_safe_live_status_and_roi(api_client, grain_viewer):
    api_client.force_authenticate(grain_viewer)
    with (
        patch.object(ai, "vehicle_number_info", return_value=deepcopy(INFO)) as info,
        patch.object(ai, "vehicle_roi", return_value=deepcopy(ROI)) as roi,
    ):
        response = api_client.get("/api/cameras/vehicle-plate-runtime/")

    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    assert response.data["camera"] == "cam1"
    assert response.data["source"] == "main"
    assert response.data["stream"] == "cam1main"
    assert response.data["monitor"]["plate_detections"] == 8
    assert response.data["monitor"]["stationary_admissions"] == 3
    assert response.data["monitor"]["ocr_attempts"] == 7
    assert set(response.data["monitor"]) == {
        "status",
        "source",
        "has_error",
        "scanned_frames",
        "plate_detections",
        "stationary_admissions",
        "ocr_attempts",
        "confirmed_events",
    }
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


def test_vehicle_runtime_projects_weight_first_readiness_without_legacy_monitor(
    api_client,
    grain_viewer,
    settings,
):
    settings.VEHICLE_PLATE_WEIGHT_FIRST_ENABLED = True
    settings.VEHICLE_PLATE_WEIGHT_FIRST_SOURCE = "main"
    info = deepcopy(INFO)
    info["automation"].update(enabled=False, configured_cameras=[], monitors={})
    api_client.force_authenticate(grain_viewer)
    with (
        patch.object(ai, "vehicle_number_info", return_value=info),
        patch.object(ai, "vehicle_roi", return_value=deepcopy(ROI)),
    ):
        response = api_client.get("/api/cameras/vehicle-plate-runtime/")

    assert response.status_code == 200
    assert response.data["weight_first_enabled"] is True
    assert response.data["on_demand_enabled"] is True
    assert response.data["on_demand_camera_configured"] is True
    assert response.data["monitor"] is None


def test_vehicle_runtime_projects_independent_automatic_scale_state(
    api_client,
    grain_viewer,
    settings,
):
    settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED = True
    settings.VEHICLE_PLATE_WEIGHT_FIRST_ENABLED = False
    settings.VEHICLE_PLATE_WEIGHT_FIRST_SOURCE = "sub"
    info = deepcopy(INFO)
    info["automation"].update(enabled=False, configured_cameras=[], monitors={})
    api_client.force_authenticate(grain_viewer)

    with (
        patch.object(ai, "vehicle_number_info", return_value=info),
        patch.object(ai, "vehicle_roi", return_value=deepcopy(ROI)),
    ):
        response = api_client.get("/api/cameras/vehicle-plate-runtime/")

    assert response.status_code == 200
    assert response.data["weight_first_enabled"] is False
    # The automatic scale alone switches the lane to the on-demand source; its
    # state is served only by /grain/automatic-passage-scale/runtime/.
    assert response.data["source"] == "sub"
    assert "scale_automation" not in response.data
    assert response.data["monitor"] is None


@pytest.mark.parametrize(
    ("configured_source", "expected_stream"),
    [("sub", "cam7"), ("main", "cam7main")],
)
def test_vehicle_runtime_bootstrap_uses_configured_weight_first_camera_and_source(
    api_client,
    grain_viewer,
    settings,
    configured_source,
    expected_stream,
):
    settings.VEHICLE_PLATE_WEIGHT_FIRST_ENABLED = True
    settings.VEHICLE_PLATE_WEIGHT_FIRST_CAMERA = "cam7"
    settings.VEHICLE_PLATE_WEIGHT_FIRST_SOURCE = configured_source
    info = deepcopy(INFO)
    info["automation"].update(enabled=False, configured_cameras=[], monitors={})
    info["on_demand"]["cameras"] = ["cam7"]
    roi_payload = {
        **deepcopy(ROI),
        "cam": "cam7",
        "source": configured_source,
    }
    api_client.force_authenticate(grain_viewer)

    with (
        patch.object(ai, "vehicle_number_info", return_value=info) as info_request,
        patch.object(ai, "vehicle_roi", return_value=roi_payload) as roi_request,
    ):
        response = api_client.get("/api/cameras/vehicle-plate-runtime/")

    assert response.status_code == 200
    assert response.data["camera"] == "cam7"
    assert response.data["source"] == configured_source
    assert response.data["stream"] == expected_stream
    assert response.data["on_demand_camera_configured"] is True
    info_request.assert_called_once_with()
    roi_request.assert_called_once_with("cam7")


def test_vehicle_runtime_weight_first_is_not_ready_for_wrong_roi_source(
    api_client,
    grain_viewer,
    settings,
):
    settings.VEHICLE_PLATE_WEIGHT_FIRST_ENABLED = True
    settings.VEHICLE_PLATE_WEIGHT_FIRST_CAMERA = "cam1"
    settings.VEHICLE_PLATE_WEIGHT_FIRST_SOURCE = "sub"
    info = deepcopy(INFO)
    info["automation"].update(enabled=False, configured_cameras=[], monitors={})
    api_client.force_authenticate(grain_viewer)

    with (
        patch.object(ai, "vehicle_number_info", return_value=info),
        patch.object(ai, "vehicle_roi", return_value=deepcopy(ROI)),
    ):
        response = api_client.get("/api/cameras/vehicle-plate-runtime/")

    assert response.status_code == 200
    assert response.data["source"] == "sub"
    assert response.data["stream"] == "cam1"
    assert response.data["roi"]["source"] == "main"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("scanned_frames", "secret-bad-value"),
        ("source", "sub"),
        ("cam", "cam2"),
    ],
)
def test_vehicle_runtime_rejects_malformed_monitor_without_leaking_it(
    api_client,
    grain_viewer,
    field,
    value,
):
    malformed = deepcopy(INFO)
    malformed["automation"]["monitors"]["cam1"][field] = value
    api_client.force_authenticate(grain_viewer)
    with (
        patch.object(ai, "vehicle_number_info", return_value=malformed),
        patch.object(ai, "vehicle_roi", return_value=deepcopy(ROI)),
    ):
        response = api_client.get("/api/cameras/vehicle-plate-runtime/")

    assert response.status_code == 502
    assert response["Cache-Control"] == "no-store"
    assert response.data == {
        "detail": "AI-сервис вернул некорректный статус модели",
        "code": "ai_invalid_response",
    }
    assert value not in repr(response.data)


def test_vehicle_runtime_maps_unavailable_service_to_safe_no_store_error(
    api_client,
    grain_viewer,
):
    api_client.force_authenticate(grain_viewer)
    with patch.object(
        ai, "vehicle_number_info", side_effect=ai.AiUnavailable("secret host")
    ):
        response = api_client.get("/api/cameras/vehicle-plate-runtime/")
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
        response = api_client.get("/api/cameras/vehicle-plate-runtime/")
    assert response.status_code == 503
    assert response["Cache-Control"] == "no-store"
    assert response.data == {
        "detail": "Модель номеров временно недоступна",
        "code": "ai_error",
    }
    assert "secret" not in repr(response.data)


def test_vehicle_roi_put_requires_superuser_and_is_never_cached(
    api_client,
    grain_viewer,
):
    api_client.force_authenticate(grain_viewer)
    with patch.object(ai, "save_vehicle_roi") as save:
        response = api_client.put(
            "/api/cameras/cam1/vehicle-plate-runtime/",
            {"points": ROI["points"], "enabled": True, "source": "main"},
            format="json",
        )

    assert response.status_code == 403
    assert response["Cache-Control"] == "no-store"
    save.assert_not_called()


def test_vehicle_roi_put_forwards_body_and_projects_safe_response(
    api_client,
    admin_user,
):
    api_client.force_authenticate(admin_user)
    body = {"points": ROI["points"], "enabled": True, "source": "main"}
    with patch.object(
        ai, "save_vehicle_roi", return_value=(200, deepcopy(SAVED_ROI))
    ) as save:
        response = api_client.put(
            "/api/cameras/cam1/vehicle-plate-runtime/", body, format="json"
        )

    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    assert response.data == {
        "saved": True,
        "applied_to_monitor": True,
        "roi": ROI,
    }
    assert "secret" not in repr(response.data)
    save.assert_called_once_with("cam1", body)


def test_vehicle_roi_put_uses_configured_weight_first_substream(
    api_client,
    admin_user,
    settings,
):
    settings.VEHICLE_PLATE_WEIGHT_FIRST_ENABLED = True
    settings.VEHICLE_PLATE_WEIGHT_FIRST_CAMERA = "cam7"
    settings.VEHICLE_PLATE_WEIGHT_FIRST_SOURCE = "sub"
    body = {"points": ROI["points"], "enabled": True, "source": "sub"}
    saved_roi = {
        **deepcopy(SAVED_ROI),
        "cam": "cam7",
        "source": "sub",
    }
    api_client.force_authenticate(admin_user)

    with patch.object(
        ai,
        "save_vehicle_roi",
        return_value=(200, saved_roi),
    ) as save:
        response = api_client.put(
            "/api/cameras/cam7/vehicle-plate-runtime/",
            body,
            format="json",
        )

    assert response.status_code == 200
    assert response.data["roi"]["source"] == "sub"
    save.assert_called_once_with("cam7", body)


def test_vehicle_roi_put_preserves_saved_refresh_pending_response(
    api_client,
    admin_user,
):
    api_client.force_authenticate(admin_user)
    pending = {
        **deepcopy(SAVED_ROI),
        "applied_to_monitor": False,
        "error": r"private monitor failure at C:\secret",
    }
    with patch.object(ai, "save_vehicle_roi", return_value=(503, pending)):
        response = api_client.put(
            "/api/cameras/cam1/vehicle-plate-runtime/",
            {"points": ROI["points"], "enabled": True, "source": "main"},
            format="json",
        )

    assert response.status_code == 503
    assert response["Cache-Control"] == "no-store"
    assert response.data["saved"] is True
    assert response.data["applied_to_monitor"] is False
    assert response.data["roi"] == ROI
    assert response.data["code"] == "roi_saved_refresh_pending"
    assert "secret" not in repr(response.data)


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"points": [[0, 0], [1, 1]]},
        {"points": [[0, 0], [0.5, 0.5], [1, 1]]},
        {"points": [[False, 0], [1, 0], [1, 1]]},
        {"points": [[0, 0], [1.1, 0], [1, 1]]},
        {"points": [[0, 0], [1, 0], [1, 1]], "enabled": 1},
        {"points": [[0, 0], [1, 0], [1, 1]], "source": 1},
        {"points": [[0, 0], [1, 0], [1, 1]], "source": "sub"},
        {
            "points": [[0, 0], [1, 0], [1, 1]],
            "coordinate_space": "normalized",
        },
    ],
)
def test_vehicle_roi_put_rejects_invalid_body_before_network(
    api_client,
    admin_user,
    body,
):
    api_client.force_authenticate(admin_user)
    with patch.object(ai, "save_vehicle_roi") as save:
        response = api_client.put(
            "/api/cameras/cam1/vehicle-plate-runtime/", body, format="json"
        )

    assert response.status_code == 400
    assert response["Cache-Control"] == "no-store"
    assert response.data == {
        "detail": "Некорректная область распознавания",
        "code": "invalid_vehicle_roi",
    }
    save.assert_not_called()


def test_vehicle_roi_put_maps_a_non_json_upstream_400_like_a_json_one(
    api_client,
    admin_user,
):
    """A non-JSON 400 from the camera PC is the same rejection, not «unknown camera»."""
    api_client.force_authenticate(admin_user)
    with patch.object(
        ai, "save_vehicle_roi", side_effect=ai.AiError(400, "AI-сервис: ошибка 400")
    ):
        response = api_client.put(
            "/api/cameras/cam1/vehicle-plate-runtime/",
            {"points": ROI["points"], "enabled": True, "source": "main"},
            format="json",
        )

    assert response.status_code == 400
    assert response.data == {
        "detail": "Некорректная область распознавания",
        "code": "ai_error",
    }


def test_vehicle_roi_put_expects_weight_first_source_only_on_its_camera(
    api_client,
    admin_user,
    settings,
):
    settings.VEHICLE_PLATE_WEIGHT_FIRST_ENABLED = False
    settings.VEHICLE_PLATE_AUTO_SCALE_ENABLED = True
    settings.VEHICLE_PLATE_WEIGHT_FIRST_CAMERA = "cam7"
    settings.VEHICLE_PLATE_WEIGHT_FIRST_SOURCE = "sub"
    api_client.force_authenticate(admin_user)
    saved = {**deepcopy(SAVED_ROI), "cam": "cam7", "source": "sub"}
    with patch.object(ai, "save_vehicle_roi", return_value=(200, saved)) as save:
        weight_camera = api_client.put(
            "/api/cameras/cam7/vehicle-plate-runtime/",
            {"points": ROI["points"], "enabled": True},
            format="json",
        )
        other_camera = api_client.put(
            "/api/cameras/cam1/vehicle-plate-runtime/",
            {"points": ROI["points"], "enabled": True, "source": "sub"},
            format="json",
        )

    save.assert_called_once()
    assert save.call_args.args[1]["source"] == "sub"
    assert weight_camera.status_code == 200
    assert weight_camera.data["roi"]["source"] == "sub"
    assert other_camera.status_code == 400
    assert other_camera.data["code"] == "invalid_vehicle_roi"


def test_vehicle_roi_put_defaults_omitted_source_to_main(
    api_client,
    admin_user,
):
    api_client.force_authenticate(admin_user)
    body = {"points": ROI["points"], "enabled": True}
    expected = {**body, "source": "main"}
    with patch.object(
        ai, "save_vehicle_roi", return_value=(200, deepcopy(SAVED_ROI))
    ) as save:
        response = api_client.put(
            "/api/cameras/cam1/vehicle-plate-runtime/", body, format="json"
        )

    assert response.status_code == 200
    save.assert_called_once_with("cam1", expected)


@pytest.mark.parametrize(
    "saved",
    [
        {
            **SAVED_ROI,
            "points": "private malformed response",
            "error": r"C:\secret\vehicle-rois.json",
        },
        {**SAVED_ROI, "source": "sub"},
    ],
    ids=["malformed", "non_main_source"],
)
def test_vehicle_roi_put_maps_invalid_saved_response_to_safe_502(
    api_client,
    admin_user,
    saved,
):
    api_client.force_authenticate(admin_user)
    with patch.object(ai, "save_vehicle_roi", return_value=(200, deepcopy(saved))):
        response = api_client.put(
            "/api/cameras/cam1/vehicle-plate-runtime/",
            {"points": ROI["points"], "enabled": True, "source": "main"},
            format="json",
        )

    assert response.status_code == 502
    assert response["Cache-Control"] == "no-store"
    assert response.data == {
        "detail": "AI-сервис вернул некорректный результат сохранения ROI",
        "code": "ai_invalid_response",
    }
    assert "secret" not in repr(response.data)
