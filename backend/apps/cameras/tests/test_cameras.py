from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.core.signing import TimestampSigner

from apps.cameras import ai, services
from apps.cameras.views import CAM_COOKIE

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clear_camera_cache(monkeypatch):
    # Инвентарь ai_service в юнитах выключен — тесты проб не должны зависеть
    # от env; инвентарные тесты включают его сами.
    monkeypatch.setattr(ai, "AI_KEY", "")
    cache.delete(services.CACHE_KEY)
    yield
    cache.delete(services.CACHE_KEY)


def fake_probe(statuses):
    """Мок _probe_path: camNsub → statuses[N-1], дальше absent."""
    def _probe(path):
        n = int(path.removeprefix("cam").removesuffix("sub"))
        return statuses[n - 1] if n <= len(statuses) else "absent"
    return _probe


INVENTORY = {
    "updated": "2026-07-09 18:23:30",
    "devices": [
        {"kind": "nvr-channel", "path": "cam2", "sub": "cam2sub", "channel": 2,
         "mac": "08:3b:c1:5e:8c:26", "model": "DS-2CD1643G2-LIZU", "online": True},
        {"kind": "nvr-channel", "path": "cam1", "sub": "cam1sub", "channel": 1,
         "mac": "08:3b:c1:5e:8c:27", "model": None, "online": False},
        {"kind": "direct", "path": "cam_8c28", "mac": "08:3b:c1:5e:8c:28",
         "model": "DS-2CD2043", "online": True},
        {"kind": "locked", "ip": "192.168.0.2", "note": "RTSP есть, ISAPI 401"},
    ],
}


def test_discover_prefers_inventory(monkeypatch):
    monkeypatch.setattr(ai, "AI_KEY", "k")
    with patch.object(ai, "inventory", return_value=INVENTORY), \
         patch.object(services, "_probe_path") as probe:
        cams = services.discover_cameras()
    probe.assert_not_called()
    assert [c["id"] for c in cams] == [
        "nvr:08:3b:c1:5e:8c:27", "nvr:08:3b:c1:5e:8c:26",
        "direct:08:3b:c1:5e:8c:28", "locked:192.168.0.2",
    ]
    cam1, cam2, direct, locked = cams
    assert (cam1["zone"], cam1["online"]) == ("Въезд / весы", False)
    assert (cam2["src"], cam2["name"]) == ("cam2", "DS-2CD1643G2-LIZU")
    assert direct["src"] == "cam_8c28"
    assert locked["src"] is None and locked["online"] is False
    assert "нет доступа" in locked["note"].lower() or "401" in locked["note"]


def test_discover_syncs_dynamic_streams_to_go2rtc(monkeypatch):
    monkeypatch.setattr(ai, "AI_KEY", "k")
    monkeypatch.setattr(services, "GO2RTC_API", "http://go2rtc:1984")
    with patch.object(ai, "inventory", return_value=INVENTORY), \
         patch.object(services, "_go2rtc_put") as put:
        services.discover_cameras()
    # cam1/cam2 — статик-слоты go2rtc.yaml; заявляется только direct-камера
    assert [c.args[0] for c in put.call_args_list] == [
        "cam_8c28src", "cam_8c28", "cam_8c28ai",
    ]


def test_discover_falls_back_to_probe_when_ai_down(monkeypatch):
    monkeypatch.setattr(ai, "AI_KEY", "k")
    monkeypatch.setattr(services, "CAMERA_PASS", "x")
    with patch.object(ai, "inventory", side_effect=ai.AiUnavailable("boom")), \
         patch.object(services, "_probe_path", side_effect=fake_probe(["online"])):
        cams = services.discover_cameras()
    assert [c["id"] for c in cams] == ["nvr:cam1"]


def test_discover_probe_returns_configured_cameras(monkeypatch):
    monkeypatch.setattr(services, "CAMERA_PASS", "x")
    with patch.object(services, "_probe_path", side_effect=fake_probe(["online", "offline", "online"])):
        cams = services.discover_cameras()
    assert [c["id"] for c in cams] == ["nvr:cam1", "nvr:cam2", "nvr:cam3"]
    assert cams[0]["src"] == "cam1"
    assert cams[0]["zone"] == "Въезд / весы"
    assert cams[1]["online"] is False  # offline: путь есть, источник лежит


def test_discover_probe_names_unknown_zones(monkeypatch):
    monkeypatch.setattr(services, "CAMERA_PASS", "x")
    statuses = ["online"] * 10
    with patch.object(services, "_probe_path", side_effect=fake_probe(statuses)):
        cams = services.discover_cameras()
    assert len(cams) == 10
    assert cams[9]["zone"] == "Камера 10"


def test_discover_caches_result(monkeypatch):
    monkeypatch.setattr(services, "CAMERA_PASS", "x")
    with patch.object(services, "_probe_path", side_effect=fake_probe(["online"])) as p:
        services.discover_cameras()
        services.discover_cameras()
    assert p.call_count == services.MAX_CAMERAS  # второй вызов — из кэша


def test_discover_without_password_is_empty(monkeypatch):
    monkeypatch.setattr(services, "CAMERA_PASS", "")
    assert services.discover_cameras() == []


def test_camera_list_for_staff(auth_client, operator):
    payload = [{"id": "nvr:cam1", "name": "Камера 1", "zone": "Въезд / весы",
                "src": "cam1", "kind": "nvr-channel", "online": True}]
    with patch.object(services, "discover_cameras", return_value=payload):
        resp = auth_client(operator).get("/api/cameras/")
    assert resp.status_code == 200
    assert resp.data == payload


def test_camera_list_denied_for_portal_client(auth_client, client_user):
    resp = auth_client(client_user).get("/api/cameras/")
    assert resp.status_code == 403


def test_camera_list_denied_anonymous(api_client):
    resp = api_client.get("/api/cameras/")
    assert resp.status_code == 401


def test_token_sets_cookie(auth_client, operator):
    resp = auth_client(operator).post("/api/cameras/token/")
    assert resp.status_code == 204
    cookie = resp.cookies.get(CAM_COOKIE)
    assert cookie is not None
    assert cookie["httponly"]


def test_token_denied_for_portal_client(auth_client, client_user):
    resp = auth_client(client_user).post("/api/cameras/token/")
    assert resp.status_code == 403


def test_auth_accepts_valid_cookie(api_client, operator):
    api_client.cookies[CAM_COOKIE] = TimestampSigner(salt="cameras").sign(str(operator.pk))
    resp = api_client.get("/api/cameras/auth/")
    assert resp.status_code == 204


def test_auth_rejects_missing_or_bad_cookie(api_client):
    assert api_client.get("/api/cameras/auth/").status_code == 403
    api_client.cookies[CAM_COOKIE] = "garbage"
    assert api_client.get("/api/cameras/auth/").status_code == 403
