"""Календарь отгрузки: работа грузчика по дням вместо таблицы заказов."""

from datetime import date, timedelta

import pytest
from django.utils import timezone

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem
from apps.shipments.models import Shipment

pytestmark = pytest.mark.django_db


def _order(client, product, *, status, arrival_date=None):
    order = Order.objects.create(client=client, status=status, arrival_date=arrival_date)
    OrderItem.objects.create(order=order, product=product, quantity=10, unit_price="100.00")
    return order


@pytest.fixture
def board():
    client = Client.objects.create_with_user(first_name="Календарь", phone="+7 700 111 11 11")
    product = Product.objects.create(name="Мука", color="White", weight_kg="50")
    return client, product


def test_calendar_counts_waiting_and_shipped_days(user_with_perms, board, auth_client):
    client, product = board
    today = timezone.localdate()
    # Второй день того же месяца, в какой бы день ни шёл прогон.
    other_day = today + timedelta(days=1 if today.day == 1 else -1)
    _order(client, product, status="confirmed", arrival_date=today)
    _order(client, product, status="confirmed", arrival_date=today)
    _order(client, product, status="confirmed", arrival_date=other_day)
    shipped = _order(client, product, status="shipped", arrival_date=today)
    Shipment.objects.create(order=shipped, shipped_at=timezone.now())
    viewer = user_with_perms("calendar-viewer", codes=["monoblock.view"])

    response = auth_client(viewer).get(f"/api/orders/shipping-calendar/?month={today.strftime('%Y-%m')}")

    assert response.status_code == 200
    assert response.data["month"] == today.strftime("%Y-%m")
    days = {row["day"]: row for row in response.data["days"]}
    assert days[today.isoformat()]["waiting"] == 2
    assert days[today.isoformat()]["shipped"] == 1
    assert days[other_day.isoformat()]["waiting"] == 1


def test_calendar_month_is_bounded_and_validated(user_with_perms, board, auth_client):
    client, product = board
    _order(client, product, status="confirmed", arrival_date=date(2026, 1, 15))
    _order(client, product, status="confirmed", arrival_date=date(2026, 2, 3))
    viewer = user_with_perms("calendar-bounds", codes=["loader.view"])

    january = auth_client(viewer).get("/api/orders/shipping-calendar/?month=2026-01")
    bad = auth_client(viewer).get("/api/orders/shipping-calendar/?month=2026-13")

    assert [row["day"] for row in january.data["days"]] == ["2026-01-15"]
    assert bad.status_code == 400
    assert bad.data["code"] == "bad_month"


def test_calendar_needs_a_board_permission(user_with_perms, auth_client):
    outsider = user_with_perms("calendar-outsider", codes=["clients.view"])

    assert auth_client(outsider).get("/api/orders/shipping-calendar/").status_code == 403
