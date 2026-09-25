"""«Оплаты» кассы → «Ждут оплаты»: отгруженный заказ без долга сразу виден кассе.

Заказ из кабинета клиента, где оплату не выбрали (или она не дошла), раньше не
попадал ни в очередь оплат, ни в «Долги клиентов».
"""

from decimal import Decimal

import pytest
from django.utils import timezone

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.eventlog.models import EventLog
from apps.orders.models import Order, OrderItem, Payment

pytestmark = pytest.mark.django_db


@pytest.fixture
def cashier(user_with_perms, departments):
    return user_with_perms(
        "mill-cashier",
        codes=["payments.confirm", "payments.create"],
        department=departments[0],
    )


def _client(department, name="Клиент"):
    return Client.objects.create_with_user(first_name=name, phone="+7 (705) 565-65-65", department=department)


def _order(client, *, status="shipped", intent="pending", amount="100.00", **fields):
    order = Order.objects.create(
        client=client,
        status=status,
        department=client.department.code if client.department else "",
        settlement_intent=intent,
        payment_method="debt" if intent == "debt" else "pending",
        **fields,
    )
    product = Product.objects.create(name=f"Мука {order.pk}", color="White", weight_kg="50")
    OrderItem.objects.create(order=order, product=product, quantity=1, unit_price=Decimal(amount))
    return order


def _confirmed(order, amount):
    Payment.objects.create(
        order=order, amount=Decimal(amount), method="cash", status="confirmed", confirmed_at=timezone.now()
    )
    order.payment_status = "partial"
    order.save(update_fields=["payment_status"])


def _ids(response):
    assert response.status_code == 200, response.data
    rows = response.data["results"] if isinstance(response.data, dict) else response.data
    return [row["id"] for row in rows]


def test_shipped_orders_without_debt_wait_for_payment(auth_client, cashier, departments):
    mill, city = departments
    client = _client(mill)
    not_chosen = _order(client, intent="pending")
    partly_paid = _order(client, intent="instant", amount="300.00")
    _confirmed(partly_paid, "100.00")
    _order(client, intent="debt")  # уже в «Долгах клиентов»
    _order(client, status="confirmed")  # ещё не отгружен
    settled = _order(client, intent="instant")
    settled.payment_status = "settled"
    settled.save(update_fields=["payment_status"])
    _order(client, amount="0.00")  # без цены платить нечего
    archived = _order(client)
    archived.deleted_at = timezone.now()
    archived.save(update_fields=["deleted_at"])
    foreign = _order(_client(city, "Чужой"))
    api = auth_client(cashier)

    assert set(_ids(api.get("/api/orders/awaiting-payment/"))) == {not_chosen.pk, partly_paid.pk}
    assert set(_ids(api.get("/api/orders/awaiting-payment/?department=mill"))) == {not_chosen.pk, partly_paid.pk}
    assert _ids(api.get("/api/orders/awaiting-payment/?department=city")) == []
    # Чужой отдел кассе не открыт — ни в списке, ни действием.
    assert foreign.pk not in _ids(api.get("/api/orders/awaiting-payment/"))
    assert api.post(f"/api/orders/{foreign.pk}/to-debt/").status_code == 404

    page = api.get("/api/orders/awaiting-payment/?page=1&page_size=1")
    assert page.data["count"] == 2
    row = page.data["results"][0]
    assert {"remaining_amount", "client_name", "pending_payments", "settlement_intent"} <= row.keys()

    summary = api.get("/api/orders/awaiting-payment/?summary=1")
    assert summary.status_code == 200
    assert summary.data == [{"currency": "KZT", "amount": "300.00", "count": 2}]


def test_cashier_moves_awaiting_order_to_debt(auth_client, cashier, departments):
    order = _order(_client(departments[0]), intent="pending", amount="250.00")
    api = auth_client(cashier)

    response = api.post(f"/api/orders/{order.pk}/to-debt/")

    assert response.status_code == 200, response.data
    assert response.data["settlement_intent"] == "debt"
    order.refresh_from_db()
    assert (order.settlement_intent, order.payment_method, order.debt_requested) == ("debt", "debt", False)
    assert _ids(api.get("/api/orders/awaiting-payment/")) == []
    debts = api.get("/api/clients/debts/")
    assert [(row["client_id"], row["debt_total"]) for row in debts.data] == [(order.client_id, "250.00")]
    event = EventLog.objects.get(order=order, event_type="debt_override")
    assert event.payload["amount"] == "250.00"
    # Повторный перевод ничего не меняет.
    again = api.post(f"/api/orders/{order.pk}/to-debt/")
    assert again.status_code == 400
    assert again.data["code"] == "already_debt"


def test_debt_move_waits_for_payment_in_progress(auth_client, cashier, departments):
    order = _order(_client(departments[0]), intent="instant")
    Payment.objects.create(order=order, amount=Decimal("100.00"), method="cash", status="requested")
    api = auth_client(cashier)

    # Незавершённая оплата не прячет заказ из «Ждут оплаты», но в долг его не перевести.
    assert _ids(api.get("/api/orders/awaiting-payment/")) == [order.pk]
    response = api.post(f"/api/orders/{order.pk}/to-debt/")

    assert response.status_code == 400
    assert response.data["code"] == "payment_in_progress"
    order.refresh_from_db()
    assert order.settlement_intent == "instant"


def test_awaiting_payment_needs_cashier_permission(auth_client, user_with_perms, departments):
    order = _order(_client(departments[0]))
    viewer = user_with_perms("orders-viewer", codes=["orders.view"])
    api = auth_client(viewer)

    assert api.get("/api/orders/awaiting-payment/").status_code == 403
    assert api.post(f"/api/orders/{order.pk}/to-debt/").status_code == 403
