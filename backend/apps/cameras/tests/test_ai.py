"""Прокси AI-подсчёта мешков: маппинг ответов ai_service и права доступа."""
from unittest.mock import patch

import pytest

from apps.cameras import ai

pytestmark = pytest.mark.django_db

RUNNING = {
    "cam": "cam2", "running": True, "stream": "cam2ai", "status": "онлайн",
    "fps": 19.8, "total": 42, "weight": 2100, "per_color": {"Blue_50": 40},
}


@pytest.fixture(autouse=True)
def ai_key(monkeypatch):
    monkeypatch.setattr(ai, "AI_KEY", "test-key")


@pytest.fixture
def loader(user_with_perms):
    return user_with_perms("loader", codes=["shipping.load"])


# --- клиент ---------------------------------------------------------------

def test_status_none_when_not_running():
    with patch.object(ai, "_request", return_value=(404, {"detail": "not running"})):
        assert ai.status("cam2") is None


def test_stop_returns_final_before_delete():
    calls = []

    def fake(method, path, body=None):
        calls.append((method, path))
        return (200, RUNNING) if method == "GET" else (200, {})

    with patch.object(ai, "_request", side_effect=fake):
        final = ai.stop("cam2")
    assert final["total"] == 42
    assert calls == [("GET", "/processors/cam2"), ("DELETE", "/processors/cam2")]


def test_stop_when_not_running_skips_delete():
    with patch.object(ai, "_request", return_value=(404, {})) as req:
        assert ai.stop("cam2") is None
    assert req.call_count == 1  # только GET, DELETE не дёргаем


def test_normalize_accepts_known_shapes():
    assert ai.normalize("2") == "cam2"          # номер канала NVR
    assert ai.normalize("cam2") == "cam2"
    assert ai.normalize("cam_8c26") == "cam_8c26"  # direct по хвосту MAC


@pytest.mark.parametrize("bad", ["token", "cam/../x", "cam" + "x" * 20, ""])
def test_bad_camera_name_rejected_locally(bad):
    with pytest.raises(ai.AiError):  # до сервиса не ходим
        ai.status(bad)


# --- вьюхи ----------------------------------------------------------------

def test_get_status_maps_404_to_not_running(api_client, operator):
    api_client.force_authenticate(operator)
    with patch.object(ai, "_request", return_value=(404, {})):
        resp = api_client.get("/api/cameras/cam2/ai/")
    assert resp.status_code == 200
    assert resp.data == {"running": False}


def test_start_attaches_to_running_without_reset(api_client, loader):
    api_client.force_authenticate(loader)

    def fake(method, path, body=None):
        assert method == "GET"  # повторный POST к сервису не уходит
        return 200, RUNNING

    with patch.object(ai, "_request", side_effect=fake):
        resp = api_client.post("/api/cameras/cam2/ai/")
    assert resp.status_code == 200
    assert resp.data["total"] == 42


def test_start_when_idle_posts_to_service(api_client, loader):
    api_client.force_authenticate(loader)

    def fake(method, path, body=None):
        if method == "GET":
            return 404, {}
        assert (method, path) == ("POST", "/processors/cam2")
        return 200, {**RUNNING, "total": 0, "status": "запуск..."}

    with patch.object(ai, "_request", side_effect=fake):
        resp = api_client.post("/api/cameras/cam2/ai/")
    assert resp.status_code == 200
    assert resp.data["total"] == 0


def test_delete_returns_final_with_running_false(api_client, loader):
    api_client.force_authenticate(loader)

    def fake(method, path, body=None):
        return (200, RUNNING) if method == "GET" else (200, {})

    with patch.object(ai, "_request", side_effect=fake):
        resp = api_client.delete("/api/cameras/cam2/ai/")
    assert resp.status_code == 200
    assert resp.data["total"] == 42
    assert resp.data["running"] is False


def test_limit_409_passes_through(api_client, loader):
    api_client.force_authenticate(loader)

    def fake(method, path, body=None):
        if method == "GET":
            return 404, {}
        return 409, {"detail": "лимит камер"}

    with patch.object(ai, "_request", side_effect=fake):
        resp = api_client.post("/api/cameras/cam2/ai/")
    assert resp.status_code == 409
    assert "лимит" in resp.data["detail"]


def test_unavailable_maps_to_502(api_client, operator):
    api_client.force_authenticate(operator)
    with patch.object(ai, "_request", side_effect=ai.AiUnavailable("boom")):
        resp = api_client.get("/api/cameras/cam2/ai/")
    assert resp.status_code == 502
    assert resp.data["code"] == "ai_unavailable"


def test_disabled_without_key(api_client, operator, monkeypatch):
    monkeypatch.setattr(ai, "AI_KEY", "")
    api_client.force_authenticate(operator)
    resp = api_client.get("/api/cameras/cam2/ai/")
    assert resp.status_code == 503
    assert resp.data["code"] == "ai_disabled"


def test_mutations_require_shipping_load(api_client, make_user):
    staff = make_user("plain-staff")  # сотрудник без прав поста
    api_client.force_authenticate(staff)
    with patch.object(ai, "_request", return_value=(200, RUNNING)):
        assert api_client.get("/api/cameras/cam2/ai/").status_code == 200  # смотреть можно
        assert api_client.post("/api/cameras/cam2/ai/").status_code == 403
        assert api_client.delete("/api/cameras/cam2/ai/").status_code == 403
        assert api_client.post("/api/cameras/cam2/ai/reset/").status_code == 403


def test_clients_cannot_even_read(api_client, make_user):
    portal_client = make_user("portal", client=True)
    api_client.force_authenticate(portal_client)
    resp = api_client.get("/api/cameras/cam2/ai/")
    assert resp.status_code == 403
