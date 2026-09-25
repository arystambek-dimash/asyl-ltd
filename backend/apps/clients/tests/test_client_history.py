import pytest
from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem, Payment
from apps.clients.services import client_history

pytestmark = pytest.mark.django_db


def _order(client, status, qty=10, paid=None, intent="debt"):
    p = Product.objects.create(
        name=f"P{status}{qty}{paid}", color="Red", weight_kg="50"
    )
    o = Order.objects.create(
        client=client, status=status, settlement_intent=intent, payment_status="unpaid"
    )
    OrderItem.objects.create(
        order=o, product=p, quantity=qty, unit_price="100.00"
    )  # 100*qty
    if paid:
        Payment.objects.create(order=o, amount=paid, status="confirmed")
    return o


def test_history_summary():
    c = Client.objects.create_with_user(first_name="A", last_name="B", phone="x")
    _order(c, "shipped", qty=10, paid="500")  # фин: revenue 1000, paid 500, долг 500
    _order(c, "pending", qty=5)  # не финансовый
    _order(c, "rejected", qty=3)  # отклонён
    h = client_history(c)
    assert h["summary"]["revenue"] == "1000.00"
    assert h["summary"]["paid"] == "500.00"
    assert h["summary"]["debt"] == "500.00"
    assert h["summary"]["orders_count"] == 1  # только финансовые


def test_history_summary_paid_includes_prepaid_orders():
    """«Оплачено за всё время» — все финансовые заказы, не только отгруженные."""
    c = Client.objects.create_with_user(first_name="A", last_name="B", phone="x")
    _order(c, "shipped", qty=2, paid="200")
    _order(c, "loading", qty=3, paid="300")  # предоплата до отгрузки
    h = client_history(c)
    assert h["summary"]["revenue"] == "500.00"
    assert h["summary"]["paid"] == "500.00"
    assert h["summary"]["debt"] == "0.00"


def test_history_rows():
    c = Client.objects.create_with_user(first_name="A", last_name="B", phone="x")
    o1 = _order(c, "shipped", qty=10, paid="500")
    o2 = _order(c, "rejected", qty=3)
    h = client_history(c)
    # Продажи — все заказы клиента, свежие сверху, со статусом.
    assert [r["id"] for r in h["sales"]] == [o2.id, o1.id]
    sale = next(r for r in h["sales"] if r["id"] == o1.id)
    assert sale["amount"] == "1000.00"
    assert sale["currency"] == "KZT"
    assert sale["status"] == "shipped"
    assert sale["settlement_intent"] == "debt"
    assert sale["bags"] == 10
    assert len(sale["items"]) == 1 and sale["items"][0]["qty"] == 10
    # Погашения — денежные оплаты с привязкой к документу.
    assert len(h["payments"]) == 1
    assert h["payments"][0]["order_id"] == o1.id
    assert h["payments"][0]["amount"] == "500.00"
    assert h["payments"][0]["currency"] == "KZT"
    # Подписи строк — из labels.py, фронт своих словарей не держит.
    assert h["payments"][0]["method_label"] == "Наличные"
    assert h["payments"][0]["status_label"] == "Оплачено"
    # Долги — только отгруженные в долг с остатком.
    assert [r["id"] for r in h["debts"]] == [o1.id]
    assert h["debts"][0]["remaining"] == "500.00"


def test_history_rows_mark_what_counts_in_totals():
    """Итог по списку на странице клиента складывает только то, что входит в
    сводку: продажи в обороте и нетто подтверждённых оплат финансовых заказов."""
    c = Client.objects.create_with_user(first_name="A", last_name="B", phone="x")
    shipped = _order(c, "shipped", qty=10, paid="600")
    Payment.objects.filter(order=shipped).update(refunded_amount="100")
    Payment.objects.create(order=shipped, amount="200", status="rejected")
    Payment.objects.create(order=shipped, amount="50", status="received")
    cancelled = _order(c, "cancelled", qty=3, paid="300")
    h = client_history(c)

    sales = {r["id"]: r for r in h["sales"]}
    assert sales[shipped.id]["is_financial"] is True
    assert sales[cancelled.id]["is_financial"] is False

    counted = sorted(
        (r["status"], r["order_id"] == shipped.id, r["counted_amount"])
        for r in h["payments"]
    )
    assert counted == [
        ("confirmed", False, "0.00"),  # отменённый заказ — не оплата клиента
        ("confirmed", True, "500.00"),  # 600 минус возврат 100
        ("received", True, "0.00"),
        ("rejected", True, "0.00"),
    ]
    # Сумма засчитанного совпадает со сводкой «Оплачено».
    assert h["summary"]["paid"] == "500.00"


def test_history_excludes_service_debt_method():
    c = Client.objects.create_with_user(first_name="A", last_name="B", phone="x")
    o = _order(c, "shipped", qty=1)
    Payment.objects.create(order=o, amount="100", method="debt", status="confirmed")
    h = client_history(c)
    assert h["payments"] == []


def test_history_endpoint(boss, api_as):
    c = Client.objects.create_with_user(first_name="A", last_name="B", phone="x", currency="USD")
    _order(c, "shipped", qty=10)
    r = api_as(boss).get(f"/api/clients/{c.id}/history/")
    assert r.status_code == 200
    assert r.data["client"]["id"] == c.id
    assert r.data["client"]["currency"] == "USD"
    assert "summary" in r.data
    assert "sales" in r.data and "payments" in r.data and "debts" in r.data
