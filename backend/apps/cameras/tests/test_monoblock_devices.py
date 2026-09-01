from unittest.mock import patch

import pytest

from apps.cameras import ai, services
from apps.cameras.models import (
    ANALYTICS_SCOPE_AI247,
    AiCountingSession,
    ContinuousCameraRole,
    MonoblockCameraSettings,
    MonoblockDevice,
)
from apps.clients.models import Client
from apps.orders.models import Order

pytestmark = pytest.mark.django_db


@pytest.fixture
def superuser(django_user_model):
    return django_user_model.objects.create_superuser(
        username="root-device", password="pass12345",
    )


def _create_device(auth_client, superuser, **overrides):
    payload = {
        "name": "Моноблок фасовки", "username": "mono-1",
        "password": "Complex-pass-123", "camera_source": "cam2",
        **overrides,
    }
    response = auth_client(superuser).post(
        "/api/cameras/monoblock-devices/", payload, format="json",
    )
    assert response.status_code in {201, 202}, response.data
    return MonoblockDevice.objects.select_related("user").get(pk=response.data["id"])


def test_superuser_creates_dedicated_account_and_me_exposes_binding(auth_client, superuser):
    device = _create_device(auth_client, superuser)
    response = auth_client(device.user).get("/api/auth/me/")
    assert response.status_code == 200
    assert response.data["is_monoblock"] is True
    assert response.data["monoblock_name"] == device.name
    assert response.data["monoblock_camera"] == "cam2"
    assert response.data["permissions"] == ["orders.view", "shipping.load"]


def test_device_sees_only_its_camera_and_locked_settings(auth_client, superuser):
    device = _create_device(auth_client, superuser)
    cameras = [
        {"id": "1", "src": "cam1", "zone": "A", "name": "1", "online": True},
        {"id": "2", "src": "cam2", "zone": "B", "name": "2", "online": True},
    ]
    with patch.object(services, "discover_cameras", return_value=cameras):
        response = auth_client(device.user).get("/api/cameras/")
    assert [row["src"] for row in response.data] == ["cam2"]
    settings = auth_client(device.user).get("/api/cameras/monoblock-settings/")
    assert settings.data["camera_sources"] == ["cam2"]
    assert settings.data["locked"] is True
    assert settings.data["device_name"] == device.name


def test_device_cannot_start_another_camera(auth_client, superuser, monkeypatch):
    device = _create_device(auth_client, superuser)
    monkeypatch.setattr(ai, "AI_KEY", "key")
    order = Order.objects.create(
        client=Client.objects.create_with_user(first_name="A", last_name="B", phone="1"),
        status="confirmed",
    )
    response = auth_client(device.user).post(
        "/api/cameras/cam3/ai/", {"order_id": order.pk}, format="json",
    )
    assert response.status_code == 403
    order.refresh_from_db()
    assert order.status == "confirmed"


def test_device_cannot_claim_an_ai247_camera(auth_client, superuser):
    MonoblockCameraSettings.objects.create(always_on_camera_sources=["cam2"])
    ContinuousCameraRole.objects.create(
        camera="cam2",
        analytics_scope=ANALYTICS_SCOPE_AI247,
    )

    response = auth_client(superuser).post(
        "/api/cameras/monoblock-devices/",
        {
            "name": "Конфликтный моноблок",
            "username": "mono-conflict",
            "password": "Complex-pass-123",
            "camera_source": "cam2",
        },
        format="json",
    )

    assert response.status_code == 409
    assert response.data["code"] == "camera_role_immutable"
    assert not MonoblockDevice.objects.exists()
    assert not type(superuser)._default_manager.filter(
        username="mono-conflict"
    ).exists()


def test_device_move_to_ai247_camera_is_atomic(auth_client, superuser):
    device = _create_device(auth_client, superuser)
    row = MonoblockCameraSettings.objects.get(singleton=True)
    row.always_on_camera_sources = ["cam3"]
    row.save(update_fields=["always_on_camera_sources"])
    ContinuousCameraRole.objects.create(
        camera="cam3",
        analytics_scope=ANALYTICS_SCOPE_AI247,
    )

    response = auth_client(superuser).patch(
        f"/api/cameras/monoblock-devices/{device.pk}/",
        {"camera_source": "cam3"},
        format="json",
    )

    assert response.status_code == 409
    assert response.data["code"] == "camera_role_immutable"
    device.refresh_from_db()
    assert device.camera_source == "cam2"


def test_device_order_list_is_scoped_to_queue_and_own_camera(auth_client, superuser):
    device = _create_device(auth_client, superuser)
    client = Client.objects.create_with_user(first_name="A", last_name="C", phone="2")
    waiting = Order.objects.create(client=client, status="confirmed")
    own = Order.objects.create(client=client, status="loading", loading_camera="cam2")
    Order.objects.create(client=client, status="loading", loading_camera="cam3")
    Order.objects.create(client=client, status="shipped")
    response = auth_client(device.user).get("/api/orders/")
    assert response.status_code == 200
    assert {row["id"] for row in response.data} == {waiting.pk, own.pk}


@pytest.mark.parametrize(
    "changes",
    [
        {"camera_source": "cam3"},
        {"is_active": False},
    ],
)
def test_active_loading_prevents_moving_or_disabling_device(
    auth_client, superuser, changes,
):
    device = _create_device(auth_client, superuser)
    order = Order.objects.create(
        client=Client.objects.create_with_user(
            first_name="A", last_name="Busy", phone="busy"
        ),
        status="loading",
        loading_camera=device.camera_source,
    )
    AiCountingSession.objects.create(
        order=order,
        camera=device.camera_source,
        status=AiCountingSession.ACTIVE,
        started_by=device.user,
    )

    response = auth_client(superuser).patch(
        f"/api/cameras/monoblock-devices/{device.pk}/",
        changes,
        format="json",
    )

    assert response.status_code == 400
    assert response.data["code"] == "monoblock_busy"
    device.refresh_from_db()
    device.user.refresh_from_db()
    assert device.camera_source == "cam2"
    assert device.is_active is True
    assert device.user.is_active is True


def test_legacy_camera_binding_also_prevents_device_deactivation(
    auth_client, superuser,
):
    device = _create_device(auth_client, superuser)
    Order.objects.create(
        client=Client.objects.create_with_user(
            first_name="A", last_name="Bound", phone="bound"
        ),
        status="loading",
        loading_camera=device.camera_source,
    )

    response = auth_client(superuser).patch(
        f"/api/cameras/monoblock-devices/{device.pk}/",
        {"is_active": False},
        format="json",
    )

    assert response.status_code == 400
    assert response.data["code"] == "monoblock_busy"


def test_active_device_cannot_be_created_over_open_camera_session(
    auth_client,
    superuser,
    django_user_model,
):
    order = Order.objects.create(
        client=Client.objects.create_with_user(
            first_name="Reserved", last_name="Camera", phone="create-busy",
        ),
        status="confirmed",
    )
    AiCountingSession.objects.create(
        order=order,
        camera="cam2",
        status=AiCountingSession.STARTING,
        started_by=superuser,
    )

    response = auth_client(superuser).post(
        "/api/cameras/monoblock-devices/",
        {
            "name": "Поздний моноблок",
            "username": "late-monoblock",
            "password": "Complex-pass-123",
            "camera_source": "cam2",
            "is_active": True,
        },
        format="json",
    )

    assert response.status_code == 400
    assert response.data["code"] == "monoblock_busy"
    assert not MonoblockDevice.objects.filter(camera_source="cam2").exists()
    assert not django_user_model.objects.filter(username="late-monoblock").exists()


def test_inactive_device_cannot_be_reactivated_over_active_camera_work(
    auth_client,
    superuser,
):
    device = _create_device(
        auth_client,
        superuser,
        username="inactive-busy-device",
        is_active=False,
    )
    order = Order.objects.create(
        client=Client.objects.create_with_user(
            first_name="Inactive",
            last_name="Busy",
            phone="inactive-busy",
        ),
        status="loading",
        loading_camera=device.camera_source,
    )
    AiCountingSession.objects.create(
        order=order,
        camera=device.camera_source,
        status=AiCountingSession.ACTIVE,
        started_by=superuser,
    )

    with patch.object(ai, "configure_always_on") as configure:
        response = auth_client(superuser).patch(
            f"/api/cameras/monoblock-devices/{device.pk}/",
            {"is_active": True},
            format="json",
        )

    assert response.status_code == 400
    assert response.data["code"] == "monoblock_busy"
    configure.assert_not_called()
    device.refresh_from_db()
    device.user.refresh_from_db()
    assert device.is_active is False
    assert device.user.is_active is False


def test_active_device_membership_reconciles_substream_on_create_move_and_delete(
    auth_client,
    superuser,
    monkeypatch,
):
    monkeypatch.setattr(ai, "AI_KEY", "key")

    def configured(cameras, source, *, analytics_scopes):
        return {
            "cameras": cameras,
            "source": source,
            "analytics_scopes": analytics_scopes,
            "capacity": 4,
            "pending": [],
            "processors": [
                {
                    "cam": camera,
                    "running": True,
                    "processor_alive": True,
                    "source": "sub",
                    "mode": "always_on",
                    "analytics_scope": analytics_scopes[camera],
                    "last_frame_at": "2026-09-01T08:00:00Z",
                }
                for camera in cameras
            ],
        }

    with patch.object(ai, "configure_always_on", side_effect=configured) as configure:
        device = _create_device(auth_client, superuser)
        moved = auth_client(superuser).patch(
            f"/api/cameras/monoblock-devices/{device.pk}/",
            {"camera_source": "cam3"},
            format="json",
        )
        deleted = auth_client(superuser).delete(
            f"/api/cameras/monoblock-devices/{device.pk}/",
        )

    assert moved.status_code == 200
    assert moved.data["always_on_source"] == "sub"
    assert moved.data["always_on_sync_status"] == "synced"
    assert deleted.status_code == 204
    assert [call.args for call in configure.call_args_list] == [
        (["cam2"], "sub"),
        (["cam3"], "sub"),
        ([], "sub"),
    ]
    assert [call.kwargs for call in configure.call_args_list] == [
        {"analytics_scopes": {"cam2": "shipping"}},
        {"analytics_scopes": {"cam3": "shipping"}},
        {"analytics_scopes": {}},
    ]


def test_active_device_creation_reports_pending_sync_without_rolling_back(
    auth_client,
    superuser,
    monkeypatch,
):
    monkeypatch.setattr(ai, "AI_KEY", "key")
    with patch.object(
        ai,
        "configure_always_on",
        side_effect=ai.AiUnavailable("camera PC offline"),
    ):
        response = auth_client(superuser).post(
            "/api/cameras/monoblock-devices/",
            {
                "name": "Моноблок фасовки",
                "username": "mono-offline",
                "password": "Complex-pass-123",
                "camera_source": "cam8",
            },
            format="json",
        )

    assert response.status_code == 202
    assert response.data["always_on_sync_status"] == "pending"
    assert "offline" in response.data["always_on_detail"]
    assert MonoblockDevice.objects.filter(camera_source="cam8").exists()


def test_active_device_creation_rejects_known_effective_capacity_before_commit(
    auth_client,
    superuser,
    django_user_model,
    monkeypatch,
):
    MonoblockCameraSettings.objects.create(camera_sources=["cam2"])
    monkeypatch.setattr(ai, "AI_KEY", "key")
    monkeypatch.setattr(
        ai,
        "cached_always_on_status",
        lambda: {"cameras": ["cam2"], "source": "sub", "capacity": 1},
    )

    with patch.object(ai, "configure_always_on") as configure:
        response = auth_client(superuser).post(
            "/api/cameras/monoblock-devices/",
            {
                "name": "Лишний моноблок",
                "username": "mono-over-capacity",
                "password": "Complex-pass-123",
                "camera_source": "cam3",
            },
            format="json",
        )

    assert response.status_code == 400
    assert response.data["code"] == "always_on_capacity_exceeded"
    configure.assert_not_called()
    assert not MonoblockDevice.objects.filter(camera_source="cam3").exists()
    assert not django_user_model.objects.filter(
        username="mono-over-capacity"
    ).exists()
