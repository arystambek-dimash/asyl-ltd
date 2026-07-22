from unittest.mock import patch

import pytest

from apps.cameras import ai, services
from apps.cameras.models import MonoblockDevice
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
        "name": "Моноблок конвейера", "username": "mono-1",
        "password": "Complex-pass-123", "camera_source": "cam2",
        **overrides,
    }
    response = auth_client(superuser).post(
        "/api/cameras/monoblock-devices/", payload, format="json",
    )
    assert response.status_code == 201, response.data
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
    monkeypatch.setattr(ai, "AI_KEY", "key")
    device = _create_device(auth_client, superuser)
    order = Order.objects.create(
        client=Client.objects.create(first_name="A", last_name="B", phone="1"),
        status="confirmed",
    )
    response = auth_client(device.user).post(
        "/api/cameras/cam3/ai/", {"order_id": order.pk}, format="json",
    )
    assert response.status_code == 403
    order.refresh_from_db()
    assert order.status == "confirmed"


def test_device_order_list_is_scoped_to_queue_and_own_camera(auth_client, superuser):
    device = _create_device(auth_client, superuser)
    client = Client.objects.create(first_name="A", last_name="C", phone="2")
    waiting = Order.objects.create(client=client, status="confirmed")
    own = Order.objects.create(client=client, status="loading", loading_camera="cam2")
    Order.objects.create(client=client, status="loading", loading_camera="cam3")
    Order.objects.create(client=client, status="shipped")
    response = auth_client(device.user).get("/api/orders/")
    assert response.status_code == 200
    assert {row["id"] for row in response.data} == {waiting.pk, own.pk}
