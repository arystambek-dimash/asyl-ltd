from unittest.mock import patch

import pytest

from apps.cameras import ai
from apps.cameras.models import MonoblockCameraSettings
from apps.catalog.models import Product
from apps.warehouse.models import Warehouse
from apps.warehouse.services import receive_stock

pytestmark = pytest.mark.django_db


READ_ENDPOINTS = (
    "/api/cameras/always-on-settings/",
    "/api/cameras/always-on-detections/",
    "/api/cameras/always-on-analytics/",
    "/api/cameras/always-on-production/?camera=cam3",
)
SHIPPING_READ_ENDPOINTS = (
    "/api/cameras/shipping-continuous-settings/",
    "/api/cameras/shipping-continuous-detections/",
    "/api/cameras/shipping-continuous-analytics/",
)


@pytest.fixture(autouse=True)
def disable_camera_pc(monkeypatch):
    monkeypatch.setattr(ai, "AI_KEY", "")


@pytest.fixture
def read_ai_status(ai247_camera):
    with patch.object(
        ai,
        "always_on_detections_cached",
        return_value={"processors": []},
    ):
        yield


def test_monoblock_viewer_can_get_all_ai_247_monitoring(
    auth_client,
    user_with_perms,
    read_ai_status,
):
    # Моноблок только для просмотра: одно право видит и AI 24/7.
    user = user_with_perms("ai-reader", codes=["monoblock.view"])

    for endpoint in READ_ENDPOINTS:
        assert auth_client(user).get(endpoint).status_code == 200


def test_shipping_reader_does_not_receive_stock_balances_or_warehouse_addresses(
    auth_client,
    user_with_perms,
    read_ai_status,
):
    loader = user_with_perms("ai-stock-private", codes=["monoblock.view"])
    warehouse = Warehouse.objects.create(
        code="private-address",
        name="Склад готовой продукции",
        address="Закрытая зона 7",
    )
    product = Product.objects.create(
        name="Приватный остаток",
        color="Red",
        weight_kg="50",
    )
    receive_stock(product, 127, user=None, warehouse=warehouse)

    response = auth_client(loader).get(
        "/api/cameras/always-on-production/?camera=cam3",
    )

    assert response.status_code == 200
    assert all("available_bags" not in row for row in response.data["products"])
    assert all("address" not in row for row in response.data["warehouses"])


def test_ai_247_monitoring_get_denies_unprivileged_client_and_anonymous_users(
    api_client,
    auth_client,
    user_with_perms,
    client_user,
    read_ai_status,
):
    unprivileged = user_with_perms("ai-outsider", codes=["orders.view"])

    for endpoint in READ_ENDPOINTS:
        assert auth_client(unprivileged).get(endpoint).status_code == 403
        assert auth_client(client_user).get(endpoint).status_code == 403
        assert api_client.get(endpoint).status_code == 401


def test_shipping_continuous_endpoints_require_employee_permissions(
    auth_client,
    user_with_perms,
    read_ai_status,
):
    shipping_user = user_with_perms(
        "shipping-continuous-reader",
        codes=["monoblock.view"],
    )
    ai247_only = user_with_perms(
        "ai247-only-reader",
        codes=["loader.confirm"],
    )

    for endpoint in SHIPPING_READ_ENDPOINTS:
        assert auth_client(shipping_user).get(endpoint).status_code == 200
        assert auth_client(ai247_only).get(endpoint).status_code == 403


MUTATION_REQUESTS = (
    ("put", "/api/cameras/always-on-settings/", {"camera_sources": []}),
    ("put", "/api/cameras/always-on-production/", {"camera": "cam3", "mappings": []}),
    ("post", "/api/cameras/always-on-production/batches/999/retry/", {}),
)


@pytest.mark.parametrize("method,endpoint,payload", MUTATION_REQUESTS)
def test_only_superuser_can_mutate_ai_247(
    auth_client,
    user_with_perms,
    method,
    endpoint,
    payload,
):
    # «Куда приходовать», режим и правки AI 24/7 — только суперпользователь.
    loader = user_with_perms(
        f"ai-mutation-loader-{method}-{len(endpoint)}",
        codes=["monoblock.view", "loader.confirm", "sys_permissions.manage"],
    )
    request = getattr(auth_client(loader), method)

    response = request(endpoint, payload, format="json")

    assert response.status_code == 403


def test_superuser_can_change_ai_247_settings(
    auth_client,
    admin_user,
):
    response = auth_client(admin_user).put(
        "/api/cameras/always-on-settings/",
        {"camera_sources": ["cam3"]},
        format="json",
    )

    assert response.status_code == 202
    row = MonoblockCameraSettings.objects.get(singleton=True)
    assert row.always_on_camera_sources == ["cam3"]
    assert row.updated_by == admin_user
