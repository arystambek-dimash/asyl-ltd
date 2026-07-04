from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.core.signing import TimestampSigner

from apps.cameras import services
from apps.cameras.views import CAM_COOKIE

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clear_camera_cache():
    cache.delete(services.CACHE_KEY)
    yield
    cache.delete(services.CACHE_KEY)


def fake_probe(statuses):
    """Мок _probe_path: camNsub → statuses[N-1], дальше absent."""
    def _probe(path):
        n = int(path.removeprefix("cam").removesuffix("sub"))
        return statuses[n - 1] if n <= len(statuses) else "absent"
    return _probe


def test_discover_returns_configured_cameras(monkeypatch):
    monkeypatch.setattr(services, "CAMERA_PASS", "x")
    with patch.object(services, "_probe_path", side_effect=fake_probe(["online", "offline", "online"])):
        cams = services.discover_cameras()
    assert [c["id"] for c in cams] == [1, 2, 3]
    assert cams[0]["src"] == "cam1"
    assert cams[0]["zone"] == "Въезд / весы"


def test_discover_names_unknown_zones(monkeypatch):
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
    with patch.object(services, "discover_cameras", return_value=[
        {"id": 1, "name": "Камера 1", "zone": "Въезд / весы", "src": "cam1"},
    ]):
        resp = auth_client(operator).get("/api/cameras/")
    assert resp.status_code == 200
    assert resp.data == [{"id": 1, "name": "Камера 1", "zone": "Въезд / весы", "src": "cam1"}]


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
