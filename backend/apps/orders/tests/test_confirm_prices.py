import pytest
from decimal import Decimal
from apps.catalog.models import Product, ClientPrice
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem
from apps.orders.services import confirm_order
from rest_framework.exceptions import ValidationError

pytestmark = pytest.mark.django_db


def _order(boss, qty=2):
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    p = Product.objects.create(name="P", color="Red", weight_kg="50", price="100.00")
    o = Order.objects.create(client=c, status="pending")
    it = OrderItem.objects.create(order=o, product=p, quantity=qty)
    return o, it, c, p


def test_confirm_writes_unit_price_and_upserts_client_price(boss):
    o, it, c, p = _order(boss)
    confirm_order(o, boss, prices={it.id: "10000.00"})
    o.refresh_from_db(); it.refresh_from_db()
    assert o.status == "confirmed"
    assert it.unit_price == Decimal("10000.00")
    assert o.total_amount == Decimal("20000.00")
    # запомнилось как цена клиента
    cp = ClientPrice.objects.get(client=c, product=p)
    assert cp.price == Decimal("10000.00")


def test_confirm_rejected_when_price_missing(boss):
    o, it, c, p = _order(boss)
    with pytest.raises(ValidationError) as e:
        confirm_order(o, boss, prices={})
    assert e.value.detail["code"] == "price_required"
    o.refresh_from_db()
    assert o.status == "pending"  # не подтвердился


def test_confirm_rejected_when_price_zero(boss):
    o, it, c, p = _order(boss)
    with pytest.raises(ValidationError):
        confirm_order(o, boss, prices={it.id: "0"})


def test_confirm_updates_existing_client_price(boss):
    o, it, c, p = _order(boss)
    ClientPrice.objects.create(client=c, product=p, price="5000.00")
    confirm_order(o, boss, prices={it.id: "12000.00"})
    cp = ClientPrice.objects.get(client=c, product=p)
    assert cp.price == Decimal("12000.00")  # перезаписалась


def test_confirm_does_not_change_personal_price_without_permission(
        user_with_perms):
    user = user_with_perms("confirm-only", codes=["orders.confirm"])
    order, item, client, product = _order(user)
    ClientPrice.objects.create(client=client, product=product, price="5000.00")

    confirm_order(order, user, prices={item.id: "12000.00"})

    item.refresh_from_db()
    assert item.unit_price == Decimal("12000.00")
    assert ClientPrice.objects.get(client=client, product=product).price == Decimal("5000.00")


def test_confirm_api_response_contains_fresh_prices(auth_client, manager):
    o, item, _client, _product = _order(manager)

    response = auth_client(manager).post(
        f"/api/orders/{o.id}/confirm/",
        {"prices": {str(item.id): "10000.00"}}, format="json")

    assert response.status_code == 200
    assert response.data["items"][0]["price"] == "10000.00"
    assert response.data["total_amount"] == "20000.00"
