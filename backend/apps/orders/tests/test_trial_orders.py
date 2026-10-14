"""Пробная партия: пометка заказа «Проба» ставится в форме и снимается правкой."""
import pytest

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem
from apps.warehouse.models import StockItem

pytestmark = pytest.mark.django_db


def _product():
    product = Product.objects.create(name="Проба P", color="Red", weight_kg="50")
    StockItem.objects.create(product=product, bags=500)
    return product


def _create(api, client, product, **fields):
    return api.post(
        "/api/orders/",
        {"client": client.id, "items": [{"product": product.id, "quantity": 2}], **fields},
        format="json",
    )


def test_new_order_is_trial_only_when_chosen(manager, api_as):
    client = Client.objects.create_with_user(first_name="Арыс", last_name="Ержан", phone="x")
    product = _product()
    api = api_as(manager)

    trial = _create(api, client, product, is_trial=True)
    regular = _create(api, client, product)

    assert trial.status_code == 201 and regular.status_code == 201
    assert trial.data["is_trial"] is True
    assert Order.objects.get(pk=trial.data["id"]).is_trial is True
    assert regular.data["is_trial"] is False
    assert Order.objects.get(pk=regular.data["id"]).is_trial is False


def test_trial_mark_is_changed_by_editing_the_order(manager, api_as):
    client = Client.objects.create_with_user(first_name="A", last_name="B", phone="x")
    order = Order.objects.create(client=client, status="confirmed", is_trial=True)
    OrderItem.objects.create(order=order, product=_product(), quantity=2, unit_price="100.00")

    response = api_as(manager).patch(f"/api/orders/{order.id}/", {"is_trial": False}, format="json")

    assert response.status_code == 200
    order.refresh_from_db()
    assert order.is_trial is False
