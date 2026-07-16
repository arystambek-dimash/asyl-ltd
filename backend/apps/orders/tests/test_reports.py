from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.catalog.models import Product
from apps.clients.models import Client, Store
from apps.orders.models import Order, OrderItem, Payment
from apps.shipments.models import Shipment

pytestmark = pytest.mark.django_db

URL = "/api/reports/summary/"


def _client(**kwargs):
    defaults = {"first_name": "И", "last_name": "П", "phone": "x"}
    return Client.objects.create(**{**defaults, **kwargs})


def _product(price="1000", name="Мука", color="Red", weight="50"):
    return Product.objects.create(
        name=name, color=color, weight_kg=Decimal(weight), price=Decimal(price))


def _shipped_order(client, product, qty=10, price="1000", *,
                   intent="debt", shipped_at=None, department="main"):
    order = Order.objects.create(
        client=client, status="shipped", settlement_intent=intent,
        department=department)
    OrderItem.objects.create(
        order=order, product=product, quantity=qty, unit_price=Decimal(price))
    Shipment.objects.create(
        order=order, shipped_at=shipped_at or timezone.now())
    return order


def _confirmed_payment(order, amount, method="cash", user=None, confirmed_at=None):
    return Payment.objects.create(
        order=order, amount=Decimal(str(amount)), method=method,
        status="confirmed", confirmed_by=user,
        confirmed_at=confirmed_at or timezone.now())


def test_requires_reports_view(auth_client, manager, boss):
    assert auth_client(manager).get(URL).status_code == 403
    assert auth_client(boss).get(URL).status_code == 200


def test_income_split_cash_cashless(auth_client, boss):
    order = _shipped_order(_client(), _product(), qty=10, price="1000")
    _confirmed_payment(order, 3000, method="cash")
    _confirmed_payment(order, 2000, method="card")
    _confirmed_payment(order, 1000, method="kaspi")
    _confirmed_payment(order, 700, method="invoice")
    # Непоступившие деньги в кассу не входят.
    Payment.objects.create(order=order, amount=Decimal("500"),
                           method="cash", status="received")
    Payment.objects.create(order=order, amount=Decimal("400"),
                           method="cash", status="rejected")

    data = auth_client(boss).get(URL).json()
    assert data["income"]["cash"] == "3000.00"
    assert data["income"]["cashless"] == "3700.00"
    assert data["income"]["total"] == "6700.00"
    assert data["income"]["payments"] == 4


def test_income_day_is_confirmation_date(auth_client, boss):
    """Оплата, записанная вчера и подтверждённая сегодня, — в сегодняшнем дне."""
    order = _shipped_order(_client(), _product())
    payment = _confirmed_payment(order, 1000, confirmed_at=timezone.now())
    Payment.objects.filter(pk=payment.pk).update(
        paid_at=timezone.now() - timedelta(days=1))

    today = timezone.localdate().isoformat()
    data = auth_client(boss).get(URL, {"from": today, "to": today}).json()
    assert data["income"]["total"] == "1000.00"
    assert len(data["days"]) == 1
    assert data["days"][0]["date"] == today
    assert data["days"][0]["received"] == "1000.00"


def test_shipped_revenue_and_debt(auth_client, boss):
    product = _product()
    _shipped_order(_client(), product, qty=10, price="1000", intent="debt")
    order_instant = _shipped_order(_client(), product, qty=5, price="1000",
                                   intent="instant")
    _confirmed_payment(order_instant, 5000)
    # Неотгруженный заказ в выручку не входит.
    pending = Order.objects.create(client=_client(), status="confirmed")
    OrderItem.objects.create(order=pending, product=product, quantity=99,
                             unit_price=Decimal("1000"))

    data = auth_client(boss).get(URL).json()
    assert data["shipped"]["revenue"] == "15000.00"
    assert data["shipped"]["bags"] == 15
    assert data["shipped"]["orders"] == 2
    assert data["shipped"]["debt_amount"] == "10000.00"
    assert data["debt_now"] == {"total": "10000.00", "orders": 1}


def test_deleted_orders_excluded(auth_client, boss):
    order = _shipped_order(_client(), _product(), qty=10, price="1000")
    _confirmed_payment(order, 1000)
    Order.all_objects.filter(pk=order.pk).update(deleted_at=timezone.now())

    data = auth_client(boss).get(URL).json()
    assert data["shipped"]["revenue"] == "0.00"
    assert data["income"]["total"] == "0.00"
    assert data["debt_now"]["orders"] == 0


def test_period_filter(auth_client, boss):
    product = _product()
    _shipped_order(_client(), product, qty=1, price="1000",
                   shipped_at=timezone.now() - timedelta(days=10))
    _shipped_order(_client(), product, qty=2, price="1000")

    today = timezone.localdate()
    data = auth_client(boss).get(URL, {
        "from": (today - timedelta(days=1)).isoformat(),
        "to": today.isoformat(),
    }).json()
    assert data["shipped"]["revenue"] == "2000.00"
    assert len(data["days"]) == 1

    assert auth_client(boss).get(
        URL, {"from": "2026-02-30"}).status_code == 400
    assert auth_client(boss).get(
        URL, {"from": "2026-07-10", "to": "2026-07-01"}).status_code == 400


def test_department_scoping(auth_client, user_with_perms):
    product = _product()
    _shipped_order(_client(department="main"), product, qty=1, price="1000",
                   department="main")
    _shipped_order(_client(department="field"), product, qty=3, price="1000",
                   department="field")

    main_only = user_with_perms("mainrep", codes=["reports.view", "orders.view"])
    data = auth_client(main_only).get(URL).json()
    assert data["shipped"]["revenue"] == "1000.00"

    all_depts = user_with_perms("allrep", codes=[
        "reports.view", "orders.view", "dept2.view_all"])
    data = auth_client(all_depts).get(URL).json()
    assert data["shipped"]["revenue"] == "4000.00"
    data = auth_client(all_depts).get(URL, {"department": "field"}).json()
    assert data["shipped"]["revenue"] == "3000.00"


def test_store_filter(auth_client, boss):
    product = _product()
    client = _client()
    first = Store.objects.create(client=client, name="Первый")
    second = Store.objects.create(client=client, name="Второй")
    first_order = _shipped_order(client, product, qty=2, price="1000")
    first_order.store = first
    first_order.save(update_fields=["store"])
    second_order = _shipped_order(client, product, qty=5, price="1000")
    second_order.store = second
    second_order.save(update_fields=["store"])

    data = auth_client(boss).get(URL, {"store": first.id}).json()

    assert data["shipped"]["revenue"] == "2000.00"
    assert data["debt_now"]["total"] == "2000.00"
    assert auth_client(boss).get(URL, {"store": "bad"}).status_code == 400


def test_manual_shipped_without_shipment_falls_back_to_created(auth_client, boss):
    """Заказ, переведённый в shipped вручную (без Shipment), не теряется."""
    order = Order.objects.create(client=_client(), status="shipped",
                                 settlement_intent="debt")
    OrderItem.objects.create(order=order, product=_product(), quantity=4,
                             unit_price=Decimal("500"))

    data = auth_client(boss).get(URL).json()
    assert data["shipped"]["revenue"] == "2000.00"
    assert data["days"][0]["date"] == timezone.localdate().isoformat()
