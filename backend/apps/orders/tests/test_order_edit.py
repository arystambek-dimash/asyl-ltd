"""Редактирование заказа: позиции и цены — до начала загрузки."""
import pytest
from decimal import Decimal
from rest_framework.test import APIClient
from apps.catalog.models import Product
from apps.clients.models import Client, Store
from apps.orders.models import Order, OrderItem

pytestmark = pytest.mark.django_db

_seq = [0]


def _product(price="100.00"):
    _seq[0] += 1
    return Product.objects.create(
        name=f"P{_seq[0]}", color="Red", weight_kg="50", price=price)


def _order(status="pending", unit_price="100.00"):
    client = Client.objects.create(first_name="A", last_name="B", phone="x")
    order = Order.objects.create(client=client, status=status)
    OrderItem.objects.create(order=order, product=_product(), quantity=2,
                             unit_price=unit_price)
    return order


def _api(user):
    api = APIClient()
    api.force_authenticate(user)
    return api


def test_edit_items_with_prices(manager):
    o = _order(status="pending")
    p2 = _product()
    r = _api(manager).patch(f"/api/orders/{o.id}/", {
        "items": [{"product": p2.id, "quantity": 5}],
        "prices": {str(p2.id): "150.00"},
    }, format="json")
    assert r.status_code == 200
    o.refresh_from_db()
    items = list(o.items.all())
    assert len(items) == 1
    assert items[0].product_id == p2.id
    assert items[0].unit_price == Decimal("150.00")
    assert o.total_amount == Decimal("750.00")


def test_edit_items_on_confirmed_requires_prices(manager):
    o = _order(status="confirmed")
    p2 = _product()
    r = _api(manager).patch(f"/api/orders/{o.id}/", {
        "items": [{"product": p2.id, "quantity": 3}],
    }, format="json")
    assert r.status_code == 400
    r = _api(manager).patch(f"/api/orders/{o.id}/", {
        "items": [{"product": p2.id, "quantity": 3}],
        "prices": {str(p2.id): "110.00"},
    }, format="json")
    assert r.status_code == 200
    o.refresh_from_db()
    assert o.total_amount == Decimal("330.00")


def test_edit_items_locked_after_loading_starts(manager):
    o = _order(status="loading")
    p2 = _product()
    r = _api(manager).patch(f"/api/orders/{o.id}/", {
        "items": [{"product": p2.id, "quantity": 1}],
    }, format="json")
    assert r.status_code == 400
    assert "загрузки" in str(r.data.get("detail", ""))


def test_edit_cannot_empty_items(manager):
    o = _order(status="pending")
    r = _api(manager).patch(f"/api/orders/{o.id}/", {"items": []}, format="json")
    assert r.status_code == 400


def test_edit_fields_without_items(manager):
    o = _order(status="confirmed")
    store = Store.objects.create(client=o.client, name="S1")
    r = _api(manager).patch(f"/api/orders/{o.id}/", {
        "arrival_date": "2026-07-10", "store": store.id,
    }, format="json")
    assert r.status_code == 200
    o.refresh_from_db()
    assert str(o.arrival_date) == "2026-07-10"
    assert o.store_id == store.id
    # Позиции не тронуты.
    assert o.items.count() == 1


def test_edit_client_is_locked(manager):
    o = _order(status="pending")
    other = Client.objects.create(first_name="Z", last_name="Z", phone="z")
    r = _api(manager).patch(f"/api/orders/{o.id}/", {"client": other.id}, format="json")
    assert r.status_code == 400
    o.refresh_from_db()
    assert o.client.first_name == "A"


def test_foreign_store_rejected(manager):
    o = _order(status="pending")
    stranger = Client.objects.create(first_name="S", last_name="S", phone="s")
    foreign_store = Store.objects.create(client=stranger, name="Чужой")
    r = _api(manager).patch(f"/api/orders/{o.id}/", {"store": foreign_store.id}, format="json")
    assert r.status_code == 400


def test_edit_requires_orders_edit_perm(operator):
    # У оператора нет orders.edit — редактирование запрещено.
    o = _order(status="pending")
    r = _api(operator).patch(f"/api/orders/{o.id}/", {"arrival_date": "2026-07-10"}, format="json")
    assert r.status_code == 403
