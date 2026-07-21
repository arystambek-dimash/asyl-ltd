from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.core.signing import TimestampSigner

from apps.cameras import ai, services
from apps.cameras.models import AiCountingSession, MonoblockCameraSettings
from apps.cameras.views import CAM_COOKIE

pytestmark = pytest.mark.django_db


@pytest.fixture
def superuser(django_user_model):
    return django_user_model.objects.create_superuser(
        username="camera-root",
        password="test",
    )


@pytest.fixture(autouse=True)
def clear_camera_cache(monkeypatch):
    # Инвентарь ai_service в юнитах выключен — тесты проб не должны зависеть
    # от env; инвентарные тесты включают его сами.
    monkeypatch.setattr(ai, "AI_KEY", "")
    cache.delete(services.CACHE_KEY)
    cache.delete(services.LAST_GOOD_CACHE_KEY)
    yield
    cache.delete(services.CACHE_KEY)
    cache.delete(services.LAST_GOOD_CACHE_KEY)


def fake_probe(statuses):
    """Мок _probe_path: camNsub → statuses[N-1], дальше absent."""
    def _probe(path):
        n = int(path.removeprefix("cam").removesuffix("sub"))
        return statuses[n - 1] if n <= len(statuses) else "absent"
    return _probe


INVENTORY = {
    "updated": "2026-07-09 18:23:30",
    "devices": [
        {"kind": "nvr-channel", "path": "cam10", "sub": "cam10sub", "channel": 10,
         "mac": "08:3b:c1:5e:8c:29", "model": None, "online": True},
        {"kind": "nvr-channel", "path": "cam2", "sub": "cam2sub", "channel": 2,
         "mac": "08:3b:c1:5e:8c:26", "model": "DS-2CD1643G2-LIZU", "online": True},
        {"kind": "nvr-channel", "path": "cam1", "sub": "cam1sub", "channel": 1,
         "mac": "08:3b:c1:5e:8c:27", "model": None, "online": False},
        {"kind": "direct", "path": "cam_8c28", "mac": "08:3b:c1:5e:8c:28",
         "model": "DS-2CD2043", "online": True},
        {"kind": "locked", "ip": "192.168.0.2", "note": "RTSP есть, ISAPI 401"},
    ],
    "line_configs": {
        "cam2": {
            "configured": True,
            "coordinate_space": "normalized",
            "line": {"x1": 0.08, "y1": 0.61, "x2": 0.93, "y2": 0.58},
            "line_spec": "0.08,0.61,0.93,0.58",
            "direction": "negative",
        },
    },
}


def test_discover_prefers_inventory(monkeypatch):
    monkeypatch.setattr(ai, "AI_KEY", "k")
    with patch.object(ai, "inventory", return_value=INVENTORY), \
         patch.object(services, "_probe_path") as probe:
        cams = services.discover_cameras()
    probe.assert_not_called()
    # натуральный порядок: cam10 после cam2, не между cam1 и cam2
    assert [c["id"] for c in cams] == [
        "nvr:08:3b:c1:5e:8c:27", "nvr:08:3b:c1:5e:8c:26", "nvr:08:3b:c1:5e:8c:29",
        "direct:08:3b:c1:5e:8c:28", "locked:192.168.0.2",
    ]
    cam1, cam2, _cam10, direct, locked = cams
    assert (cam1["zone"], cam1["online"]) == ("Въезд / весы", False)
    assert (cam2["src"], cam2["name"]) == ("cam2", "DS-2CD1643G2-LIZU")
    assert cam2["line_config"] == INVENTORY["line_configs"]["cam2"]
    assert cam1["line_config"] is None
    assert direct["src"] == "cam_8c28"
    assert locked["src"] is None and locked["online"] is False
    assert "нет доступа" in locked["note"].lower() or "401" in locked["note"]


def test_discover_syncs_dynamic_streams_to_go2rtc(monkeypatch):
    monkeypatch.setattr(ai, "AI_KEY", "k")
    monkeypatch.setattr(services, "GO2RTC_API", "http://go2rtc:1984")
    with patch.object(ai, "inventory", return_value=INVENTORY), \
         patch.object(services, "_go2rtc_put") as put:
        services.discover_cameras()
    # cam1/cam2 — статик-слоты go2rtc.yaml; заявляется только direct-камера:
    # нативный сабпоток + ffmpeg-запаска (транскод лишь при чужом кодеке)
    assert [c.args for c in put.call_args_list] == [
        ("cam_8c28",
         f"rtsp://{services.CAMERA_USER}:{services.CAMERA_PASS}"
         f"@{services.CAMERA_HOST}:{services.CAMERA_PORT}/cam_8c28",
         "ffmpeg:cam_8c28#video=h264"),
        ("cam_8c28ai",
         f"rtsp://{services.CAMERA_USER}:{services.CAMERA_PASS}"
         f"@{services.CAMERA_HOST}:{services.CAMERA_PORT}/cam_8c28ai"),
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


def test_discover_preserves_last_good_topology_during_total_outage(monkeypatch):
    monkeypatch.setattr(services, "CAMERA_PASS", "x")
    with patch.object(services, "_probe_path", side_effect=fake_probe(["online", "online"])):
        first = services.discover_cameras()
    cache.delete(services.CACHE_KEY)
    with patch.object(services, "_probe_path", return_value="absent"):
        during_outage = services.discover_cameras()

    assert [camera["id"] for camera in during_outage] == [camera["id"] for camera in first]
    assert all(camera["online"] is False for camera in during_outage)
    assert all("переподключ" in camera["note"].lower() for camera in during_outage)


def test_empty_discovery_uses_short_cache_ttl(monkeypatch):
    monkeypatch.setattr(services, "CAMERA_PASS", "")
    with patch.object(cache, "set", wraps=cache.set) as cache_set:
        assert services.discover_cameras() == []
    assert cache_set.call_args_list[-1].args == (
        services.CACHE_KEY,
        [],
        services.EMPTY_CACHE_TTL,
    )


def test_camera_list_for_staff(auth_client, operator):
    line_config = INVENTORY["line_configs"]["cam2"]
    payload = [{"id": "nvr:cam1", "name": "Камера 1", "zone": "Въезд / весы",
                "src": "cam1", "kind": "nvr-channel", "online": True,
                "line_config": line_config}]
    with patch.object(services, "discover_cameras", return_value=payload):
        resp = auth_client(operator).get("/api/cameras/")
    assert resp.status_code == 200
    assert resp.data == payload
    assert resp.data[0]["line_config"] == line_config


def test_admin_camera_name_is_returned_everywhere(auth_client, boss, operator):
    payload = [{"id": "nvr:cam1", "name": "Камера 1", "zone": "Въезд / весы",
                "src": "cam1", "kind": "nvr-channel", "online": True}]
    with patch.object(services, "discover_cameras", return_value=payload):
        response = auth_client(boss).patch(
            "/api/cameras/",
            {"camera": "cam1", "name": "  Главные   ворота  "},
            format="json",
        )
        assert response.status_code == 200
        assert response.data == {"camera": "cam1", "name": "Главные ворота"}

        response = auth_client(operator).get("/api/cameras/")

    assert response.status_code == 200
    assert response.data[0]["zone"] == "Главные ворота"
    row = MonoblockCameraSettings.objects.get(singleton=True)
    assert row.camera_names == {"cam1": "Главные ворота"}
    assert row.updated_by == boss


def test_admin_can_rename_direct_camera_without_enabling_it_for_ai(auth_client, boss):
    response = auth_client(boss).patch(
        "/api/cameras/",
        {"camera": "cam_8c28", "name": "Боковой склад"},
        format="json",
    )

    assert response.status_code == 200
    assert response.data == {"camera": "cam_8c28", "name": "Боковой склад"}
    assert MonoblockCameraSettings.objects.get().camera_names == {
        "cam_8c28": "Боковой склад"
    }


def test_operator_cannot_rename_camera(auth_client, operator):
    response = auth_client(operator).patch(
        "/api/cameras/",
        {"camera": "cam1", "name": "Новое имя"},
        format="json",
    )

    assert response.status_code == 403
    assert not MonoblockCameraSettings.objects.exists()


def test_camera_list_denied_for_portal_client(auth_client, client_user):
    resp = auth_client(client_user).get("/api/cameras/")
    assert resp.status_code == 403


def test_camera_list_denied_anonymous(api_client):
    resp = api_client.get("/api/cameras/")
    assert resp.status_code == 401


def test_admin_configures_monoblock_camera_allowlist(auth_client, boss, operator):
    response = auth_client(boss).put(
        "/api/cameras/monoblock-settings/",
        {"camera_sources": ["2", "cam3", "cam3"]},
        format="json",
    )
    assert response.status_code == 200
    assert response.data["camera_sources"] == ["cam2", "cam3"]
    row = MonoblockCameraSettings.objects.get(singleton=True)
    assert row.camera_sources == ["cam2", "cam3"]
    assert row.updated_by == boss

    response = auth_client(operator).get("/api/cameras/monoblock-settings/")
    assert response.status_code == 200
    assert response.data["camera_sources"] == ["cam2", "cam3"]


def test_shipping_board_settings_default_to_today(auth_client, operator):
    response = auth_client(operator).get("/api/cameras/shipping-settings/")

    assert response.status_code == 200
    assert response.data["completed_orders_days"] == 1
    assert response.data["video_retention_days"] == 14


def test_admin_changes_completed_order_retention(auth_client, boss, operator):
    response = auth_client(boss).patch(
        "/api/cameras/shipping-settings/",
        {"completed_orders_days": 7},
        format="json",
    )

    assert response.status_code == 200
    assert response.data["completed_orders_days"] == 7
    row = MonoblockCameraSettings.objects.get(singleton=True)
    assert row.completed_orders_days == 7
    assert row.updated_by == boss

    denied = auth_client(operator).patch(
        "/api/cameras/shipping-settings/",
        {"completed_orders_days": 3},
        format="json",
    )
    assert denied.status_code == 403


@pytest.mark.parametrize("value", [0, 91, "bad", True])
def test_shipping_board_retention_is_validated(auth_client, boss, value):
    response = auth_client(boss).patch(
        "/api/cameras/shipping-settings/",
        {"completed_orders_days": value},
        format="json",
    )
    assert response.status_code == 400


def test_operator_cannot_change_monoblock_camera_allowlist(auth_client, operator):
    response = auth_client(operator).put(
        "/api/cameras/monoblock-settings/",
        {"camera_sources": ["cam2"]},
        format="json",
    )

    assert response.status_code == 403
    assert not MonoblockCameraSettings.objects.exists()


def test_always_on_settings_are_superuser_only(
    auth_client, superuser, boss, operator, client_user, monkeypatch,
):
    for user in (boss, operator, client_user):
        response = auth_client(user).get("/api/cameras/always-on-settings/")
        assert response.status_code == 403

    monkeypatch.setattr(ai, "AI_KEY", "k")
    live = {
        "cameras": ["cam2"],
        "source": "sub",
        "capacity": 2,
        "processors": [{
            "cam": "cam2", "running": True, "mode": "always_on",
            "recording": False, "total": 14,
        }],
    }
    with patch.object(ai, "always_on_status", return_value={
        "cameras": [], "source": "sub", "capacity": 2, "processors": [],
    }), patch.object(ai, "configure_always_on", return_value=live) as configure:
        response = auth_client(superuser).put(
            "/api/cameras/always-on-settings/",
            {"camera_sources": ["2", "cam2"]},
            format="json",
        )

    assert response.status_code == 200
    assert response.data["camera_sources"] == ["cam2"]
    assert response.data["processors"][0]["recording"] is False
    configure.assert_called_once_with(["cam2"], "sub")
    row = MonoblockCameraSettings.objects.get(singleton=True)
    assert row.always_on_camera_sources == ["cam2"]
    assert row.updated_by == superuser
    # 24/7 — фоновый режим камеры, а не отгрузка: он не создаёт владельца,
    # сессию заказа или какую-либо camera binding в CRM.
    assert AiCountingSession.objects.count() == 0


def test_always_on_choice_survives_camera_pc_outage(
    auth_client, superuser, monkeypatch,
):
    monkeypatch.setattr(ai, "AI_KEY", "k")
    with patch.object(
        ai, "always_on_status", side_effect=ai.AiUnavailable("offline"),
    ), patch.object(
        ai, "configure_always_on", side_effect=ai.AiUnavailable("offline"),
    ):
        response = auth_client(superuser).put(
            "/api/cameras/always-on-settings/",
            {"camera_sources": ["cam3"]},
            format="json",
        )

    assert response.status_code == 202
    assert response.data["sync_status"] == "pending"
    assert MonoblockCameraSettings.always_on_sources() == ["cam3"]


def test_superuser_cannot_exceed_camera_pc_processor_capacity(
    auth_client, superuser, monkeypatch,
):
    monkeypatch.setattr(ai, "AI_KEY", "k")
    with patch.object(ai, "always_on_status", return_value={
        "cameras": [], "source": "sub", "capacity": 1, "processors": [],
    }), patch.object(ai, "configure_always_on") as configure:
        response = auth_client(superuser).put(
            "/api/cameras/always-on-settings/",
            {"camera_sources": ["cam2", "cam3"]},
            format="json",
        )

    assert response.status_code == 400
    configure.assert_not_called()
    assert MonoblockCameraSettings.always_on_sources() == []


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
