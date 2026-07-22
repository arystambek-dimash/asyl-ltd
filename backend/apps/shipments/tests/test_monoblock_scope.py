import pytest
from rest_framework.exceptions import PermissionDenied

from apps.cameras.models import MonoblockDevice
from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem
from apps.shipments.models import Shipment
from apps.shipments.services import finish_loading, record_count, rewind_loading
from apps.warehouse.services import receive_stock


pytestmark = pytest.mark.django_db


def _device_user(django_user_model, camera="cam2"):
    user = django_user_model.objects.create_user(
        username=f"device-{camera}", password="pass12345",
    )
    MonoblockDevice.objects.create(
        user=user,
        name=f"Моноблок {camera}",
        camera_source=camera,
    )
    return user


def _loading_order(boss, camera="cam3"):
    product = Product.objects.create(
        name="Scope product", color="Blue", weight_kg="50", price="100.00",
    )
    receive_stock(product, 100, boss)
    client = Client.objects.create(
        first_name="Scope", last_name="Client", phone="scope",
    )
    order = Order.objects.create(
        client=client,
        status="loading",
        truck_number="01SCOPE",
        loading_camera=camera,
    )
    OrderItem.objects.create(order=order, product=product, quantity=5)
    shipment = Shipment.objects.create(
        order=order,
        truck_number=order.truck_number,
        bags_loaded=11,
    )
    return order, shipment, product


def _assert_loading_unchanged(order, shipment, product):
    order.refresh_from_db()
    persisted_shipment = Shipment.objects.get(pk=shipment.pk)
    product.stock.refresh_from_db()
    assert order.status == "loading"
    assert order.loading_camera == "cam3"
    assert persisted_shipment.bags_loaded == 11
    assert persisted_shipment.shipped_at is None
    assert product.stock.bags == 100


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("load", {"bags": 17}),
        ("finish-loading", {}),
        ("rewind-loading", {}),
    ],
)
def test_monoblock_cannot_mutate_another_cameras_order(
    path, payload, api_client, django_user_model, boss,
):
    user = _device_user(django_user_model)
    order, shipment, product = _loading_order(boss)
    api_client.force_authenticate(user)

    response = api_client.post(
        f"/api/orders/{order.pk}/{path}/", payload, format="json",
    )

    assert response.status_code == 404
    _assert_loading_unchanged(order, shipment, product)


def test_monoblock_can_update_its_own_loading_workflow(
    api_client, django_user_model, boss,
):
    user = _device_user(django_user_model)
    order, shipment, _ = _loading_order(boss, camera="cam2")
    api_client.force_authenticate(user)

    response = api_client.post(
        f"/api/orders/{order.pk}/load/", {"bags": 17}, format="json",
    )

    assert response.status_code == 200
    order.refresh_from_db()
    shipment.refresh_from_db()
    assert order.status == "loading"
    assert order.loading_camera == "cam2"
    assert shipment.bags_loaded == 17


def test_monoblock_cannot_release_another_cameras_binding(
    api_client, django_user_model,
):
    user = _device_user(django_user_model)
    client = Client.objects.create(
        first_name="Bound", last_name="Elsewhere", phone="bound",
    )
    order = Order.objects.create(
        client=client,
        status="confirmed",
        loading_camera="cam3",
    )
    api_client.force_authenticate(user)

    response = api_client.post(
        f"/api/orders/{order.pk}/loading-camera/", {"camera": ""}, format="json",
    )

    assert response.status_code == 403
    order.refresh_from_db()
    assert order.status == "confirmed"
    assert order.loading_camera == "cam3"


@pytest.mark.parametrize("operation", ["load", "finish", "rewind"])
def test_service_layer_rejects_another_monoblocks_order(
    operation, django_user_model, boss,
):
    user = _device_user(django_user_model)
    order, shipment, product = _loading_order(boss)

    with pytest.raises(PermissionDenied):
        if operation == "load":
            record_count(order, 17, user)
        elif operation == "finish":
            finish_loading(order, user)
        else:
            rewind_loading(order, user)

    _assert_loading_unchanged(order, shipment, product)
