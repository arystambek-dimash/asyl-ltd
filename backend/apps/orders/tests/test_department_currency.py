"""Сводка по отделам не складывает валюты.

Заказ в долларах и заказ в тенге в одном отделе — обычное дело, а «выручка»
одним числом означала бы, что 5 $ прибавили к 1000 ₸.
"""
import pytest

from apps.catalog.models import Product
from apps.clients.models import Client, Department
from apps.orders.models import Order, OrderItem

pytestmark = pytest.mark.django_db


def _order(client, department, currency, price, qty=1, status="shipped"):
    product = Product.objects.create(
        name=f"P-{currency}-{price}", color="Red", weight_kg="50", price="1")
    order = Order.objects.create(
        client=client, status=status, department=department.code, currency=currency)
    OrderItem.objects.create(order=order, product=product, quantity=qty, unit_price=price)
    return order


def _summary(auth_client, boss):
    response = auth_client(boss).get("/api/orders/department-summary/")
    assert response.status_code == 200
    return {row["code"]: row for row in response.json()}


def test_revenue_is_split_by_currency(boss, auth_client):
    department = Department.objects.create(code="main2", name="Отдел", color="#000")
    client = Client.objects.create(first_name="A", last_name="B", phone="x")
    _order(client, department, "KZT", "1000")
    _order(client, department, "USD", "5")

    row = _summary(auth_client, boss)[department.code]

    # Раньше здесь было «1005» — сумма, не существующая ни в одной валюте.
    assert row["revenue_by_currency"] == {"KZT": "1000.00", "USD": "5.00"}
    assert row["revenue_currency"] == "KZT"
    assert row["revenue"] == "1000.00"


def test_single_currency_department_reports_it_plainly(boss, auth_client):
    department = Department.objects.create(code="usd_only", name="Экспорт", color="#000")
    client = Client.objects.create(first_name="C", last_name="D", phone="y")
    _order(client, department, "USD", "500", qty=2)

    row = _summary(auth_client, boss)[department.code]

    assert row["revenue_currency"] == "USD"
    assert row["revenue"] == "1000.00"
    assert row["revenue_by_currency"] == {"USD": "1000.00"}


def test_drafts_stay_out_of_revenue(boss, auth_client):
    department = Department.objects.create(code="draft_dep", name="Черновики", color="#000")
    client = Client.objects.create(first_name="E", last_name="F", phone="z")
    _order(client, department, "KZT", "700", status="draft")

    row = _summary(auth_client, boss)[department.code]

    # Заказ есть, но в оборот он ещё не входит.
    assert row["orders"] == 1
    assert row["revenue_by_currency"] == {}
    assert row["revenue"] == "0.00"
