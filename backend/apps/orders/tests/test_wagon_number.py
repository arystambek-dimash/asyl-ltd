from types import SimpleNamespace

import pytest
from rest_framework.exceptions import ValidationError

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import Order
from apps.orders.serializers import OrderSerializer
from apps.orders.services import set_truck_number
from apps.warehouse.models import StockItem

pytestmark = pytest.mark.django_db


@pytest.fixture
def wagon_order():
    client = Client.objects.create_with_user(
        first_name="Rail", last_name="Client", phone="wagon-number"
    )
    return Order.objects.create(
        client=client, transport_type="train", truck_number="00123456"
    )


def test_create_wagon_preserves_full_number(auth_client, manager, wagon_order):
    product = Product.objects.create(
        name="Мука", color="Red", weight_kg="50", price="100.00"
    )
    StockItem.objects.create(product=product, bags=500)
    response = auth_client(manager).post(
        "/api/orders/",
        {
            "client": wagon_order.client_id,
            "transport_type": "train",
            "truck_number": "00123456",
            "items": [{"product": product.pk, "quantity": 5}],
        },
        format="json",
    )
    assert response.status_code == 201, response.data
    assert response.data["truck_number"] == "00123456"
    assert Order.objects.get(pk=response.data["id"]).truck_number == "00123456"


@pytest.mark.parametrize(
    "value", ["1234567", "123456789", "123ABC02", "１２３４５６７８"]
)
def test_wagon_rejects_invalid_number(auth_client, manager, wagon_order, value):
    response = auth_client(manager).patch(
        f"/api/orders/{wagon_order.pk}/",
        {
            "truck_number": value,
        },
        format="json",
    )
    assert response.status_code == 400
    assert "truck_number" in response.data["detail"]
    wagon_order.refresh_from_db()
    assert wagon_order.truck_number == "00123456"


@pytest.mark.parametrize("value", ["", "00012345"])
def test_wagon_number_can_be_blank_or_changed_before_loading(
    auth_client, manager, wagon_order, value
):
    response = auth_client(manager).patch(
        f"/api/orders/{wagon_order.pk}/", {"truck_number": value}, format="json"
    )
    assert response.status_code == 200, response.data
    wagon_order.refresh_from_db()
    assert wagon_order.truck_number == value


@pytest.mark.parametrize("status", ["arrived", "loading", "loaded", "shipped"])
def test_wagon_number_remains_locked_after_physical_start(
    auth_client, manager, wagon_order, status
):
    Order.objects.filter(pk=wagon_order.pk).update(status=status)
    api_client = auth_client(manager)
    same = api_client.patch(
        f"/api/orders/{wagon_order.pk}/", {"truck_number": "00123456"}, format="json"
    )
    assert same.status_code == 200, same.data
    changed = api_client.patch(
        f"/api/orders/{wagon_order.pk}/", {"truck_number": "00123457"}, format="json"
    )
    assert changed.status_code == 400
    assert changed.data["code"] == "truck_number_locked"
    wagon_order.refresh_from_db()
    assert wagon_order.truck_number == "00123456"


@pytest.mark.parametrize(
    "transport,number", [("truck", "123ABC02"), ("train", "00012345")]
)
def test_changing_transport_and_number_validates_final_pair(
    auth_client, manager, wagon_order, transport, number
):
    Order.objects.filter(pk=wagon_order.pk).update(
        transport_type="train" if transport == "truck" else "truck"
    )
    response = auth_client(manager).patch(
        f"/api/orders/{wagon_order.pk}/",
        {
            "transport_type": transport,
            "truck_number": number,
        },
        format="json",
    )
    assert response.status_code == 200, response.data
    wagon_order.refresh_from_db()
    assert (wagon_order.transport_type, wagon_order.truck_number) == (transport, number)


def test_type_only_change_to_train_does_not_keep_vehicle_plate(
    auth_client, manager, wagon_order
):
    Order.objects.filter(pk=wagon_order.pk).update(
        transport_type="truck", truck_number="123ABC02"
    )
    response = auth_client(manager).patch(
        f"/api/orders/{wagon_order.pk}/", {"transport_type": "train"}, format="json"
    )
    assert response.status_code == 400
    wagon_order.refresh_from_db()
    assert (wagon_order.transport_type, wagon_order.truck_number) == (
        "truck",
        "123ABC02",
    )


def test_direct_number_service_applies_wagon_validation(manager, wagon_order):
    with pytest.raises(ValidationError):
        set_truck_number(wagon_order, "INVALID", manager)
    wagon_order.refresh_from_db()
    assert wagon_order.truck_number == "00123456"


@pytest.mark.parametrize("status", ["confirmed", "loading"])
@pytest.mark.parametrize(
    "unchanged_fields",
    [
        {"truck_number": "1234567"},
        {"transport_type": "train"},
        {"truck_number": "1234567", "transport_type": "train"},
    ],
)
def test_unrelated_patch_preserves_unchanged_legacy_wagon_number(
    auth_client, manager, wagon_order, status, unchanged_fields
):
    Order.objects.filter(pk=wagon_order.pk).update(
        truck_number="1234567", status=status
    )
    response = auth_client(manager).patch(
        f"/api/orders/{wagon_order.pk}/",
        {**unchanged_fields, "notes": "Уточнено время погрузки"},
        format="json",
    )
    assert response.status_code == 200, response.data
    wagon_order.refresh_from_db()
    assert wagon_order.truck_number == "1234567"
    assert wagon_order.transport_type == "train"
    assert wagon_order.notes == "Уточнено время погрузки"


def test_stale_legacy_number_cannot_replace_corrected_number(manager, wagon_order):
    Order.objects.filter(pk=wagon_order.pk).update(truck_number="1234567")
    wagon_order.refresh_from_db()
    serializer = OrderSerializer(
        wagon_order,
        data={"truck_number": "1234567", "notes": "Устаревшая форма"},
        partial=True,
        context={"request": SimpleNamespace(user=manager)},
    )
    assert serializer.is_valid(), serializer.errors
    # Another request corrected the identifier after this form was validated.
    Order.objects.filter(pk=wagon_order.pk).update(truck_number="00123456")
    with pytest.raises(ValidationError):
        serializer.save()
    wagon_order.refresh_from_db()
    assert wagon_order.truck_number == "00123456"
    assert wagon_order.notes == ""
