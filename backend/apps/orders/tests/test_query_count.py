"""Списковые эндпоинты не должны порождать запросы «на строку» (N+1).

Считаем SQL-запросы на маленькой и большой выборке: число обязано совпасть —
иначе где-то в сериализаторе появился запрос на каждый заказ/клиента.
"""
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient
from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem, Payment, StatusChangeRequest

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


def _count_queries(user, url):
    api = APIClient()
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
