"""Списковые эндпоинты не должны порождать запросы «на строку» (N+1).

Считаем SQL-запросы на маленькой и большой выборке: число обязано совпасть —
иначе где-то в сериализаторе появился запрос на каждый заказ/клиента.
"""
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient
from apps.catalog.models import ClientPrice, Product
from apps.clients.models import Client, Store
from apps.orders.models import Order, OrderItem, Payment, StatusChangeRequest
from apps.shipments.models import Shipment

pytestmark = pytest.mark.django_db

_seq = [0]


def _make_order():
    _seq[0] += 1
    n = _seq[0]
    product = Product.objects.create(
        name=f"P{n}", color="Red", weight_kg="50", price="100.00")
    client = Client.objects.create(first_name=f"C{n}", last_name="X", phone="x")
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
        name=f"Hint{n}", color="Blue", weight_kg="25", price="90.00")
    client = Client.objects.create(first_name=f"Hint{n}", last_name="X", phone="x")
    ClientPrice.objects.create(client=client, product=product, price="85.00")
    order = Order.objects.create(client=client, status="pending")
    OrderItem.objects.create(order=order, product=product, quantity=2)
    Payment.objects.create(
        order=order, amount="50", status="requested", recorded_by=user)
    StatusChangeRequest.objects.create(
        order=order, to_status="cancelled", requested_by=user)
    return order


def _make_portal_order(client, store):
    _seq[0] += 1
    n = _seq[0]
    product = Product.objects.create(
        name=f"Portal{n}", color="Green", weight_kg="50", price="100.00")
    order = Order.objects.create(client=client, store=store, status="pending")
    OrderItem.objects.create(order=order, product=product, quantity=1)
    Payment.objects.create(order=order, amount="10", status="requested")
    return order


def _count_queries(user, url):
    api = APIClient()
    # Свежий инстанс из БД — как в реальном запросе (JWT достаёт юзера заново),
    # иначе кэш effective_perm_codes переживает вызовы и искажает счётчик.
    user = type(user).objects.get(pk=user.pk)
    api.force_authenticate(user)
    with CaptureQueriesContext(connection) as ctx:
        response = api.get(url)
        assert response.status_code == 200
    return len(ctx)


def test_orders_list_query_count_is_constant(boss):
    for _ in range(2):
        _make_order()
    small = _count_queries(boss, "/api/orders/")
    for _ in range(6):
        _make_order()
    large = _count_queries(boss, "/api/orders/")
    assert large == small, f"orders list: {small} → {large} запросов (N+1)"


def test_orders_list_with_unfixed_prices_query_count_is_constant(boss):
    for _ in range(2):
        _make_unpriced_order(boss)
    small = _count_queries(boss, "/api/orders/")
    for _ in range(6):
        _make_unpriced_order(boss)
    large = _count_queries(boss, "/api/orders/")
    assert large == small, (
        f"orders list with price hints: {small} → {large} запросов (N+1)"
    )


def test_clients_list_query_count_is_constant(boss):
    for _ in range(2):
        _make_order()
    small = _count_queries(boss, "/api/clients/")
    for _ in range(6):
        _make_order()
    large = _count_queries(boss, "/api/clients/")
    assert large == small, f"clients list: {small} → {large} запросов (N+1)"


def test_client_debts_query_count_is_constant(boss):
    for _ in range(2):
        _make_order()
    small = _count_queries(boss, "/api/clients/debts/")
    for _ in range(6):
        _make_order()
    large = _count_queries(boss, "/api/clients/debts/")
    assert large == small, f"client debts: {small} → {large} запросов (N+1)"


def test_store_debt_detail_query_count_is_constant(boss):
    client = Client.objects.create(first_name="Store", last_name="Client", phone="x")
    store = Store.objects.create(client=client, name="Store")

    def add_order():
        _seq[0] += 1
        product = Product.objects.create(
            name=f"Debt{_seq[0]}", color="Red", weight_kg="50", price="100.00")
        order = Order.objects.create(
            client=client, store=store, status="shipped", settlement_intent="debt")
        OrderItem.objects.create(order=order, product=product, quantity=1)
        StatusChangeRequest.objects.create(
            order=order, to_status="cancelled", requested_by=boss)
        Shipment.objects.create(order=order)

    for _ in range(2):
        add_order()
    small = _count_queries(boss, f"/api/stores/{store.id}/debt-detail/")
    for _ in range(6):
        add_order()
    large = _count_queries(boss, f"/api/stores/{store.id}/debt-detail/")
    assert large == small, f"store debt detail: {small} → {large} запросов (N+1)"


def test_portal_orders_query_count_is_constant(client_user):
    client = Client.objects.create(
        first_name="Portal", last_name="Client", phone="x", user=client_user)
    store = Store.objects.create(client=client, name="Portal store")
    for _ in range(2):
        _make_portal_order(client, store)
    small = _count_queries(client_user, "/api/portal/orders/")
    for _ in range(6):
        _make_portal_order(client, store)
    large = _count_queries(client_user, "/api/portal/orders/")
    assert large == small, f"portal orders: {small} → {large} запросов (N+1)"
