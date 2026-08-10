"""Сводка по отделам показывает не только выручку, но и расчёты.

Одна цифра выручки не отвечает на главный вопрос дашборда — сколько из неё
уже получено, а сколько висит долгом. Разбивка обязана считаться по тем же
правилам, что «Касса» и выписка (``Order.is_debt``), иначе цифры разойдутся.
"""
import pytest

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.sales.models import Department
from apps.orders.models import Order, OrderItem, Payment

pytestmark = pytest.mark.django_db


def _order(client, department, price, *, qty=1, status="shipped", intent="debt"):
    # Товар уникален по (имя, цвет, вес) — нумеруем, чтобы в одном тесте
    # можно было создать несколько заказов с одинаковой ценой.
    product = Product.objects.create(
        name=f"P-{Product.objects.count() + 1}", color="Red", weight_kg="50",
        price="1")
    order = Order.objects.create(
        client=client, status=status, department=department.code,
        settlement_intent=intent,
    )
    OrderItem.objects.create(
        order=order, product=product, quantity=qty, unit_price=price)
    return order


def _pay(order, amount):
    return Payment.objects.create(
        order=order, amount=amount, method="cash", status="confirmed")


def _summary(auth_client, boss):
    response = auth_client(boss).get("/api/orders/department-summary/")
    assert response.status_code == 200
    return {row["code"]: row for row in response.json()}


def test_summary_splits_paid_partial_and_unpaid(boss, auth_client):
    department = Department.objects.create(
        code="mill", name="Мельница", color="#315FD5")
    client = Client.objects.create_with_user(first_name="Опл", last_name="Ата", phone="1")

    _pay(_order(client, department, "1000"), "1000")   # оплачен полностью
    _pay(_order(client, department, "1000"), "400")    # частично
    _order(client, department, "1000")                 # не оплачен

    row = _summary(auth_client, boss)["mill"]

    assert row["paid_orders"] == 1
    assert row["partial_orders"] == 1
    assert row["unpaid_orders"] == 1
    assert row["revenue"] == "3000.00"
    assert row["paid"] == "1400.00"
    # Долг — только непогашенные остатки отгруженных заказов «в долг».
    assert row["debt"] == "1600.00"
    assert row["debt_orders"] == 2


def test_summary_debt_matches_is_debt_rule(boss, auth_client):
    """Моментальная оплата долгом не считается, даже если денег ещё нет."""
    department = Department.objects.create(
        code="city", name="Нью-Сити", color="#1F9D6A")
    client = Client.objects.create_with_user(first_name="Мгн", last_name="Овен", phone="2")

    _order(client, department, "500", intent="instant")
    _order(client, department, "700", intent="debt")

    row = _summary(auth_client, boss)["city"]

    assert row["debt"] == "700.00", "в долг попадает только settlement_intent=debt"
    assert row["debt_orders"] == 1
    assert row["unpaid_orders"] == 2, "неоплаченными числятся оба"


def test_summary_ignores_non_financial_orders(boss, auth_client):
    """Черновик и «на рассмотрении» не попадают ни в выручку, ни в счётчики."""
    department = Department.objects.create(
        code="draft", name="Черновики", color="#888888")
    client = Client.objects.create_with_user(first_name="Чер", last_name="Новик", phone="3")

    _order(client, department, "900", status="draft")
    _order(client, department, "900", status="pending")

    row = _summary(auth_client, boss)["draft"]

    assert row["revenue"] == "0.00"
    assert row["debt"] == "0.00"
    assert (row["paid_orders"], row["partial_orders"], row["unpaid_orders"]) == (0, 0, 0)
    assert row["orders"] == 2, "в общем счётчике заказов они по-прежнему видны"


def test_summary_keeps_debt_split_by_currency(boss, auth_client):
    """Долги разных валют не складываются — как и выручка."""
    department = Department.objects.create(
        code="mix", name="Смешанный", color="#C58A35")
    client = Client.objects.create_with_user(first_name="Мул", last_name="Ьти", phone="4")

    kzt = _order(client, department, "1000")
    usd = _order(client, department, "20")
    Order.objects.filter(pk=usd.pk).update(currency="USD")

    row = _summary(auth_client, boss)["mix"]

    assert row["debt_by_currency"] == {"KZT": "1000.00", "USD": "20.00"}
    assert kzt.currency == "KZT"
