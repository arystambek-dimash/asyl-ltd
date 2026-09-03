import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from django.core import signing
from django.core.cache import cache
from django.db import connections
from django.test import override_settings

from apps.cameras import ai, continuous, services
from apps.cameras.models import (
    ANALYTICS_SCOPE_AI247,
    ANALYTICS_SCOPE_SHIPPING,
    AiCountingSession,
    ContinuousCameraRole,
    MonoblockCameraSettings,
    MonoblockDevice,
)
from apps.cameras.views import (
    CAM_COOKIE,
    CAM_TOKEN_AUDIENCE,
    CAM_TOKEN_MAX_AGE,
    CAM_TOKEN_SALT,
    CAM_TOKEN_VERSION,
)
from apps.clients.models import Client
from apps.orders.models import Order

pytestmark = pytest.mark.django_db

STREAM_URI = "/go2rtc/api/ws?src=cam2"


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


def _stream_token(auth_client, user):
    response = auth_client(user).post("/api/cameras/token/")
    assert response.status_code == 204
    return response.cookies[CAM_COOKIE].value


def _authorize_stream(api_client, token, uri=STREAM_URI):
    api_client.cookies[CAM_COOKIE] = token
    headers = {} if uri is None else {"HTTP_X_ORIGINAL_URI": uri}
    return api_client.get("/api/cameras/auth/", **headers)


def fake_probe(statuses):
    """Мок _probe_path: camNsub → statuses[N-1], дальше absent."""

    def _probe(path):
        n = int(path.removeprefix("cam").removesuffix("sub"))
        return statuses[n - 1] if n <= len(statuses) else "absent"

    return _probe


INVENTORY = {
    "updated": "2026-07-09 18:23:30",
    "devices": [
        {
            "kind": "nvr-channel",
            "path": "cam10",
            "sub": "cam10sub",
            "channel": 10,
            "mac": "08:3b:c1:5e:8c:29",
            "model": None,
            "online": True,
        },
        {
            "kind": "nvr-channel",
            "path": "cam2",
            "sub": "cam2sub",
            "channel": 2,
            "mac": "08:3b:c1:5e:8c:26",
            "model": "DS-2CD1643G2-LIZU",
            "online": True,
        },
        {
            "kind": "nvr-channel",
            "path": "cam1",
            "sub": "cam1sub",
            "channel": 1,
            "mac": "08:3b:c1:5e:8c:27",
            "model": None,
            "online": False,
        },
        {
            "kind": "direct",
            "path": "cam_8c28",
            "mac": "08:3b:c1:5e:8c:28",
            "model": "DS-2CD2043",
            "online": True,
        },
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
    with (
        patch.object(ai, "inventory", return_value=INVENTORY),
        patch.object(services, "_probe_path") as probe,
    ):
        cams = services.discover_cameras()
    probe.assert_not_called()
    # натуральный порядок: cam10 после cam2, не между cam1 и cam2
    assert [c["id"] for c in cams] == [
        "nvr:08:3b:c1:5e:8c:27",
        "nvr:08:3b:c1:5e:8c:26",
        "nvr:08:3b:c1:5e:8c:29",
        "direct:08:3b:c1:5e:8c:28",
        "locked:192.168.0.2",
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
    with (
        patch.object(ai, "inventory", return_value=INVENTORY),
        patch.object(services, "_go2rtc_put") as put,
    ):
        services.discover_cameras()
    # cam1/cam2 — статик-слоты go2rtc.yaml; заявляется только direct-камера:
    # нативный сабпоток + ffmpeg-запаска (транскод лишь при чужом кодеке)
    assert [c.args for c in put.call_args_list] == [
        (
            "cam_8c28",
            f"rtsp://{services.CAMERA_USER}:{services.CAMERA_PASS}"
            f"@{services.CAMERA_HOST}:{services.CAMERA_PORT}/cam_8c28",
            "ffmpeg:cam_8c28#video=h264",
        ),
        (
            "cam_8c28ai",
            f"rtsp://{services.CAMERA_USER}:{services.CAMERA_PASS}"
            f"@{services.CAMERA_HOST}:{services.CAMERA_PORT}/cam_8c28ai",
        ),
    ]


def test_discover_falls_back_to_probe_when_ai_down(monkeypatch):
    monkeypatch.setattr(ai, "AI_KEY", "k")
    monkeypatch.setattr(services, "CAMERA_PASS", "x")
    with (
        patch.object(ai, "inventory", side_effect=ai.AiUnavailable("boom")),
        patch.object(services, "_probe_path", side_effect=fake_probe(["online"])),
    ):
        cams = services.discover_cameras()
    assert [c["id"] for c in cams] == ["nvr:cam1"]


def test_discover_probe_returns_configured_cameras(monkeypatch):
    monkeypatch.setattr(services, "CAMERA_PASS", "x")
    with patch.object(
        services, "_probe_path", side_effect=fake_probe(["online", "offline", "online"])
    ):
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
    with patch.object(
        services, "_probe_path", side_effect=fake_probe(["online", "online"])
    ):
        first = services.discover_cameras()
    cache.delete(services.CACHE_KEY)
    with patch.object(services, "_probe_path", return_value="absent"):
        during_outage = services.discover_cameras()

    assert [camera["id"] for camera in during_outage] == [
        camera["id"] for camera in first
    ]
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
    payload = [
        {
            "id": "nvr:cam1",
            "name": "Камера 1",
            "zone": "Въезд / весы",
            "src": "cam1",
            "kind": "nvr-channel",
            "online": True,
            "line_config": line_config,
        }
    ]
    with patch.object(services, "discover_cameras", return_value=payload):
        resp = auth_client(operator).get("/api/cameras/")
    assert resp.status_code == 200
    assert resp.data == payload
    assert resp.data[0]["line_config"] == line_config


def test_admin_camera_name_is_returned_everywhere(auth_client, boss, operator):
    payload = [
        {
            "id": "nvr:cam1",
            "name": "Камера 1",
            "zone": "Въезд / весы",
            "src": "cam1",
            "kind": "nvr-channel",
            "online": True,
        }
    ]
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
    assert response.status_code == 202
    assert response.data["camera_sources"] == ["cam2", "cam3"]
    assert response.data["always_on_camera_sources"] == ["cam2", "cam3"]
    assert response.data["always_on_source"] == "sub"
    row = MonoblockCameraSettings.objects.get(singleton=True)
    assert row.camera_sources == ["cam2", "cam3"]
    assert row.updated_by == boss

    response = auth_client(operator).get("/api/cameras/monoblock-settings/")
    assert response.status_code == 200
    assert response.data["camera_sources"] == ["cam2", "cam3"]


def test_continuous_sources_and_roles_are_stable_and_disjoint(
    django_user_model,
):
    row = MonoblockCameraSettings.objects.create(
        camera_sources=["cam3", "cam2", "cam3"],
        always_on_camera_sources=["cam4"],
    )
    active_user = django_user_model.objects.create_user(username="mono-cam5")
    inactive_user = django_user_model.objects.create_user(username="mono-cam6")
    MonoblockDevice.objects.create(
        user=active_user,
        name="Активный",
        camera_source="cam5",
        is_active=True,
    )
    MonoblockDevice.objects.create(
        user=inactive_user,
        name="Отключённый",
        camera_source="cam6",
        is_active=False,
    )
    ContinuousCameraRole.objects.bulk_create(
        [
            ContinuousCameraRole(
                camera=camera,
                analytics_scope=ANALYTICS_SCOPE_SHIPPING,
            )
            for camera in ("cam2", "cam3", "cam5")
        ]
        + [
            ContinuousCameraRole(
                camera="cam4",
                analytics_scope=ANALYTICS_SCOPE_AI247,
            )
        ]
    )

    assert MonoblockCameraSettings.shipping_sources(row) == [
        "cam3",
        "cam2",
        "cam5",
    ]
    assert MonoblockCameraSettings.ai247_sources(row) == ["cam4"]
    assert MonoblockCameraSettings.continuous_sources(row) == [
        "cam3",
        "cam2",
        "cam5",
        "cam4",
    ]
    assert MonoblockCameraSettings.continuous_roles(row) == {
        "cam3": ANALYTICS_SCOPE_SHIPPING,
        "cam2": ANALYTICS_SCOPE_SHIPPING,
        "cam5": ANALYTICS_SCOPE_SHIPPING,
        "cam4": ANALYTICS_SCOPE_AI247,
    }


def test_shipping_picker_cannot_claim_an_ai247_camera_atomically(
    auth_client,
    boss,
):
    row = MonoblockCameraSettings.objects.create(
        camera_sources=["cam2"],
        always_on_camera_sources=["cam4"],
    )

    with patch.object(ai, "configure_always_on") as configure:
        response = auth_client(boss).put(
            "/api/cameras/monoblock-settings/",
            {"camera_sources": ["cam2", "cam4"]},
            format="json",
        )

    assert response.status_code == 409
    assert response.data["code"] == "camera_role_immutable"
    assert response.data["detail"]["cameras"] == ["cam4"]
    configure.assert_not_called()
    row.refresh_from_db()
    assert row.camera_sources == ["cam2"]
    assert row.always_on_camera_sources == ["cam4"]


def test_monoblock_camera_save_reconciles_effective_substream_policy(
    auth_client,
    boss,
    monkeypatch,
):
    MonoblockCameraSettings.objects.create(always_on_camera_sources=["cam4"])
    monkeypatch.setattr(ai, "AI_KEY", "k")
    live = {
        "cameras": ["cam2", "cam3", "cam4"],
        "source": "sub",
        "analytics_scopes": {
            "cam2": ANALYTICS_SCOPE_SHIPPING,
            "cam3": ANALYTICS_SCOPE_SHIPPING,
            "cam4": ANALYTICS_SCOPE_AI247,
        },
        "capacity": 4,
        "pending": [],
        "processors": [
            {
                "cam": camera,
                "running": True,
                "processor_alive": True,
                "source": "sub",
                "mode": "always_on",
                "analytics_scope": (
                    ANALYTICS_SCOPE_AI247
                    if camera == "cam4"
                    else ANALYTICS_SCOPE_SHIPPING
                ),
                "last_frame_at": "2026-09-01T08:00:00Z",
            }
            for camera in ("cam2", "cam3", "cam4")
        ],
    }
    with patch.object(ai, "configure_always_on", return_value=live) as configure:
        response = auth_client(boss).put(
            "/api/cameras/monoblock-settings/",
            {"camera_sources": ["cam2", "cam3"]},
            format="json",
        )

    assert response.status_code == 200
    assert response.data["always_on_sync_status"] == "synced"
    configure.assert_called_once_with(
        ["cam2", "cam3", "cam4"],
        "sub",
        analytics_scopes={
            "cam2": ANALYTICS_SCOPE_SHIPPING,
            "cam3": ANALYTICS_SCOPE_SHIPPING,
            "cam4": ANALYTICS_SCOPE_AI247,
        },
    )


def test_monoblock_camera_save_is_durable_when_camera_pc_is_offline(
    auth_client,
    boss,
    monkeypatch,
):
    monkeypatch.setattr(ai, "AI_KEY", "k")
    with patch.object(
        ai,
        "configure_always_on",
        side_effect=ai.AiUnavailable("offline"),
    ):
        response = auth_client(boss).put(
            "/api/cameras/monoblock-settings/",
            {"camera_sources": ["cam7"]},
            format="json",
        )

    assert response.status_code == 202
    assert response.data["always_on_sync_status"] == "pending"
    assert "offline" in response.data["always_on_detail"]
    assert MonoblockCameraSettings.shipping_sources() == ["cam7"]


def test_monoblock_camera_change_is_blocked_during_open_session(
    auth_client,
    boss,
):
    row = MonoblockCameraSettings.objects.create(camera_sources=["cam2"])
    order = Order.objects.create(
        client=Client.objects.create_with_user(
            first_name="Busy",
            last_name="Camera",
            phone="shared-settings-busy",
        ),
        status="loading",
        loading_camera="cam2",
    )
    AiCountingSession.objects.create(
        order=order,
        camera="cam2",
        status=AiCountingSession.ACTIVE,
        started_by=boss,
    )

    with patch.object(ai, "configure_always_on") as configure:
        response = auth_client(boss).put(
            "/api/cameras/monoblock-settings/",
            {"camera_sources": ["cam3"]},
            format="json",
        )

    assert response.status_code == 400
    assert response.data["code"] == "monoblock_busy"
    configure.assert_not_called()
    row.refresh_from_db()
    assert row.camera_sources == ["cam2"]


def test_monoblock_camera_change_rejects_known_effective_capacity_before_commit(
    auth_client,
    boss,
    monkeypatch,
):
    row = MonoblockCameraSettings.objects.create(camera_sources=["cam2"])
    monkeypatch.setattr(ai, "AI_KEY", "k")
    cache.set(
        ai.ALWAYS_ON_CACHE_KEY,
        {"cameras": ["cam2"], "source": "sub", "capacity": 1},
        ai.ALWAYS_ON_TTL,
    )

    with patch.object(ai, "configure_always_on") as configure:
        response = auth_client(boss).put(
            "/api/cameras/monoblock-settings/",
            {"camera_sources": ["cam2", "cam3"]},
            format="json",
        )

    assert response.status_code == 400
    assert response.data["code"] == "always_on_capacity_exceeded"
    configure.assert_not_called()
    row.refresh_from_db()
    assert row.camera_sources == ["cam2"]


def test_always_on_pending_reason_is_not_reported_as_synced(
    auth_client,
    superuser,
    monkeypatch,
):
    monkeypatch.setattr(ai, "AI_KEY", "k")
    live = {
        "cameras": ["cam2"],
        "source": "sub",
        "analytics_scopes": {"cam2": ANALYTICS_SCOPE_AI247},
        "capacity": 2,
        "pending": [{"cam": "cam2", "reason": "camera_warming"}],
        "processors": [
            {
                "cam": "cam2",
                "running": True,
                "processor_alive": True,
                "source": "sub",
                "mode": "always_on",
                "analytics_scope": ANALYTICS_SCOPE_AI247,
                "last_frame_at": "2026-09-01T08:00:00Z",
            }
        ],
    }
    with patch.object(ai, "configure_always_on", return_value=live):
        response = auth_client(superuser).put(
            "/api/cameras/always-on-settings/",
            {"camera_sources": ["cam2"]},
            format="json",
        )

    assert response.status_code == 202
    assert response.data["sync_status"] == "pending"
    assert "cam2: camera_warming" in response.data["detail"]

    with patch.object(ai, "always_on_status_cached", return_value=live):
        follow_up = auth_client(superuser).get(
            "/api/cameras/always-on-settings/"
        )
    assert follow_up.status_code == 200
    assert follow_up.data["sync_status"] == "pending"
    assert "camera_warming" in follow_up.data["detail"]


def test_order_mode_requires_continuous_analytics_for_always_on_readiness():
    live = {
        "cameras": ["cam2"],
        "source": "sub",
        "analytics_scopes": {"cam2": ANALYTICS_SCOPE_SHIPPING},
        "pending": [],
        "processors": [
            {
                "cam": "cam2",
                "running": True,
                "processor_alive": True,
                "source": "sub",
                "mode": "session",
                "analytics_scope": ANALYTICS_SCOPE_SHIPPING,
                "continuous_analytics": False,
                "last_frame_at": "2026-09-01T08:00:00Z",
            }
        ],
    }

    sync_status, detail = continuous.contour_sync_state(
        live,
        ["cam2"],
        ANALYTICS_SCOPE_SHIPPING,
    )

    assert sync_status == "pending"
    assert "потеряла непрерывную аналитику" in detail


def test_always_on_readiness_waits_for_first_inference_frame_when_exposed():
    live = {
        "cameras": ["cam2"],
        "source": "sub",
        "analytics_scopes": {"cam2": ANALYTICS_SCOPE_AI247},
        "pending": [],
        "processors": [
            {
                "cam": "cam2",
                "running": True,
                "processor_alive": True,
                "source": "sub",
                "mode": "always_on",
                "analytics_scope": ANALYTICS_SCOPE_AI247,
                "last_frame_at": "2026-09-01T08:00:00Z",
                "metrics": {"inference_frames": 0},
            }
        ],
    }

    sync_status, detail = continuous.contour_sync_state(
        live,
        ["cam2"],
        ANALYTICS_SCOPE_AI247,
    )
    assert sync_status == "pending"
    assert "ни одного кадра" in detail

    live["processors"][0]["metrics"]["inference_frames"] = 1
    assert continuous.contour_sync_state(
        live,
        ["cam2"],
        ANALYTICS_SCOPE_AI247,
    ) == ("synced", "")


def test_always_on_readiness_rejects_stale_frame_during_camera_gap():
    live = {
        "cameras": ["cam2"],
        "source": "sub",
        "analytics_scopes": {"cam2": ANALYTICS_SCOPE_AI247},
        "pending": [],
        "processors": [
            {
                "cam": "cam2",
                "running": True,
                "processor_alive": True,
                "source": "sub",
                "mode": "always_on",
                "analytics_scope": ANALYTICS_SCOPE_AI247,
                "status": "reconnecting",
                # This timestamp is deliberately stale: it must not make a
                # disconnected capture look ready.
                "last_frame_at": "2026-09-01T08:00:00Z",
                "metrics": {
                    "inference_frames": 18,
                    "camera_gap_started_at": "2026-09-01T08:00:05Z",
                },
            }
        ],
    }

    sync_status, detail = continuous.contour_sync_state(
        live,
        ["cam2"],
        ANALYTICS_SCOPE_AI247,
    )

    assert sync_status == "pending"
    assert "reconnecting" in detail

    live["processors"][0]["status"] = "online"
    sync_status, detail = continuous.contour_sync_state(
        live,
        ["cam2"],
        ANALYTICS_SCOPE_AI247,
    )
    assert sync_status == "pending"
    assert "потери потока" in detail

    live["processors"][0]["metrics"]["camera_gap_started_at"] = None
    assert continuous.contour_sync_state(
        live,
        ["cam2"],
        ANALYTICS_SCOPE_AI247,
    ) == ("synced", "")


def test_contour_readiness_requires_exact_top_level_and_processor_roles():
    live = {
        "cameras": ["cam2"],
        "source": "sub",
        "analytics_scopes": {"cam2": ANALYTICS_SCOPE_AI247},
        "pending": [],
        "processors": [
            {
                "cam": "cam2",
                "running": True,
                "processor_alive": True,
                "source": "sub",
                "mode": "always_on",
                "analytics_scope": ANALYTICS_SCOPE_AI247,
                "last_frame_at": "2026-09-01T08:00:00Z",
            }
        ],
    }

    status, detail = continuous.contour_sync_state(
        live,
        ["cam2"],
        ANALYTICS_SCOPE_SHIPPING,
    )
    assert status == "pending"
    assert "не подтвердил роль камеры cam2" in detail

    live["analytics_scopes"]["cam2"] = ANALYTICS_SCOPE_SHIPPING
    status, detail = continuous.contour_sync_state(
        live,
        ["cam2"],
        ANALYTICS_SCOPE_SHIPPING,
    )
    assert status == "pending"
    assert "Процессор cam2 не подтвердил свою роль" in detail


def test_always_on_apply_is_serialized_so_newer_policy_finishes_last(monkeypatch):
    desired = {"value": ["cam2"]}
    first_started = threading.Event()
    allow_first_to_finish = threading.Event()
    applied = []

    monkeypatch.setattr(
        MonoblockCameraSettings,
        "continuous_sources",
        lambda: list(desired["value"]),
    )
    monkeypatch.setattr(
        MonoblockCameraSettings,
        "continuous_roles",
        lambda: {
            camera: ANALYTICS_SCOPE_AI247 for camera in desired["value"]
        },
    )

    def configure(cameras, source, *, analytics_scopes):
        if cameras == ["cam2"]:
            first_started.set()
            assert allow_first_to_finish.wait(timeout=5)
        applied.append((list(cameras), source, dict(analytics_scopes)))
        return {
            "cameras": cameras,
            "source": source,
            "analytics_scopes": analytics_scopes,
            "processors": [],
        }

    monkeypatch.setattr(ai, "configure_always_on", configure)

    def apply():
        try:
            return continuous.sync_always_on_policy()
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(apply)
        assert first_started.wait(timeout=5)
        desired["value"] = ["cam3"]
        second = executor.submit(apply)
        allow_first_to_finish.set()
        first.result(timeout=5)
        second.result(timeout=5)

    assert applied == [
        (["cam2"], "sub", {"cam2": ANALYTICS_SCOPE_AI247}),
        (["cam3"], "sub", {"cam3": ANALYTICS_SCOPE_AI247}),
    ]


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


def test_always_on_settings_are_readable_to_loaders_and_managed_separately(
    auth_client,
    boss,
    operator,
    client_user,
    user_with_perms,
    monkeypatch,
):
    for user in (boss, operator):
        response = auth_client(user).get("/api/cameras/always-on-settings/")
        assert response.status_code == 200
    assert (
        auth_client(client_user).get("/api/cameras/always-on-settings/").status_code
        == 403
    )

    denied = auth_client(boss).put(
        "/api/cameras/always-on-settings/",
        {"camera_sources": ["cam2"]},
        format="json",
    )
    assert denied.status_code == 403

    manager = user_with_perms("ai-247-manager", codes=["ai_247.manage"])

    monkeypatch.setattr(ai, "AI_KEY", "k")
    live = {
        "cameras": ["cam2"],
        "source": "sub",
        "analytics_scopes": {"cam2": ANALYTICS_SCOPE_AI247},
        "capacity": 2,
        "pending": [],
        "processors": [
            {
                "cam": "cam2",
                "running": True,
                "processor_alive": True,
                "source": "sub",
                "mode": "always_on",
                "analytics_scope": ANALYTICS_SCOPE_AI247,
                "recording": False,
                "total": 14,
                "last_frame_at": "2026-09-01T08:00:00Z",
            }
        ],
    }
    with (
        patch.object(
            ai,
            "cached_always_on_status",
            return_value={
                "cameras": [],
                "source": "sub",
                "analytics_scopes": {},
                "capacity": 2,
                "processors": [],
            },
        ),
        patch.object(ai, "configure_always_on", return_value=live) as configure,
    ):
        response = auth_client(manager).put(
            "/api/cameras/always-on-settings/",
            {"camera_sources": ["2", "cam2"]},
            format="json",
        )

    assert response.status_code == 200
    assert response.data["camera_sources"] == ["cam2"]
    assert response.data["analytics_scope"] == ANALYTICS_SCOPE_AI247
    assert response.data["blocked_camera_sources"] == []
    assert response.data["processors"][0]["recording"] is False
    configure.assert_called_once_with(
        ["cam2"],
        "sub",
        analytics_scopes={"cam2": ANALYTICS_SCOPE_AI247},
    )
    row = MonoblockCameraSettings.objects.get(singleton=True)
    assert row.always_on_camera_sources == ["cam2"]
    assert row.updated_by == manager
    # 24/7 — фоновый режим камеры, а не отгрузка: он не создаёт владельца,
    # сессию заказа или какую-либо camera binding в CRM.
    assert AiCountingSession.objects.count() == 0


def test_always_on_choice_survives_camera_pc_outage(
    auth_client,
    superuser,
    monkeypatch,
):
    monkeypatch.setattr(ai, "AI_KEY", "k")
    with (
        patch.object(
            ai,
            "always_on_status",
            side_effect=ai.AiUnavailable("offline"),
        ),
        patch.object(
            ai,
            "configure_always_on",
            side_effect=ai.AiUnavailable("offline"),
        ),
    ):
        response = auth_client(superuser).put(
            "/api/cameras/always-on-settings/",
            {"camera_sources": ["cam3"]},
            format="json",
        )

    assert response.status_code == 202
    assert response.data["sync_status"] == "pending"
    assert MonoblockCameraSettings.ai247_sources() == ["cam3"]


def test_ai247_settings_expose_shipping_cameras_as_blocked(
    auth_client,
    superuser,
):
    MonoblockCameraSettings.objects.create(
        camera_sources=["cam2"],
        always_on_camera_sources=["cam3"],
    )
    ContinuousCameraRole.objects.bulk_create(
        [
            ContinuousCameraRole(
                camera="cam2",
                analytics_scope=ANALYTICS_SCOPE_SHIPPING,
            ),
            ContinuousCameraRole(
                camera="cam3",
                analytics_scope=ANALYTICS_SCOPE_AI247,
            ),
        ]
    )

    response = auth_client(superuser).get("/api/cameras/always-on-settings/")

    assert response.status_code == 200
    assert response.data["analytics_scope"] == ANALYTICS_SCOPE_AI247
    assert response.data["automatic_camera_sources"] == []
    assert response.data["manual_camera_sources"] == ["cam3"]
    assert response.data["camera_sources"] == ["cam3"]
    assert response.data["blocked_camera_sources"] == ["cam2"]


def test_ai247_picker_cannot_claim_shipping_camera_atomically(
    auth_client,
    superuser,
):
    row = MonoblockCameraSettings.objects.create(
        camera_sources=["cam2"],
        always_on_camera_sources=["cam3"],
    )

    with patch.object(ai, "configure_always_on") as configure:
        response = auth_client(superuser).put(
            "/api/cameras/always-on-settings/",
            {"camera_sources": ["cam2", "cam3"]},
            format="json",
        )

    assert response.status_code == 409
    assert response.data["code"] == "camera_role_immutable"
    assert response.data["detail"]["cameras"] == ["cam2"]
    configure.assert_not_called()
    row.refresh_from_db()
    assert row.camera_sources == ["cam2"]
    assert row.always_on_camera_sources == ["cam3"]


def test_contour_settings_and_shipping_detections_do_not_leak_other_role(
    auth_client,
    boss,
    monkeypatch,
):
    MonoblockCameraSettings.objects.create(
        camera_sources=["cam2"],
        always_on_camera_sources=["cam3"],
    )
    ContinuousCameraRole.objects.bulk_create(
        [
            ContinuousCameraRole(
                camera="cam2",
                analytics_scope=ANALYTICS_SCOPE_SHIPPING,
            ),
            ContinuousCameraRole(
                camera="cam3",
                analytics_scope=ANALYTICS_SCOPE_AI247,
            ),
        ]
    )
    monkeypatch.setattr(ai, "AI_KEY", "k")
    live = {
        "cameras": ["cam2", "cam3"],
        "source": "sub",
        "analytics_scopes": {
            "cam2": ANALYTICS_SCOPE_SHIPPING,
            "cam3": ANALYTICS_SCOPE_AI247,
        },
        "capacity": 4,
        "pending": [],
        "processors": [
            {
                "cam": "cam2",
                "running": True,
                "processor_alive": True,
                "source": "sub",
                "mode": "always_on",
                "analytics_scope": ANALYTICS_SCOPE_SHIPPING,
                "last_frame_at": "2026-09-01T08:00:00Z",
                "total": 12,
            },
            {
                "cam": "cam3",
                "running": True,
                "processor_alive": True,
                "source": "sub",
                "mode": "always_on",
                "analytics_scope": ANALYTICS_SCOPE_AI247,
                "last_frame_at": "2026-09-01T08:00:00Z",
                "total": 34,
            },
        ],
    }
    detections = {
        **live,
        "processors": [
            {"cam": "cam2", "analytics_scope": ANALYTICS_SCOPE_SHIPPING},
            {"cam": "cam3", "analytics_scope": ANALYTICS_SCOPE_AI247},
        ],
    }

    with (
        patch.object(ai, "always_on_status_cached", return_value=live),
        patch.object(ai, "always_on_detections_cached", return_value=detections),
    ):
        shipping = auth_client(boss).get(
            "/api/cameras/shipping-continuous-settings/"
        )
        ai247 = auth_client(boss).get("/api/cameras/always-on-settings/")
        boxes = auth_client(boss).get(
            "/api/cameras/shipping-continuous-detections/"
        )

    assert shipping.status_code == 200
    assert shipping.data["analytics_scope"] == ANALYTICS_SCOPE_SHIPPING
    assert shipping.data["camera_sources"] == ["cam2"]
    assert shipping.data["blocked_camera_sources"] == ["cam3"]
    assert shipping.data["sync_status"] == "synced"
    assert [item["cam"] for item in shipping.data["processors"]] == ["cam2"]

    assert ai247.status_code == 200
    assert ai247.data["analytics_scope"] == ANALYTICS_SCOPE_AI247
    assert ai247.data["camera_sources"] == ["cam3"]
    assert ai247.data["blocked_camera_sources"] == ["cam2"]
    assert ai247.data["sync_status"] == "synced"
    assert [item["cam"] for item in ai247.data["processors"]] == ["cam3"]

    assert boxes.status_code == 200
    assert boxes.data["cameras"] == ["cam2"]
    assert boxes.data["camera_sources"] == ["cam2"]
    assert boxes.data["analytics_scopes"] == {
        "cam2": ANALYTICS_SCOPE_SHIPPING
    }
    assert [item["cam"] for item in boxes.data["processors"]] == ["cam2"]


def test_contour_settings_hide_stale_processors_from_the_opposite_scope(
    auth_client,
    boss,
    monkeypatch,
):
    MonoblockCameraSettings.objects.create(
        camera_sources=["cam2"],
        always_on_camera_sources=["cam3"],
    )
    ContinuousCameraRole.objects.bulk_create(
        [
            ContinuousCameraRole(
                camera="cam2",
                analytics_scope=ANALYTICS_SCOPE_SHIPPING,
            ),
            ContinuousCameraRole(
                camera="cam3",
                analytics_scope=ANALYTICS_SCOPE_AI247,
            ),
        ]
    )
    monkeypatch.setattr(ai, "AI_KEY", "k")
    stale_live = {
        "cameras": ["cam2", "cam3"],
        "source": "sub",
        "analytics_scopes": {
            "cam2": ANALYTICS_SCOPE_SHIPPING,
            "cam3": ANALYTICS_SCOPE_AI247,
        },
        "processors": [
            {
                "cam": "cam2",
                "analytics_scope": ANALYTICS_SCOPE_AI247,
                "running": True,
                "total": 99,
            },
            {
                "cam": "cam3",
                "analytics_scope": ANALYTICS_SCOPE_SHIPPING,
                "running": True,
                "total": 88,
            },
        ],
        "pending": [
            {"cam": "cam2", "analytics_scope": ANALYTICS_SCOPE_AI247},
            {"cam": "cam3", "analytics_scope": ANALYTICS_SCOPE_SHIPPING},
        ],
    }

    with (
        patch.object(ai, "always_on_status_cached", return_value=stale_live),
        patch.object(ai, "always_on_detections_cached", return_value=stale_live),
    ):
        shipping = auth_client(boss).get(
            "/api/cameras/shipping-continuous-settings/"
        )
        ai247 = auth_client(boss).get("/api/cameras/always-on-settings/")
        monoblock = auth_client(boss).get("/api/cameras/monoblock-settings/")
        detections = auth_client(boss).get(
            "/api/cameras/shipping-continuous-detections/"
        )

    assert shipping.data["processors"] == []
    assert shipping.data["sync_status"] == "pending"
    assert ai247.data["processors"] == []
    assert ai247.data["sync_status"] == "pending"
    assert monoblock.data["processors"] == []
    assert detections.data["processors"] == []
    assert detections.data["pending"] == []


def test_wagon_number_camera_assignment_is_superuser_only_and_uses_main_stream(
    auth_client,
    superuser,
    boss,
    operator,
    client_user,
    user_with_perms,
    monkeypatch,
):
    for user in (boss, operator, client_user):
        response = auth_client(user).get("/api/cameras/wagon-number-settings/")
        assert response.status_code == 403

    grain_viewer = user_with_perms("grain-camera-viewer", codes=["grain.view"])
    assert (
        auth_client(grain_viewer).get("/api/cameras/wagon-number-settings/").status_code
        == 200
    )
    assert (
        auth_client(grain_viewer)
        .put(
            "/api/cameras/wagon-number-settings/",
            {"camera_source": "cam1"},
            format="json",
        )
        .status_code
        == 403
    )

    monkeypatch.setattr(ai, "AI_KEY", "k")
    live = {
        "camera": "cam1",
        "source": "main",
        "stream": "cam1",
        "assigned": True,
        "mode": "wagon_number_24_7",
    }
    with patch.object(ai, "configure_wagon_number", return_value=live) as configure:
        response = auth_client(superuser).put(
            "/api/cameras/wagon-number-settings/",
            {"camera_source": "1"},
            format="json",
        )

    assert response.status_code == 200
    assert response.data["camera_source"] == "cam1"
    assert response.data["source"] == "main"
    assert response.data["sync_status"] == "synced"
    configure.assert_called_once_with("cam1", "main")
    row = MonoblockCameraSettings.objects.get(singleton=True)
    assert row.wagon_number_camera_source == "cam1"
    assert row.updated_by == superuser


def test_wagon_number_camera_assignment_survives_camera_pc_outage(
    auth_client,
    superuser,
    monkeypatch,
):
    monkeypatch.setattr(ai, "AI_KEY", "k")
    with patch.object(
        ai,
        "configure_wagon_number",
        side_effect=ai.AiUnavailable("offline"),
    ):
        response = auth_client(superuser).put(
            "/api/cameras/wagon-number-settings/",
            {"camera_source": "cam3"},
            format="json",
        )

    assert response.status_code == 202
    assert response.data["camera_source"] == "cam3"
    assert response.data["sync_status"] == "pending"
    assert MonoblockCameraSettings.wagon_number_source() == "cam3"

    with patch.object(
        ai,
        "wagon_number_status_cached",
        side_effect=ai.AiUnavailable("offline"),
    ):
        follow_up = auth_client(superuser).get("/api/cameras/wagon-number-settings/")
    assert follow_up.data["camera_source"] == "cam3"


def test_superuser_can_clear_wagon_number_camera_assignment(
    auth_client,
    superuser,
    monkeypatch,
):
    MonoblockCameraSettings.objects.create(wagon_number_camera_source="cam2")
    monkeypatch.setattr(ai, "AI_KEY", "k")
    live = {
        "camera": None,
        "source": "main",
        "stream": None,
        "assigned": False,
        "mode": "wagon_number_24_7",
    }
    with patch.object(ai, "configure_wagon_number", return_value=live) as configure:
        response = auth_client(superuser).put(
            "/api/cameras/wagon-number-settings/",
            {"camera_source": None},
            format="json",
        )

    assert response.status_code == 200
    assert response.data["camera_source"] is None
    configure.assert_called_once_with(None, "main")
    assert MonoblockCameraSettings.wagon_number_source() == ""


def test_superuser_cannot_exceed_camera_pc_processor_capacity(
    auth_client,
    superuser,
    monkeypatch,
):
    monkeypatch.setattr(ai, "AI_KEY", "k")
    # Проверка лимита читает только готовый снимок, чтобы сохранение не платило
    # второй сетевой таймаут, когда ПК цеха недоступен.
    with (
        patch.object(
            ai,
            "cached_always_on_status",
            return_value={
                "cameras": [],
                "source": "sub",
                "analytics_scopes": {},
                "capacity": 1,
                "processors": [],
            },
        ),
        patch.object(ai, "configure_always_on") as configure,
    ):
        response = auth_client(superuser).put(
            "/api/cameras/always-on-settings/",
            {"camera_sources": ["cam2", "cam3"]},
            format="json",
        )

    assert response.status_code == 400
    configure.assert_not_called()
    assert MonoblockCameraSettings.ai247_sources() == []


def test_capacity_guard_allows_policy_reduction_back_toward_limit(
    auth_client,
    superuser,
    monkeypatch,
):
    MonoblockCameraSettings.objects.create(
        always_on_camera_sources=["cam2", "cam3"]
    )
    monkeypatch.setattr(ai, "AI_KEY", "k")
    live = {
        "cameras": ["cam2"],
        "source": "sub",
        "analytics_scopes": {"cam2": ANALYTICS_SCOPE_AI247},
        "capacity": 1,
        "pending": [],
        "processors": [
            {
                "cam": "cam2",
                "running": True,
                "processor_alive": True,
                "source": "sub",
                "mode": "always_on",
                "analytics_scope": ANALYTICS_SCOPE_AI247,
                "last_frame_at": "2026-09-01T08:00:00Z",
            }
        ],
    }
    with (
        patch.object(
            ai,
            "cached_always_on_status",
            return_value={"capacity": 1},
        ),
        patch.object(ai, "configure_always_on", return_value=live) as configure,
    ):
        response = auth_client(superuser).put(
            "/api/cameras/always-on-settings/",
            {"camera_sources": ["cam2"]},
            format="json",
        )

    assert response.status_code == 200
    configure.assert_called_once_with(
        ["cam2"],
        "sub",
        analytics_scopes={"cam2": ANALYTICS_SCOPE_AI247},
    )
    assert MonoblockCameraSettings.ai247_sources() == ["cam2"]


def test_token_sets_cookie(auth_client, operator):
    resp = auth_client(operator).post("/api/cameras/token/")
    assert resp.status_code == 204
    cookie = resp.cookies.get(CAM_COOKIE)
    assert cookie is not None
    assert cookie["httponly"]
    assert cookie["max-age"] == CAM_TOKEN_MAX_AGE
    payload = signing.loads(cookie.value, salt=CAM_TOKEN_SALT)
    assert payload == {
        "version": CAM_TOKEN_VERSION,
        "audience": CAM_TOKEN_AUDIENCE,
        "user_id": operator.pk,
        "revocation": operator.get_session_auth_hash(),
    }


def test_token_denied_for_portal_client(auth_client, client_user):
    resp = auth_client(client_user).post("/api/cameras/token/")
    assert resp.status_code == 403


@pytest.mark.parametrize(
    "source",
    ["cam2", "cam2ai", "cam_8c28", "cam1main"],
)
def test_auth_accepts_valid_staff_cookie(api_client, auth_client, operator, source):
    token = _stream_token(auth_client, operator)
    response = _authorize_stream(
        api_client,
        token,
        f"/go2rtc/api/ws?src={source}",
    )
    assert response.status_code == 204


@override_settings(
    VEHICLE_PLATE_WEIGHT_FIRST_ENABLED=True,
    VEHICLE_PLATE_WEIGHT_FIRST_CAMERA="cam32",
    VEHICLE_PLATE_WEIGHT_FIRST_SOURCE="main",
)
def test_auth_accepts_only_the_configured_weight_first_main_alias(
    api_client,
    auth_client,
    operator,
):
    token = _stream_token(auth_client, operator)

    assert (
        _authorize_stream(
            api_client,
            token,
            "/go2rtc/api/ws?src=cam32main",
        ).status_code
        == 204
    )
    assert (
        _authorize_stream(
            api_client,
            token,
            "/go2rtc/api/ws?src=cam2main",
        ).status_code
        == 403
    )


@override_settings(
    VEHICLE_PLATE_WEIGHT_FIRST_ENABLED=False,
    VEHICLE_PLATE_AUTO_SCALE_ENABLED=True,
    VEHICLE_PLATE_WEIGHT_FIRST_CAMERA="cam32",
    VEHICLE_PLATE_WEIGHT_FIRST_SOURCE="main",
)
def test_auth_accepts_configured_main_alias_in_auto_scale_only_mode(
    api_client,
    auth_client,
    operator,
):
    token = _stream_token(auth_client, operator)

    assert (
        _authorize_stream(
            api_client,
            token,
            "/go2rtc/api/ws?src=cam32main",
        ).status_code
        == 204
    )
    assert (
        _authorize_stream(
            api_client,
            token,
            "/go2rtc/api/ws?src=cam1main",
        ).status_code
        == 403
    )


@override_settings(
    VEHICLE_PLATE_WEIGHT_FIRST_ENABLED=True,
    VEHICLE_PLATE_WEIGHT_FIRST_CAMERA="cam7",
    VEHICLE_PLATE_WEIGHT_FIRST_SOURCE="sub",
)
def test_auth_does_not_expose_a_main_alias_for_a_substream_lane(
    api_client,
    auth_client,
    operator,
):
    token = _stream_token(auth_client, operator)

    assert (
        _authorize_stream(
            api_client,
            token,
            "/go2rtc/api/ws?src=cam7main",
        ).status_code
        == 403
    )


@pytest.mark.parametrize(
    "source",
    ["cam0main", "cam33main", "cam1mainai", "cam1main-extra"],
)
def test_auth_rejects_unprovisioned_or_malformed_main_stream_aliases(
    api_client,
    auth_client,
    operator,
    source,
):
    token = _stream_token(auth_client, operator)
    response = _authorize_stream(
        api_client,
        token,
        f"/go2rtc/api/ws?src={source}",
    )
    assert response.status_code == 403


def test_auth_rejects_missing_or_bad_cookie(api_client):
    assert _authorize_stream(api_client, "").status_code == 403
    assert _authorize_stream(api_client, "garbage").status_code == 403


def test_auth_rejects_expired_cookie(api_client, auth_client, operator):
    with patch("django.core.signing.time.time", return_value=1):
        token = _stream_token(auth_client, operator)
    assert _authorize_stream(api_client, token).status_code == 403


def test_auth_rejects_old_legacy_cookie(api_client, operator):
    token = signing.TimestampSigner(salt="cameras").sign(str(operator.pk))
    assert _authorize_stream(api_client, token).status_code == 403


def test_auth_rejects_malformed_structured_cookie(api_client, operator):
    malformed_payloads = [
        ["not", "a", "mapping"],
        {
            "version": CAM_TOKEN_VERSION,
            "audience": "some-other-service",
            "user_id": operator.pk,
            "revocation": operator.get_session_auth_hash(),
        },
    ]
    for payload in malformed_payloads:
        token = signing.dumps(payload, salt=CAM_TOKEN_SALT)
        assert _authorize_stream(api_client, token).status_code == 403


@pytest.mark.parametrize(
    "account_change", ["inactive", "deleted", "password", "client"]
)
def test_auth_reloads_and_revalidates_current_user(
    api_client,
    auth_client,
    operator,
    account_change,
):
    token = _stream_token(auth_client, operator)
    if account_change == "deleted":
        operator.delete()
    elif account_change == "password":
        operator.set_password("a-new-password-123")
        operator.save(update_fields=["password"])
    else:
        field = "is_active" if account_change == "inactive" else "is_client"
        setattr(operator, field, account_change != "inactive")
        operator.save(update_fields=[field])

    assert _authorize_stream(api_client, token).status_code == 403


@pytest.fixture
def stream_device(django_user_model):
    user = django_user_model.objects.create_user(
        username="camera-device",
        password="device-password-123",
    )
    return MonoblockDevice.objects.create(
        user=user,
        name="Device cam2",
        camera_source="cam2",
    )


@pytest.mark.parametrize("source", ["cam2", "cam2ai"])
def test_monoblock_stream_cookie_allows_own_base_and_ai_stream(
    api_client,
    auth_client,
    stream_device,
    source,
):
    token = _stream_token(auth_client, stream_device.user)
    response = _authorize_stream(
        api_client,
        token,
        f"/go2rtc/api/ws?src={source}",
    )
    assert response.status_code == 204


def test_monoblock_stream_cookie_rejects_cross_camera(
    api_client,
    auth_client,
    stream_device,
):
    token = _stream_token(auth_client, stream_device.user)
    response = _authorize_stream(
        api_client,
        token,
        "/go2rtc/api/ws?src=cam3",
    )
    assert response.status_code == 403


def test_monoblock_stream_cookie_cannot_use_staff_only_cam1_main_alias(
    api_client,
    auth_client,
    stream_device,
):
    stream_device.camera_source = "cam1"
    stream_device.save(update_fields=["camera_source"])
    token = _stream_token(auth_client, stream_device.user)
    response = _authorize_stream(
        api_client,
        token,
        "/go2rtc/api/ws?src=cam1main",
    )
    assert response.status_code == 403


def test_inactive_monoblock_device_stream_cookie_is_rejected(
    api_client,
    auth_client,
    stream_device,
):
    token = _stream_token(auth_client, stream_device.user)
    stream_device.is_active = False
    stream_device.save(update_fields=["is_active"])
    assert _authorize_stream(api_client, token).status_code == 403


@pytest.mark.parametrize(
    "original_uri",
    [
        None,
        "",
        "/go2rtc/api/ws",
        "/go2rtc/api/ws?src=",
        "/go2rtc/api/ws?src=cam2&src=cam2",
        "/go2rtc/api/ws?src=cam2&extra=1",
        "/go2rtc/api/ws?src=../cam2",
        "/go2rtc/api/ws?src=cam2%00",
        "/go2rtc/api/ws?src=2",
        "/go2rtc/api/ws/?src=cam2",
        "/go2rtc/api/ws?src=cam2#fragment",
        "https://example.test/go2rtc/api/ws?src=cam2",
    ],
)
def test_auth_rejects_malformed_original_uri(
    api_client,
    auth_client,
    operator,
    original_uri,
):
    token = _stream_token(auth_client, operator)
    assert _authorize_stream(api_client, token, original_uri).status_code == 403


def test_known_topology_answers_without_waiting_for_the_network(monkeypatch):
    """A stale cache must never make the request pay a camera-PC timeout.

    The monoblock page re-reads cameras on a timer. When the shop floor is
    offline, probing inline costs up to two ``PROBE_TIMEOUT`` waves per request
    and left the page stuck on "Загрузка…". With a known topology the answer is
    served immediately and the refresh happens in the background.
    """
    monkeypatch.setattr(services, "CAMERA_PASS", "x")
    with patch.object(services, "_probe_path", side_effect=fake_probe(["online"])):
        services.discover_cameras()
    cache.delete(services.CACHE_KEY)  # снимок протух, last-known-good остался

    started = []

    class ImmediateThread:
        def __init__(self, target, **kwargs):
            self._target = target
            started.append(self)

        def start(self):
            pass  # обнаружение НЕ должно выполняться внутри запроса

    with (
        patch.object(services.threading, "Thread", ImmediateThread),
        patch.object(services, "_probe_path") as probe,
    ):
        cameras = services.discover_cameras()

    assert probe.call_count == 0, "запрос не должен ходить по сети"
    assert [c["id"] for c in cameras] == ["nvr:cam1"]
    assert cameras[0]["online"] is False
    assert started, "обновление должно уйти в фон"

    # Фоновое обновление возвращает камеры в строй и снимает замок.
    with patch.object(services, "_probe_path", side_effect=fake_probe(["online"])):
        started[0]._target()
    assert cache.get(services.REFRESH_LOCK_KEY) is None
    assert services.discover_cameras()[0]["online"] is True


def test_always_on_status_is_not_refetched_on_every_poll(monkeypatch):
    """The polling read path must not call the camera PC per request."""
    monkeypatch.setattr(ai, "AI_KEY", "k")
    cache.delete(ai.ALWAYS_ON_CACHE_KEY)
    payload = {
        "cameras": ["cam1"],
        "source": "sub",
        "analytics_scopes": {"cam1": ANALYTICS_SCOPE_AI247},
        "processors": [],
    }
    with patch.object(ai, "_call", return_value=payload) as call:
        first = ai.always_on_status_cached()
        second = ai.always_on_status_cached()
    assert call.call_count == 1
    assert first == second == payload


def test_always_on_choice_survives_an_unreachable_camera_pc(
    auth_client,
    superuser,
    monkeypatch,
):
    """A camera-PC timeout must not discard the administrator's selection.

    PostgreSQL is authoritative and the camera-monitor keeps retrying, so the
    saved cameras have to come back on the next read even while the shop floor
    is offline — the UI showed the choice reverting to zero instead.
    """
    monkeypatch.setattr(ai, "AI_KEY", "k")
    cache.delete(ai.ALWAYS_ON_CACHE_KEY)
    timeout = ai.AiUnavailable("<urlopen error timed out>")

    with (
        patch.object(ai, "configure_always_on", side_effect=timeout),
        patch.object(ai, "always_on_status") as blocking_status,
    ):
        response = auth_client(superuser).put(
            "/api/cameras/always-on-settings/",
            {"camera_sources": ["cam3"]},
            format="json",
        )

    # Saving must cost at most the write itself. The optional capacity hint may
    # not add a second timeout wait on top of it.
    blocking_status.assert_not_called()
    assert response.status_code == 202
    assert response.data["camera_sources"] == ["cam3"]
    assert response.data["sync_status"] == "pending"
    assert MonoblockCameraSettings.ai247_sources() == ["cam3"]

    # Follow-up reads keep showing it, so the page cannot fall back to "0".
    with patch.object(ai, "always_on_status_cached", side_effect=timeout):
        follow_up = auth_client(superuser).get("/api/cameras/always-on-settings/")
    assert follow_up.data["camera_sources"] == ["cam3"]
