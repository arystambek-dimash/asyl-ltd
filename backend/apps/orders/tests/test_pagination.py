"""Opt-in пагинация списков: без ?page ответ остаётся плоским списком.

Старые экраны (дашборд, посты, порталы) читают массив как есть, поэтому
страница включается только явным параметром — совместимость не ломается.
"""
from decimal import Decimal

import pytest

from apps.catalog.models import Product
from apps.clients.models import Client, Store
from apps.orders.models import Order, OrderItem, Payment

pytestmark = pytest.mark.django_db


def _client(n):
    return Client.objects.create_with_user(
        first_name=f"К{n}", last_name="Тест", phone=f"+7{n:010d}")


def _orders(count):
    product = Product.objects.create(
        name="Мука", color="Red", weight_kg=Decimal("50"),
        price=Decimal("1000"))
    for n in range(count):
        order = Order.objects.create(client=_client(n), status="confirmed")
        OrderItem.objects.create(
            order=order, product=product, quantity=1,
            unit_price=Decimal("1000"))


def test_orders_list_stays_flat_without_page(auth_client, boss):
    _orders(3)
    data = auth_client(boss).get("/api/orders/").json()
    assert isinstance(data, list)
    assert len(data) == 3


def test_orders_list_paginates_on_demand(auth_client, boss):
    _orders(3)
    data = auth_client(boss).get("/api/orders/?page=1&page_size=2").json()
    assert data["count"] == 3
    assert len(data["results"]) == 2
    assert data["next"] is not None

    tail = auth_client(boss).get("/api/orders/?page=2&page_size=2").json()
    assert len(tail["results"]) == 1
    assert tail["next"] is None


def test_page_size_is_capped(auth_client, boss):
    _orders(1)
    data = auth_client(boss).get("/api/orders/?page=1&page_size=9999").json()
    assert data["count"] == 1  # не 500 из-за дикого page_size


def test_clients_and_stores_paginate_on_demand(auth_client, boss):
    first = _client(1)
    _client(2)
    Store.objects.create(client=first, name="Магазин 1")
    Store.objects.create(client=first, name="Магазин 2")

    flat = auth_client(boss).get("/api/clients/").json()
    assert isinstance(flat, list)
    paged = auth_client(boss).get("/api/clients/?page=1&page_size=1").json()
    assert paged["count"] == 2
    assert len(paged["results"]) == 1

    flat_stores = auth_client(boss).get("/api/stores/").json()
    assert isinstance(flat_stores, list)
    paged_stores = auth_client(boss).get(
        "/api/stores/?page=1&page_size=1").json()
    assert paged_stores["count"] == 2
    assert len(paged_stores["results"]) == 1


def test_cashier_log_paginates_on_demand(auth_client, user_with_perms):
    from apps.eventlog.models import EventLog

    cashier = user_with_perms("cashier", codes=["payments.confirm"])
    order = Order.objects.create(client=_client(1), status="shipped")
    for n in range(3):
        EventLog.objects.create(
            event_type="payment", message=f"Событие {n}", order=order)

    flat = auth_client(cashier).get("/api/orders/cashier-log/").json()
    assert isinstance(flat, list)
    assert len(flat) == 3

    paged = auth_client(cashier).get(
        "/api/orders/cashier-log/?page=1&page_size=2").json()
    assert set(paged) >= {"count", "next", "previous", "results"}
    assert paged["count"] == 3
    assert len(paged["results"]) == 2
