"""API номеров тягача и прицепа: форма заказа и поиск."""

import pytest

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.notifications.models import Notification
from apps.orders.models import Order, OrderItem
from apps.warehouse.models import StockItem

pytestmark = pytest.mark.django_db


@pytest.fixture
def product():
    item = Product.objects.create(name="Мука", color="Red", weight_kg="50")
    StockItem.objects.create(product=item, bags=10_000)
    return item


def _client(name="Азамат", **fields):
    return Client.objects.create_with_user(first_name=name, last_name="К", phone=name, **fields)


def _order(client, product, *, quantity=1360, **fields):
    fields.setdefault("status", "confirmed")
    order = Order.objects.create(client=client, **fields)
    OrderItem.objects.create(order=order, product=product, quantity=quantity, unit_price="10.00")
    return order


# ── Форма заказа ─────────────────────────────────────────────────────────────


def test_create_normalizes_pair_and_records_its_owner(auth_client, manager, product):
    client = _client()
    response = auth_client(manager).post("/api/orders/", {
        "client": client.pk, "truck_number": "07 kg 695 adt", "trailer_number": "07-KG-837-PB",
        "items": [{"product": product.pk, "quantity": 5}],
    }, format="json")

    assert response.status_code == 201, response.data
    assert (response.data["truck_number"], response.data["trailer_number"]) == ("07KG695ADT", "07KG837PB")
    order = Order.objects.get(pk=response.data["id"])
    assert order.truck_number_set_by == manager


def test_create_rejects_an_impossible_trailer(auth_client, manager, product):
    response = auth_client(manager).post("/api/orders/", {
        "client": _client().pk, "truck_number": "403BJN13", "trailer_number": "AB",
        "items": [{"product": product.pk, "quantity": 5}],
    }, format="json")

    assert response.status_code == 400
    assert "trailer_number" in response.data["detail"]


def test_patch_trailer_keeps_truck_and_notifies_client(auth_client, manager, product):
    order = _order(_client(), product, truck_number="403BJN13")

    response = auth_client(manager).patch(
        f"/api/orders/{order.pk}/", {"trailer_number": "07kg837pb"}, format="json")

    assert response.status_code == 200, response.data
    order.refresh_from_db()
    assert (order.truck_number, order.trailer_number) == ("403BJN13", "07KG837PB")
    assert Notification.objects.filter(
        client=order.client, text=f"Заказ №{order.pk}: машина 403 BJN 13, прицеп 07 KG 837 PB").exists()


def test_patch_trailer_on_site_does_not_tell_the_client(auth_client, manager, product):
    """Карточка заказа: прицеп дописан, когда машина уже на территории, —
    номер клиенту не показывается, и уведомления о нём нет."""
    order = _order(_client(), product, status="arrived", truck_number="403BJN13", truck_number_set_by=manager)

    response = auth_client(manager).patch(
        f"/api/orders/{order.pk}/", {"trailer_number": "07kg837pb"}, format="json")

    assert response.status_code == 200, response.data
    order.refresh_from_db()
    assert (order.truck_number, order.trailer_number) == ("403BJN13", "07KG837PB")
    assert not Notification.objects.filter(client=order.client).exists()


def test_patch_to_train_drops_the_trailer(auth_client, manager, product):
    order = _order(_client(), product, truck_number="", trailer_number="07KG837PB")

    response = auth_client(manager).patch(
        f"/api/orders/{order.pk}/", {"transport_type": "train", "truck_number": "00123456"}, format="json")

    assert response.status_code == 200, response.data
    order.refresh_from_db()
    assert (order.transport_type, order.truck_number, order.trailer_number) == ("train", "00123456", "")


@pytest.mark.parametrize("search", ["07KG695", "07 695 adt", "07695ADT", "07695", "07 695", "837pb", "07 KG 837"])
def test_order_search_finds_truck_and_trailer_in_any_spelling(auth_client, manager, product, search):
    found = _order(_client(), product, truck_number="07KG695ADT", trailer_number="07KG837PB")
    _order(_client("Берик"), product, truck_number="403BJN13")

    rows = auth_client(manager).get("/api/orders/", {"search": search}).data

    assert [row["id"] for row in rows] == [found.pk]
