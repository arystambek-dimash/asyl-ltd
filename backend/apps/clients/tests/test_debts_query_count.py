"""Список должников не должен порождать запросы «на клиента» или «на заказ».

Считаем SQL-запросы на маленькой и большой выборке: число обязано совпасть —
иначе остаток считается построчно, и касса на телефоне ждёт секунды.
"""
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient
from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem, Payment

pytestmark = pytest.mark.django_db


def _debtors(count, orders_each, product, offset):
    for c in range(count):
        client = Client.objects.create_with_user(
            first_name=f"Должник{offset + c}", last_name="X", phone=f"7{offset + c:05d}")
        for o in range(orders_each):
            # Нечётные заказы частично оплачены — статус ставим как сервис оплат.
            partial = bool(o % 2)
            order = Order.objects.create(
                client=client, status="shipped",
                payment_status="partial" if partial else "unpaid")
            OrderItem.objects.create(
                order=order, product=product, quantity=2, unit_price="100.00")
            if partial:
                Payment.objects.create(order=order, amount="50", status="confirmed")


def _debts(api):
    with CaptureQueriesContext(connection) as ctx:
        response = api.get("/api/clients/debts/")
    assert response.status_code == 200
    return len(ctx.captured_queries), response.data


def test_debts_query_count_does_not_grow_with_data(boss):
    product = Product.objects.create(
        name="P", color="Red", weight_kg="50", price="100.00")
    api = APIClient()
    api.force_authenticate(boss)

    _debtors(2, 1, product, offset=0)
    # Первый запрос подгружает сотрудника пользователя (кэш на объекте) — не считаем его.
    _debts(api)
    small, rows = _debts(api)
    assert len(rows) == 2

    _debtors(20, 5, product, offset=100)
    big, rows = _debts(api)
    assert len(rows) == 22
    assert big == small
    # Остаток: 2 × 100 за заказ; нечётные заказы частично оплачены на 50.
    biggest = max(rows, key=lambda row: row["orders_count"])
    assert biggest["orders_count"] == 5
    assert biggest["debt_total"] == "900.00"
    assert biggest["partial_count"] == 2


def test_debts_skip_settled_orders_but_recheck_the_rest(boss):
    """Погашенные по payment_status не считаются; остальные проверяются по факту."""
    product = Product.objects.create(
        name="P", color="Red", weight_kg="50", price="100.00")
    api = APIClient()
    api.force_authenticate(boss)
    client = Client.objects.create_with_user(first_name="Дана", last_name="X", phone="70001")
    settled = Order.objects.create(client=client, status="shipped", payment_status="settled")
    OrderItem.objects.create(order=settled, product=product, quantity=1, unit_price="100.00")
    Payment.objects.create(order=settled, amount="100", status="confirmed")
    # Статус ещё «частично», но оплата уже покрыла всё — точный остаток ноль.
    covered = Order.objects.create(client=client, status="shipped", payment_status="partial")
    OrderItem.objects.create(order=covered, product=product, quantity=1, unit_price="100.00")
    Payment.objects.create(order=covered, amount="100", status="confirmed")
    open_order = Order.objects.create(client=client, status="shipped", payment_status="partial")
    OrderItem.objects.create(order=open_order, product=product, quantity=2, unit_price="100.00")
    Payment.objects.create(order=open_order, amount="50", status="confirmed")

    _, rows = _debts(api)

    assert [(row["client_name"], row["debt_total"], row["orders_count"]) for row in rows] == [
        ("Дана X", "150.00", 1),
    ]
