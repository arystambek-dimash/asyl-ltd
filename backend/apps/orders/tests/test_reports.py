from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.catalog.models import Product
from apps.clients.models import Client, Store
from apps.orders.models import Order, OrderItem, Payment, PaymentRefund
from apps.orders.reports import summary_report
from apps.shipments.models import Shipment

pytestmark = pytest.mark.django_db

URL = "/api/reports/summary/"


def _client(**kwargs):
    defaults = {"first_name": "И", "last_name": "П", "phone": "x"}
    return Client.objects.create_with_user(**{**defaults, **kwargs})


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
    Payment.objects.create(order=order, amount=Decimal(500),
                           method="cash", status="received")
    Payment.objects.create(order=order, amount=Decimal(400),
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
                             unit_price=Decimal(1000))

    data = auth_client(boss).get(URL).json()
    assert data["shipped"]["revenue"] == "15000.00"
    assert data["shipped"]["bags"] == 15
    assert data["shipped"]["orders"] == 2
    assert data["shipped"]["paid_amount"] == "5000.00"
    assert data["shipped"]["debt_amount"] == "10000.00"
    assert data["shipped"]["awaiting_amount"] == "0.00"
    assert data["debt_now"] == {
        "total": "10000.00", "orders": 1,
        "by_currency": {"KZT": "10000.00"}, "currency": "KZT",
        "overdue_by_currency": {},
        "overdue_currency": "KZT",
        "overdue_clients": 0,
    }


def test_period_shipment_split_uses_confirmed_net_balance(auth_client, boss):
    """Intent is provenance; paid/debt/awaiting are calculated from real money."""
    client = _client()
    product = _product()
    fully_paid_debt = _shipped_order(
        client, product, qty=1, price="1000", intent="debt"
    )
    partial_debt = _shipped_order(
        client, product, qty=1, price="1000", intent="debt"
    )
    instant_unpaid = _shipped_order(
        client, product, qty=1, price="500", intent="instant"
    )
    _confirmed_payment(fully_paid_debt, 1000)
    _confirmed_payment(partial_debt, 400)

    data = auth_client(boss).get(URL).json()
    shipped = data["shipped"]

    assert shipped["revenue_by_currency"] == {"KZT": "2500.00"}
    assert shipped["paid_amount_by_currency"] == {"KZT": "1400.00"}
    assert shipped["debt_amount_by_currency"] == {"KZT": "600.00"}
    assert shipped["awaiting_amount_by_currency"] == {"KZT": "500.00"}
    assert Decimal(shipped["revenue"]) == (
        Decimal(shipped["paid_amount"])
        + Decimal(shipped["debt_amount"])
        + Decimal(shipped["awaiting_amount"])
    )

    [day] = data["days"]
    assert day["paid_amount"] == "1400.00"
    assert day["debt_amount"] == "600.00"
    assert day["awaiting_amount"] == "500.00"

    [client_row] = data["clients"]
    assert client_row["paid_amount_by_currency"] == {"KZT": "1400.00"}
    assert client_row["debt_amount_by_currency"] == {"KZT": "600.00"}
    assert client_row["awaiting_amount_by_currency"] == {"KZT": "500.00"}
    by_id = {row["id"]: row for row in client_row["order_list"]}
    assert by_id[fully_paid_debt.id] == {
        "id": fully_paid_debt.id,
        "date": timezone.localdate().isoformat(),
        "bags": 1,
        "total": "1000.00",
        "paid_amount": "1000.00",
        "remaining_amount": "0.00",
        "currency": "KZT",
        "is_debt": False,
        "on_debt": False,
        "payment_status": "settled",
    }
    assert by_id[partial_debt.id]["remaining_amount"] == "600.00"
    assert by_id[partial_debt.id]["is_debt"] is True
    assert by_id[partial_debt.id]["payment_status"] == "partial"
    assert by_id[instant_unpaid.id]["remaining_amount"] == "500.00"
    assert by_id[instant_unpaid.id]["is_debt"] is False
    assert by_id[instant_unpaid.id]["payment_status"] == "unpaid"
    assert data["debt_now"]["by_currency"] == {"KZT": "600.00"}


def test_completed_refund_reopens_current_period_debt(auth_client, boss):
    order = _shipped_order(_client(), _product(), qty=1, price="1000")
    payment = _confirmed_payment(order, 1000)
    PaymentRefund.objects.create(
        payment=payment,
        amount="250.00",
        method="cash",
        status="completed",
        reason="Возврат",
        completed_at=timezone.now(),
    )
    Payment.objects.filter(pk=payment.pk).update(refunded_amount="250.00")

    data = auth_client(boss).get(URL).json()

    assert data["shipped"]["paid_amount"] == "750.00"
    assert data["shipped"]["debt_amount"] == "250.00"
    assert data["shipped"]["awaiting_amount"] == "0.00"
    assert data["debt_now"]["total"] == "250.00"
    [order_row] = data["clients"][0]["order_list"]
    assert order_row["payment_status"] == "partial"
    assert order_row["remaining_amount"] == "250.00"
    assert order_row["is_debt"] is True


def test_refund_is_cash_outflow_on_completion_day(auth_client, boss):
    """A later cash refund must not rewrite the old cashless receipt day."""
    old_stamp = timezone.now() - timedelta(days=10)
    today_stamp = timezone.now()
    order = _shipped_order(
        _client(), _product(), qty=1, price="100",
        shipped_at=old_stamp,
    )
    payment = _confirmed_payment(
        order, 100, method="invoice", confirmed_at=old_stamp
    )
    PaymentRefund.objects.create(
        payment=payment,
        amount="40.00",
        method="cash",
        status="completed",
        reason="Выдано из кассы",
        completed_at=today_stamp,
    )
    Payment.objects.filter(pk=payment.pk).update(refunded_amount="40.00")
    old_day = timezone.localdate(old_stamp).isoformat()
    today = timezone.localdate(today_stamp).isoformat()

    old = auth_client(boss).get(URL, {"from": old_day, "to": old_day}).json()
    assert old["income"] == {
        "total": "100.00",
        "gross": "100.00",
        "refunded": "0.00",
        "cash": "0.00",
        "cashless": "100.00",
        "payments": 1,
        "refunds": 0,
        "currency": "KZT",
        "by_currency": {"KZT": "100.00"},
        "gross_by_currency": {"KZT": "100.00"},
        "refunded_by_currency": {"KZT": "0.00"},
        "cash_by_currency": {"KZT": "0.00"},
        "cashless_by_currency": {"KZT": "100.00"},
    }

    refund_day = auth_client(boss).get(
        URL, {"from": today, "to": today}
    ).json()
    assert refund_day["income"]["total"] == "-40.00"
    assert refund_day["income"]["gross"] == "0.00"
    assert refund_day["income"]["refunded"] == "40.00"
    assert refund_day["income"]["cash"] == "-40.00"
    assert refund_day["income"]["cashless"] == "0.00"
    assert refund_day["income"]["payments"] == 0
    assert refund_day["income"]["refunds"] == 1
    [day] = refund_day["days"]
    assert day["received"] == "-40.00"
    assert day["gross_received"] == "0.00"
    assert day["refunded"] == "40.00"
    assert day["refunds"] == 1
    assert day["cash_by_currency"] == {"KZT": "-40.00"}
    assert day["cashless_by_currency"] == {"KZT": "0.00"}

    all_time = auth_client(boss).get(
        URL, {"from": old_day, "to": today}
    ).json()["income"]
    assert all_time["total"] == "60.00"
    assert all_time["gross"] == "100.00"
    assert all_time["refunded"] == "40.00"


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


def test_dynamic_department_filter(auth_client, user_with_perms):
    product = _product()
    _shipped_order(_client(), product, qty=1, price="1000",
                   department="main")
    _shipped_order(_client(), product, qty=3, price="1000",
                   department="field")

    main_only = user_with_perms("mainrep", codes=["reports.view", "orders.view"])
    data = auth_client(main_only).get(URL).json()
    assert data["shipped"]["revenue"] == "4000.00"

    data = auth_client(main_only).get(URL, {"department": "field"}).json()
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
                             unit_price=Decimal(500))

    data = auth_client(boss).get(URL).json()
    assert data["shipped"]["revenue"] == "2000.00"
    assert data["days"][0]["date"] == timezone.localdate().isoformat()


def test_report_does_not_add_kzt_and_usd(auth_client, boss):
    """1000 ₸ и 5 $ — это не «1005». Валюты идут раздельно.

    Остальная система уже считает по валютам (orders/debt.py); сводный отчёт
    оставался последним местом, где суммы складывались через курс «1:1».
    """
    client = _client()
    product = _product()
    kzt = _shipped_order(client, product, qty=1, price="1000")
    usd = _shipped_order(client, product, qty=1, price="5")
    usd.currency = "USD"
    usd.save(update_fields=["currency"])
    _confirmed_payment(kzt, 1000, method="cash")
    _confirmed_payment(usd, 5, method="cash")

    data = auth_client(boss).get(URL).json()

    assert data["income"]["by_currency"] == {"KZT": "1000.00", "USD": "5.00"}
    assert data["income"]["total"] == "1000.00"
    assert data["shipped"]["revenue_by_currency"] == {"KZT": "1000.00", "USD": "5.00"}
    assert data["shipped"]["paid_amount_by_currency"] == {
        "KZT": "1000.00",
        "USD": "5.00",
    }
    assert data["shipped"]["debt_amount_by_currency"] == {
        "KZT": "0.00",
        "USD": "0.00",
    }
    assert data["shipped"]["awaiting_amount_by_currency"] == {
        "KZT": "0.00",
        "USD": "0.00",
    }
    assert data["shipped"]["revenue"] == "1000.00"
    assert data["shipped"]["paid_amount"] == "1000.00"
    assert data["shipped"]["debt_amount"] == "0.00"
    assert data["shipped"]["awaiting_amount"] == "0.00"
    assert data["days"][0]["revenue"] == "1000.00"
    assert data["days"][0]["paid_amount"] == "1000.00"
    assert data["days"][0]["revenue_by_currency"] == {
        "KZT": "1000.00",
        "USD": "5.00",
    }
    assert data["days"][0]["cash_by_currency"] == {
        "KZT": "1000.00",
        "USD": "5.00",
    }
    assert data["days"][0]["cashless_by_currency"] == {
        "KZT": "0.00",
        "USD": "0.00",
    }
    assert data["days"][0]["received_by_currency"] == {
        "KZT": "1000.00",
        "USD": "5.00",
    }
    # Оба заказа погашены полностью — в дебиторке их нет ни в одной валюте.
    assert data["debt_now"]["by_currency"] == {}
    assert data["debt_now"]["orders"] == 0
    assert data["debt_now"]["overdue_by_currency"] == {}
    assert data["debt_now"]["overdue_clients"] == 0
    # Плоское поле осталось для совместимости, но валюты в нём уже не смешаны:
    # «1005» получиться не может, потому что раскладка идёт отдельно.
    assert data["income"]["cash_by_currency"] == {"KZT": "1000.00", "USD": "5.00"}


def test_report_debt_by_currency_keeps_outstanding_apart(auth_client, boss):
    client = _client()
    product = _product()
    kzt = _shipped_order(client, product, qty=1, price="1000")
    usd = _shipped_order(client, product, qty=1, price="5")
    usd.currency = "USD"
    usd.save(update_fields=["currency"])
    _confirmed_payment(kzt, 400, method="cash")  # остаток 600 ₸

    data = auth_client(boss).get(URL).json()

    assert data["debt_now"]["by_currency"] == {"KZT": "600.00", "USD": "5.00"}
    assert data["debt_now"]["total"] == "600.00"
    assert data["debt_now"]["orders"] == 2


def test_report_includes_small_overdue_dashboard_aggregate(auth_client, boss):
    client = _client()
    store = Store.objects.create(
        client=client,
        name="Сегодня",
        payment_schedule_type="monthly",
        payment_days=[timezone.localdate().day],
    )
    order = _shipped_order(client, _product(), qty=2, price="250")
    order.store = store
    order.save(update_fields=["store"])

    data = auth_client(boss).get(URL).json()["debt_now"]

    assert data["overdue_by_currency"] == {"KZT": "500.00"}
    assert data["overdue_currency"] == "KZT"
    assert data["overdue_clients"] == 1


def test_clients_breakdown_groups_orders_with_details(auth_client, boss):
    product = _product()
    gani = _client(first_name="Гани", last_name="Таскен")
    erzhan = _client(first_name="Ержан", last_name="Ко")
    debt_order = _shipped_order(gani, product, qty=10, price="1000", intent="debt")
    instant = _shipped_order(gani, product, qty=5, price="1000", intent="instant")
    _shipped_order(erzhan, product, qty=2, price="500", intent="debt")

    clients = auth_client(boss).get(URL).json()["clients"]

    # Крупнейший по выручке — первым.
    assert [c["name"] for c in clients] == ["Гани Таскен", "Ержан Ко"]
    top = clients[0]
    assert top["id"] == gani.id
    assert top["orders"] == 2
    assert top["bags"] == 15
    assert top["revenue_by_currency"] == {"KZT": "15000.00"}
    assert top["paid_amount_by_currency"] == {"KZT": "0.00"}
    assert top["debt_amount_by_currency"] == {"KZT": "10000.00"}
    assert top["awaiting_amount_by_currency"] == {"KZT": "5000.00"}

    by_id = {o["id"]: o for o in top["order_list"]}
    assert set(by_id) == {debt_order.id, instant.id}
    assert by_id[debt_order.id]["on_debt"] is True
    assert by_id[debt_order.id]["bags"] == 10
    assert by_id[debt_order.id]["total"] == "10000.00"
    assert by_id[debt_order.id]["currency"] == "KZT"
    assert by_id[instant.id]["on_debt"] is False
    assert by_id[instant.id]["is_debt"] is False
    assert by_id[instant.id]["payment_status"] == "unpaid"
    assert by_id[instant.id]["remaining_amount"] == "5000.00"


def test_clients_breakdown_respects_period_and_currencies(auth_client, boss):
    product = _product()
    client = _client()
    _shipped_order(client, product, qty=1, price="100",
                   shipped_at=timezone.now() - timedelta(days=10))
    usd = _shipped_order(client, product, qty=2, price="50")
    usd.currency = "USD"
    usd.save(update_fields=["currency"])

    today = timezone.localdate().isoformat()
    data = auth_client(boss).get(URL, {"from": today, "to": today}).json()

    [row] = data["clients"]
    # Валюты не складываются, старый заказ отрезан периодом.
    assert row["revenue_by_currency"] == {"USD": "100.00"}
    assert [o["id"] for o in row["order_list"]] == [usd.id]


def test_report_query_count_is_constant_for_period_cohort():
    client = _client()
    product = _product()

    def add_orders(count):
        for _ in range(count):
            order = _shipped_order(client, product, qty=1, price="100")
            _confirmed_payment(order, 25)

    def query_count():
        with CaptureQueriesContext(connection) as captured:
            summary_report(Order.objects.all())
        return len(captured)

    add_orders(2)
    small = query_count()
    add_orders(8)
    large = query_count()

    assert large == small, f"report summary: {small} -> {large} queries"
