from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient
from apps.catalog.models import Product
from apps.clients.models import Client, Store
from apps.orders.models import Order, OrderItem, Payment

pytestmark = pytest.mark.django_db


def _api(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def _product():
    return Product.objects.create(name="P", color="Red", weight_kg="50", price="100.00")


def _order(client, product, qty=2, payment_status="unpaid", paid=None, store=None):
    order = Order.objects.create(
        client=client,
        store=store,
        status="shipped",
        payment_status=payment_status,
    )
    OrderItem.objects.create(
        order=order, product=product, quantity=qty, unit_price="100.00")
    if paid is not None:
        # Учтённые деньги — оплата, прошедшая всю цепочку (подтверждена кассиром).
        Payment.objects.create(order=order, amount=paid, status="confirmed")
    return order


def test_client_debts_aggregate_by_client(boss):
    p = _product()
    c = Client.objects.create(first_name="A", last_name="B", phone="1")
    store = Store.objects.create(
        client=c, name="S", payment_schedule_type="monthly", payment_days=[25]
    )
    _order(c, p, qty=3, payment_status="unpaid", store=store)  # 300
    _order(c, p, qty=2, payment_status="partial", paid="50.00")  # 150 left
    _order(c, p, qty=1, payment_status="settled", paid="100.00")  # excluded

    r = _api(boss).get("/api/clients/debts/")

    assert r.status_code == 200
    row = next(x for x in r.data if x["client_id"] == c.id)
    assert row["debt_total"] == "450.00"
    assert row["orders_count"] == 2
    assert row["unpaid_count"] == 1
    assert row["partial_count"] == 1
    assert row["stores_count"] == 1


def test_client_debts_filters_department_store_date_and_remaining(boss):
    p = _product()
    main = Client.objects.create(
        first_name="Main", last_name="Client", phone="1")
    field = Client.objects.create(
        first_name="Field", last_name="Client", phone="2")
    main_store = Store.objects.create(client=main, name="Main store")
    other_store = Store.objects.create(client=main, name="Other store")
    field_store = Store.objects.create(client=field, name="Field store")
    old = _order(main, p, qty=9, store=other_store)
    _order(main, p, qty=3, store=main_store)
    _order(field, p, qty=7, store=field_store)
    Order.objects.filter(pk=old.pk).update(
        created_at=timezone.now() - timedelta(days=20))
    today = timezone.localdate().isoformat()

    r = _api(boss).get("/api/clients/debts/", {
        "department": "main",
        "store": main_store.id,
        "date_from": today,
        "date_to": today,
        "remaining_min": "250",
        "remaining_max": "350",
    })

    assert r.status_code == 200
    assert [row["client_id"] for row in r.data] == [main.id]
    assert r.data[0]["debt_total"] == "300.00"

    assert _api(boss).get("/api/clients/debts/", {
        "remaining_min": "400", "remaining_max": "100",
    }).status_code == 400


def test_client_debt_detail_returns_unsettled_orders(boss):
    p = _product()
    c = Client.objects.create(first_name="A", last_name="B", phone="1")
    unpaid = _order(c, p, qty=2, payment_status="unpaid")
    settled = _order(c, p, qty=1, payment_status="settled", paid="100.00")

    r = _api(boss).get(f"/api/clients/{c.id}/debt-detail/")

    assert r.status_code == 200
    assert r.data["debt_total"] == "200.00"
    # За всё время: погашенный заказ входит в общую задолженность и оплаты.
    assert r.data["lifetime_total"] == "300.00"
    assert r.data["lifetime_paid"] == "100.00"
    assert r.data["overdue_total"] == "0.00"
    ids = [row["id"] for row in r.data["orders"]]
    assert unpaid.id in ids
    assert settled.id not in ids


def test_client_debt_detail_overdue_on_payment_day(boss):
    """Открытое окно оплаты магазина = остаток по его заказам просрочен."""
    from django.utils import timezone
    p = _product()
    c = Client.objects.create(first_name="A", last_name="B", phone="1")
    today = timezone.localdate()
    store = Store.objects.create(
        client=c, name="S", payment_schedule_type="monthly",
        payment_days=[today.day])
    _order(c, p, qty=2, payment_status="unpaid", store=store)  # 200
    _order(c, p, qty=1, payment_status="unpaid")  # 100, без магазина

    r = _api(boss).get(f"/api/clients/{c.id}/debt-detail/")

    assert r.status_code == 200
    assert r.data["debt_total"] == "300.00"
    assert r.data["overdue_total"] == "200.00"
