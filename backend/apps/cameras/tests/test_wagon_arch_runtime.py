from unittest.mock import patch

import pytest

from apps.cameras import ai

pytestmark = pytest.mark.django_db

ZONE = {"cam": "cam8", "configured": True, "enabled": True, "source": "main", "coordinate_space": "normalized",
        "points": [{"x": 0.1, "y": 0.3}, {"x": 0.9, "y": 0.3}, {"x": 0.9, "y": 0.7}, {"x": 0.1, "y": 0.7}],
        "updated_at": "2026-09-14T05:00:00+00:00"}
MOTION = {"cam": "cam8", "source": "main", "status": "online", "state": "still", "still_seconds": 12.5,
          "direction": "right", "moving_fraction": 0.01, "sample_age_seconds": 0.3, "samples": 40}


@pytest.fixture
def viewer(user_with_perms):
    return user_with_perms("arch-viewer", codes=["grain.view"])


@pytest.fixture
def superuser(make_user):
    user = make_user("arch-admin")
    user.is_superuser = True
    user.save(update_fields=["is_superuser"])
    return user


def test_runtime_projects_zone_motion_and_importer_state(auth_client, viewer, settings):
    settings.WAGON_ARCH_AUTOMATION_ENABLED = True
    with patch.object(ai, "enabled", return_value=True), patch.object(ai, "arch_zone", return_value=ZONE), \
            patch.object(ai, "arch_motion", return_value=MOTION), \
            patch("apps.grain.wagon_arch.runtime", return_value={"enabled": True, "camera": "cam8", "collector": None,
                                                                 "pending_stops": 0, "attention_stops": 0, "last_stop": None, "updated_at": None}):
        response = auth_client(viewer).get("/api/cameras/wagon-arch-runtime/")
    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    assert (response.data["camera"], response.data["stream"], response.data["automation_enabled"]) == ("cam8", "cam8", True)
    assert response.data["zone"]["points"] == ZONE["points"]
    assert response.data["motion"] == {"state": "still", "still_seconds": 12.5, "direction": "right", "status": "online", "sample_age_seconds": 0.3}
    assert response.data["runtime"]["camera"] == "cam8" and response.data["diagnostic"] == ""


def test_runtime_rejects_an_unknown_camera_cleanly(auth_client, viewer):
    response = auth_client(viewer).get("/api/cameras/not-a-camera/wagon-arch-runtime/")
    assert response.status_code == 400
    assert response.data["code"] == "ai_error"
    assert response["Cache-Control"] == "no-store"


def test_runtime_degrades_when_the_camera_pc_is_off(auth_client, viewer):
    with patch.object(ai, "enabled", return_value=False):
        response = auth_client(viewer).get("/api/cameras/cam8/wagon-arch-runtime/")
    assert response.status_code == 200
    assert response.data["zone"]["configured"] is False and response.data["motion"] is None
    assert response.data["diagnostic"] == "AI-сервис камер не настроен"


def test_runtime_reports_motion_errors_without_hiding_the_zone(auth_client, viewer):
    with patch.object(ai, "enabled", return_value=True), patch.object(ai, "arch_zone", return_value=ZONE), \
            patch.object(ai, "arch_motion", side_effect=ai.AiError(404, "not configured")):
        response = auth_client(viewer).get("/api/cameras/wagon-arch-runtime/")
    assert response.status_code == 200
    assert response.data["zone"]["configured"] is True and response.data["motion"] is None
    assert response.data["diagnostic"].startswith("Движение недоступно")


def test_runtime_diagnostic_hides_raw_camera_pc_unavailable_detail(auth_client, viewer):
    """AiUnavailable's str() may carry the camera-PC host/connection error — never show it raw."""
    with patch.object(ai, "enabled", return_value=True), \
            patch.object(ai, "arch_zone", side_effect=ai.AiUnavailable("Connection refused to 10.0.0.5:8443")), \
            patch.object(ai, "arch_motion", return_value=MOTION):
        response = auth_client(viewer).get("/api/cameras/wagon-arch-runtime/")
    assert response.status_code == 200
    assert response.data["diagnostic"] == "Зона арки недоступна: ПК камер недоступен"
    assert "10.0.0.5" not in response.data["diagnostic"]


def test_runtime_diagnostic_reports_a_malformed_motion_payload_in_russian(auth_client, viewer):
    """VehicleRuntimeContractError's internal English message must not reach the user."""
    with patch.object(ai, "enabled", return_value=True), patch.object(ai, "arch_zone", return_value=ZONE), \
            patch.object(ai, "arch_motion", return_value={"state": "sideways", "still_seconds": 1}):
        response = auth_client(viewer).get("/api/cameras/wagon-arch-runtime/")
    assert response.status_code == 200
    assert response.data["diagnostic"] == "Движение недоступно: ПК камер вернул некорректный ответ"


def test_runtime_diagnostic_uses_the_sanitised_ai_error_detail(auth_client, viewer):
    """ai.AiError.detail is already a safe, pre-sanitised string — use it as-is."""
    with patch.object(ai, "enabled", return_value=True), patch.object(ai, "arch_zone", return_value=ZONE), \
            patch.object(ai, "arch_motion", side_effect=ai.AiError(404, "AI-сервис: ошибка 404")):
        response = auth_client(viewer).get("/api/cameras/wagon-arch-runtime/")
    assert response.status_code == 200
    assert response.data["diagnostic"] == "Движение недоступно: AI-сервис: ошибка 404"


def test_runtime_diagnostic_logs_the_raw_exception_server_side(auth_client, viewer, caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="apps.cameras.api_views.wagon_arch_runtime"):
        with patch.object(ai, "enabled", return_value=True), \
                patch.object(ai, "arch_zone", side_effect=ai.AiUnavailable("Connection refused to 10.0.0.5:8443")), \
                patch.object(ai, "arch_motion", return_value=MOTION):
            auth_client(viewer).get("/api/cameras/wagon-arch-runtime/")
    assert any("10.0.0.5" in record.getMessage() for record in caplog.records)


def test_put_requires_superuser_and_delegates_to_the_camera_pc(auth_client, viewer, superuser):
    body = {"points": ZONE["points"], "enabled": True, "source": "main"}
    assert auth_client(viewer).put("/api/cameras/cam8/wagon-arch-runtime/", body, format="json").status_code == 403
    upstream = {"ok": True, "saved": True, "applied_to_monitor": True, **ZONE}
    with patch.object(ai, "enabled", return_value=True), patch.object(ai, "save_arch_zone", return_value=(200, upstream)) as save:
        response = auth_client(superuser).put("/api/cameras/cam8/wagon-arch-runtime/", body, format="json")
    assert response.status_code == 200
    assert response.data["saved"] is True and response.data["applied_to_monitor"] is True
    assert response.data["zone"]["points"] == ZONE["points"]
    assert save.call_args.args[0] == "cam8" and save.call_args.args[1]["points"] == ZONE["points"]
    with patch.object(ai, "enabled", return_value=True), patch.object(ai, "save_arch_zone", return_value=(503, {**upstream, "applied_to_monitor": False, "error": "monitor unavailable"})):
        pending = auth_client(superuser).put("/api/cameras/cam8/wagon-arch-runtime/", body, format="json")
    assert pending.status_code == 503 and pending.data["saved"] is True and pending.data["code"] == "zone_saved_refresh_pending"
    with patch.object(ai, "enabled", return_value=True):
        assert auth_client(superuser).put("/api/cameras/cam8/wagon-arch-runtime/", {"points": [{"x": 0, "y": 0}]}, format="json").data["code"] == "invalid_arch_zone"


def test_put_maps_camera_pc_unavailable_to_a_502(auth_client, superuser):
    body = {"points": ZONE["points"], "enabled": True, "source": "main"}
    with patch.object(ai, "enabled", return_value=True), \
            patch.object(ai, "save_arch_zone", side_effect=ai.AiUnavailable("Connection refused to 10.0.0.5:8443")):
        response = auth_client(superuser).put("/api/cameras/cam8/wagon-arch-runtime/", body, format="json")
    assert response.status_code == 502
    assert response.data["code"] == "ai_unavailable"
    assert "10.0.0.5" not in response.data["detail"]


def test_put_maps_an_upstream_500_to_a_502_ai_error(auth_client, superuser):
    body = {"points": ZONE["points"], "enabled": True, "source": "main"}
    with patch.object(ai, "enabled", return_value=True), \
            patch.object(ai, "save_arch_zone", return_value=(500, {"error": "internal error"})):
        response = auth_client(superuser).put("/api/cameras/cam8/wagon-arch-runtime/", body, format="json")
    assert response.status_code == 502
    assert response.data["code"] == "ai_error"
