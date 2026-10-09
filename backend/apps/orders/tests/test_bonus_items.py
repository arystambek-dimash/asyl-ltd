"""Бонусный мешок: бесплатная позиция заказа — склад и машина как у всех, денег 0."""
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction

from apps.catalog.models import ClientPrice, Product
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem
from apps.sales.models import Department
from apps.shipments.services import dispatch_order
from apps.warehouse.models import StockItem
from apps.warehouse.services import receive_stock

pytestmark = pytest.mark.django_db


def _client():
    return Client.objects.create_with_user(first_name="Бонус", last_name="Клиент", phone="bonus")


def _product(bags=1000, name="Высший сорт KOROL 50кг"):
    product = Product.objects.create(name=name, color="Blue", weight_kg="50")
    StockItem.objects.create(product=product, bags=bags)
    return product


def _create(api, client, items, prices):
    Department.objects.get_or_create(code="main", defaults={"name": "Основной"})
    return api.post(
        "/api/orders/",
        {"client": client.id, "department": "main", "items": items, "prices": prices},
        format="json",
    )


def test_bonus_line_of_the_same_product_is_free_and_keeps_the_client_price(manager, api_as):
    client = _client()
    product = _product()

    response = _create(
        api_as(manager), client,
        [{"product": product.id, "quantity": 300}, {"product": product.id, "quantity": 3, "is_bonus": True}],
        {str(product.id): "8500"},
    )

    assert response.status_code == 201, response.data
    order = Order.objects.get()
    assert order.status == "confirmed"
    paid, bonus = order.items.order_by("is_bonus")
    assert (paid.unit_price, bonus.unit_price) == (Decimal("8500.00"), Decimal("0.00"))
    assert bonus.is_bonus and not paid.is_bonus
    assert order.ordered_bags == 303
    assert order.total_amount == Decimal("2550000.00")
    # Личный прайс клиента — цена платной строки, бонус его не обнуляет.
    assert ClientPrice.objects.get(client=client, product=product).price == Decimal("8500.00")
    # Порядок позиций в ответе не задан — сверяем набором.
    assert sorted(item["is_bonus"] for item in response.data["items"]) == [False, True]
    assert bonus.line_label.endswith("(бонус)")


def test_order_of_only_bonus_bags_is_rejected(manager, api_as):
    product = _product()

    response = _create(
        api_as(manager), _client(), [{"product": product.id, "quantity": 1, "is_bonus": True}], {},
    )

    assert response.status_code == 400
    assert "бонус идёт только к покупке" in str(response.data)
    assert not Order.objects.exists()


def test_two_bonus_lines_of_one_product_must_be_merged(manager, api_as):
    product = _product()

    response = _create(
        api_as(manager), _client(),
        [
            {"product": product.id, "quantity": 100},
            {"product": product.id, "quantity": 1, "is_bonus": True},
            {"product": product.id, "quantity": 1, "is_bonus": True},
        ],
        {str(product.id): "100"},
    )

    assert response.status_code == 400
    assert "Объедините повторяющиеся товары" in str(response.data)


def test_confirming_a_request_needs_prices_only_for_paid_lines(boss):
    from apps.orders.services import confirm_order

    order = Order.objects.create(client=_client(), status="pending")
    product = _product()
    paid = OrderItem.objects.create(order=order, product=product, quantity=100)
    bonus = OrderItem.objects.create(order=order, product=product, quantity=1, is_bonus=True, unit_price=0)

    confirm_order(order, boss, prices={paid.id: "1000.00"})

    order.refresh_from_db()
    bonus.refresh_from_db()
    assert order.status == "confirmed"
    assert bonus.unit_price == Decimal("0.00")
    assert order.total_amount == Decimal("100000.00")


def _shipped_with_bonus():
    order = Order.objects.create(
        client=_client(), status="shipped", settlement_intent="debt", payment_status="unpaid",
    )
    product = Product.objects.create(name="Мука", color="Белый", weight_kg="50")
    paid = OrderItem.objects.create(order=order, product=product, quantity=100, unit_price="100.00")
    bonus = OrderItem.objects.create(order=order, product=product, quantity=1, is_bonus=True, unit_price=0)
    return order, paid, bonus


def test_total_correction_is_spread_over_paid_bags_only(user_with_perms, api_as):
    user = user_with_perms("corrector", codes=["orders.correct_price"])
    order, paid, bonus = _shipped_with_bonus()

    response = api_as(user).post(
        f"/api/orders/{order.id}/correct-price/", {"total_amount": "12000.00"}, format="json",
    )

    assert response.status_code == 200, response.data
    paid.refresh_from_db()
    bonus.refresh_from_db()
    assert paid.unit_price == Decimal("120.00")
    assert bonus.unit_price == Decimal("0.00")


def test_per_item_correction_refuses_a_price_for_the_bonus_line(user_with_perms, api_as):
    user = user_with_perms("corrector", codes=["orders.correct_price"])
    order, paid, bonus = _shipped_with_bonus()
    api = api_as(user)

    refused = api.post(
        f"/api/orders/{order.id}/correct-price/",
        {"prices": {str(paid.id): "90.00", str(bonus.id): "90.00"}},
        format="json",
    )
    accepted = api.post(
        f"/api/orders/{order.id}/correct-price/", {"prices": {str(paid.id): "90.00"}}, format="json",
    )

    assert refused.status_code == 400
    assert refused.data["code"] == "bonus_item_price"
    assert accepted.status_code == 200, accepted.data
    order.refresh_from_db()
    assert order.total_amount == Decimal("9000.00")


def test_editing_a_confirmed_order_keeps_the_bonus_free(manager, api_as):
    product = _product()
    order = Order.objects.create(client=_client(), status="confirmed")
    OrderItem.objects.create(order=order, product=product, quantity=10, unit_price="50.00")

    response = api_as(manager).patch(
        f"/api/orders/{order.id}/",
        {
            "items": [
                {"product": product.id, "quantity": 200},
                {"product": product.id, "quantity": 2, "is_bonus": True},
            ],
            "prices": {str(product.id): "60.00"},
        },
        format="json",
    )

    assert response.status_code == 200, response.data
    prices = dict(order.items.values_list("is_bonus", "unit_price"))
    assert prices == {False: Decimal("60.00"), True: Decimal("0.00")}


def test_bonus_bags_leave_the_warehouse_with_the_order(boss, operator):
    product = Product.objects.create(name="Мука на отгрузку", color="Blue", weight_kg="50")
    receive_stock(product, 500, boss)
    order = Order.objects.create(client=_client(), status="confirmed", truck_number="01BONUS")
    OrderItem.objects.create(order=order, product=product, quantity=100, unit_price="10.00")
    OrderItem.objects.create(order=order, product=product, quantity=1, is_bonus=True, unit_price=0)

    dispatch_order(order, operator)

    order.refresh_from_db()
    assert order.status == "shipped"
    assert order.shipment.bags_loaded == 101
    assert StockItem.objects.get(product=product).bags == 399
    assert order.total_amount == Decimal("1000.00")


@pytest.mark.parametrize("price", [None, "5.00"])
def test_database_refuses_a_bonus_line_with_a_price(price):
    order = Order.objects.create(client=_client(), status="pending")

    with pytest.raises(IntegrityError), transaction.atomic():
        OrderItem.objects.create(order=order, product=_product(), quantity=1, is_bonus=True, unit_price=price)
