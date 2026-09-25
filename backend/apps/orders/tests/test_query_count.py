"""Списковые эндпоинты не должны порождать запросы «на строку» (N+1).

Считаем SQL-запросы на маленькой и большой выборке: число обязано совпасть —
иначе где-то в сериализаторе появился запрос на каждый заказ/клиента.
"""
import pytest

from apps.catalog.models import ClientPrice, Product
from apps.clients.models import Client, Store
from apps.orders.models import Order, OrderItem, Payment, StatusChangeRequest

pytestmark = pytest.mark.django_db

_seq = [0]


def _make_order():
    _seq[0] += 1
    n = _seq[0]
    product = Product.objects.create(
        name=f"P{n}", color="Red", weight_kg="50")
    client = Client.objects.create_with_user(first_name=f"C{n}", last_name="X", phone="x")
    order = Order.objects.create(client=client, status="shipped")
    OrderItem.objects.create(order=order, product=product, quantity=2,
                             unit_price="120.00")
    Payment.objects.create(order=order, amount="100", status="confirmed")
    Payment.objects.create(order=order, amount="50", status="received")
    StatusChangeRequest.objects.create(order=order, to_status="cancelled")
    return order


def _make_unpriced_order(user):
    """Exercise every relation traversed by OrderSerializer."""
    _seq[0] += 1
    n = _seq[0]
    product = Product.objects.create(
        name=f"Hint{n}", color="Blue", weight_kg="25")
    client = Client.objects.create_with_user(first_name=f"Hint{n}", last_name="X", phone="x")
    ClientPrice.objects.create(client=client, product=product, price="85.00")
    order = Order.objects.create(client=client, status="pending")
    OrderItem.objects.create(order=order, product=product, quantity=2)
    Payment.objects.create(
        order=order, amount="50", status="requested", recorded_by=user)
    StatusChangeRequest.objects.create(
        order=order, to_status="cancelled", requested_by=user)
    return order


def _make_portal_order(client, store, truck_set_by=None):
    _seq[0] += 1
    n = _seq[0]
    product = Product.objects.create(
        name=f"Portal{n}", color="Green", weight_kg="50")
    # Номер задал сотрудник: признак transport_locked читает владельца номера.
    order = Order.objects.create(
        client=client, store=store, status="pending",
        truck_number="403BJN13" if truck_set_by else "", truck_number_set_by=truck_set_by)
    OrderItem.objects.create(order=order, product=product, quantity=1)
    Payment.objects.create(order=order, amount="10", status="requested")
    return order


@pytest.mark.parametrize(
    ("url", "seed"),
    [
        ("/api/orders/", lambda user: _make_order()),
        # Цены не зафиксированы: сериализатор подсказывает цену клиента.
        ("/api/orders/", _make_unpriced_order),
        ("/api/clients/", lambda user: _make_order()),
    ],
    ids=["orders", "orders-price-hints", "clients"],
)
def test_list_query_count_is_constant(boss, count_queries, url, seed):
    for _ in range(2):
        seed(boss)
    small = count_queries(boss, url)
    for _ in range(6):
        seed(boss)
    large = count_queries(boss, url)
    assert large == small, f"{url}: {small} → {large} запросов (N+1)"


def test_portal_orders_query_count_is_constant(client_user, boss, count_queries):
    client = Client.objects.create_with_user(
        first_name="Portal", last_name="Client", phone="x", user=client_user)
    store = Store.objects.create(client=client, name="Portal store")
    for index in range(2):
        _make_portal_order(client, store, truck_set_by=boss if index % 2 else None)
    small = count_queries(client_user, "/api/portal/orders/")
    for index in range(6):
        _make_portal_order(client, store, truck_set_by=boss if index % 2 else None)
    large = count_queries(client_user, "/api/portal/orders/")
    assert large == small, f"portal orders: {small} → {large} запросов (N+1)"
