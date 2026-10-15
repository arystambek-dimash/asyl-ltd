from unittest.mock import patch

import pytest
from django.core.cache import cache

from apps.cameras import ai, services

pytestmark = pytest.mark.django_db

URL = "/api/cameras/cam3/visibility-zone/"
POINTS = [{"x": 0.05, "y": 0.2}, {"x": 0.95, "y": 0.2}, {"x": 0.95, "y": 1.0}, {"x": 0.05, "y": 1.0}]
ZONE = {"cam": "cam3", "configured": True, "enabled": True, "source": "main", "coordinate_space": "normalized",
        "points": POINTS, "updated_at": "2026-10-15T07:00:00+00:00"}
BODY = {"points": POINTS, "enabled": True, "source": "main"}


@pytest.fixture(autouse=True)
def camera_pc_enabled(monkeypatch):
    monkeypatch.setattr(ai, "enabled", lambda: True)


@pytest.fixture
def manager(user_with_perms):
    return user_with_perms("zone-manager", codes=["sys_permissions.manage"])


def test_only_superuser_reads_and_saves_the_zone(auth_client, manager):
    with patch.object(ai, "visibility_zone") as read, patch.object(ai, "save_visibility_zone") as save:
        assert auth_client(manager).get(URL).status_code == 403
        assert auth_client(manager).put(URL, BODY, format="json").status_code == 403
    read.assert_not_called()
    save.assert_not_called()


def test_get_projects_the_camera_pc_zone(auth_client, admin_user):
    with patch.object(ai, "visibility_zone", return_value={**ZONE, "secret": "x"}) as read:
        response = auth_client(admin_user).get(URL)
    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    assert response.data == ZONE
    read.assert_called_once_with("cam3")


@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    [
        (ai.AiUnavailable("Connection refused to 10.0.0.5"), 502, "ai_unavailable"),
        (ai.AiError(404, "not found"), 404, "visibility_zone_unsupported"),
        (ai.AiError(500, "boom"), 502, "ai_error"),
    ],
)
def test_get_maps_camera_pc_failures(auth_client, admin_user, error, status_code, code):
    with patch.object(ai, "visibility_zone", side_effect=error):
        response = auth_client(admin_user).get(URL)
    assert (response.status_code, response.data["code"]) == (status_code, code)
    assert "10.0.0.5" not in response.data["detail"]


def test_get_rejects_a_zone_of_another_camera(auth_client, admin_user):
    with patch.object(ai, "visibility_zone", return_value={**ZONE, "cam": "cam4"}):
        response = auth_client(admin_user).get(URL)
    assert (response.status_code, response.data["code"]) == (502, "ai_invalid_response")


def test_get_rejects_a_direct_camera_path(auth_client, admin_user):
    with patch.object(ai, "visibility_zone") as read:
        response = auth_client(admin_user).get("/api/cameras/cam_8c28/visibility-zone/")
    assert response.status_code == 400
    read.assert_not_called()


def test_put_saves_and_patches_the_cached_inventory(auth_client, admin_user):
    snapshot = [{"id": "nvr:3", "src": "cam3", "visibility_zone": None}, {"id": "nvr:4", "src": "cam4"}]
    cache.set(services.CACHE_KEY, snapshot)
    cache.set(services.LAST_GOOD_CACHE_KEY, snapshot)
    upstream = {"ok": True, "saved": True, "applied_to_monitor": True, **ZONE}
    with patch.object(ai, "save_visibility_zone", return_value=(200, upstream)) as save:
        response = auth_client(admin_user).put(URL, BODY, format="json")
    assert response.status_code == 200
    assert response.data == {"saved": True, "applied_to_monitor": True, "zone": ZONE}
    assert save.call_args.args == ("cam3", {"points": POINTS, "enabled": True, "source": "main"})
    for key in (services.CACHE_KEY, services.LAST_GOOD_CACHE_KEY):
        cam3, cam4 = cache.get(key)
        assert cam3["visibility_zone"] == ZONE
        assert "visibility_zone" not in cam4


def test_put_can_switch_the_mask_off(auth_client, admin_user):
    upstream = {"saved": True, "applied_to_monitor": True, **ZONE, "enabled": False}
    with patch.object(ai, "save_visibility_zone", return_value=(200, upstream)) as save:
        response = auth_client(admin_user).put(URL, {**BODY, "enabled": False}, format="json")
    assert response.status_code == 200
    assert response.data["zone"]["enabled"] is False
    assert save.call_args.args[1]["enabled"] is False


def test_put_keeps_a_saved_zone_the_processor_has_not_applied(auth_client, admin_user):
    upstream = {"saved": True, "applied_to_monitor": False, **ZONE}
    with patch.object(ai, "save_visibility_zone", return_value=(503, upstream)):
        response = auth_client(admin_user).put(URL, BODY, format="json")
    assert response.status_code == 503
    assert response.data["saved"] is True and response.data["code"] == "zone_saved_refresh_pending"


def test_put_rejects_a_degenerate_polygon_without_calling_the_camera_pc(auth_client, admin_user):
    with patch.object(ai, "save_visibility_zone") as save:
        response = auth_client(admin_user).put(
            URL, {"points": [{"x": 0, "y": 0}, {"x": 0.5, "y": 0.5}, {"x": 1, "y": 1}]}, format="json"
        )
    assert (response.status_code, response.data["code"]) == (400, "invalid_visibility_zone")
    save.assert_not_called()


def test_put_does_not_touch_the_cache_when_the_camera_pc_refuses(auth_client, admin_user):
    cache.set(services.CACHE_KEY, [{"id": "nvr:3", "src": "cam3", "visibility_zone": None}])
    with patch.object(ai, "save_visibility_zone", return_value=(400, {"error": "bad polygon"})):
        response = auth_client(admin_user).put(URL, BODY, format="json")
    assert response.status_code == 400
    assert response.data == {"detail": "Некорректная зона видимости", "code": "ai_error"}
    assert cache.get(services.CACHE_KEY)[0]["visibility_zone"] is None
