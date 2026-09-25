"""Общие очереди для сотрудника, закреплённого за отделом.

Оплаты ручной очереди кассы видны всем отделам. Заявки всех отделов
(«Заказы» → «Заявки») — только с правом orders.confirm_all. Всё остальное
(обычный список заказов, карточки подтверждённых заказов, журнал, возврат
подтверждения, транзакции) остаётся в его отделе.
"""

from decimal import Decimal

import pytest
from django.utils import timezone

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import ApiPayInvoice, Order, OrderItem, Payment

pytestmark = pytest.mark.django_db


@pytest.fixture
def mill_cashier(user_with_perms, departments):
    return user_with_perms(
        "mill-cashier",
        codes=["orders.view", "orders.confirm", "orders.confirm_all", "payments.view", "payments.confirm"],
        department=departments[0],
    )


@pytest.fixture
def city_client(departments):
    return Client.objects.create_with_user(first_name="Чужой", phone="+7 (705) 565-65-65", department=departments[1])


def _order(client, *, status, amount="100.00"):
    order = Order.objects.create(client=client, status=status, department=client.department.code)
    product = Product.objects.create(name=f"Мука {order.pk}", color="White", weight_kg="50")
    OrderItem.objects.create(order=order, product=product, quantity=1, unit_price=amount)
    return order


def _payment(order, *, status, amount="10.00"):
    return Payment.objects.create(order=order, amount=Decimal(amount), method="cash", status=status)


def _ids(response):
    assert response.status_code == 200, response.data
    rows = response.data["results"] if isinstance(response.data, dict) else response.data
    return {row["id"] for row in rows}


def test_foreign_requests_are_listed_only_in_the_queue(auth_client, mill_cashier, city_client):
    request = _order(city_client, status="pending")
    confirmed = _order(city_client, status="confirmed")
    api = auth_client(mill_cashier)

    assert _ids(api.get("/api/orders/?status_group=pending&confirm_queue=1")) == {request.pk}
    # Касса с выбранным отделом сужает очередь, как и раньше.
    assert _ids(api.get("/api/orders/?status_group=pending&confirm_queue=1&department=city")) == {request.pk}
    assert _ids(api.get("/api/orders/?status_group=pending&confirm_queue=1&department=mill")) == set()
    # Без флага очереди — обычный список своего отдела.
    assert _ids(api.get("/api/orders/?status_group=pending")) == set()
    assert _ids(api.get("/api/orders/")) == set()
    assert _ids(api.get("/api/orders/?confirm_queue=1")) == {request.pk}
    assert api.get(f"/api/orders/{request.pk}/").status_code == 200
    assert api.get(f"/api/orders/{confirmed.pk}/").status_code == 404


def test_queue_flag_needs_confirm_permission(auth_client, user_with_perms, departments, city_client):
    request = _order(city_client, status="pending")
    viewer = user_with_perms("mill-viewer", codes=["orders.view"], department=departments[0])
    api = auth_client(viewer)

    assert _ids(api.get("/api/orders/?status_group=pending&confirm_queue=1")) == set()
    assert api.get(f"/api/orders/{request.pk}/").status_code == 404


def test_foreign_requests_need_all_departments_permission(auth_client, user_with_perms, departments, city_client):
    foreign = _order(city_client, status="pending")
    unassigned = Order.objects.create(
        client=Client.objects.create_with_user(first_name="Новый", phone="+7 (705) 565-65-66"),
        status="pending",
    )
    confirmer = user_with_perms(
        "mill-confirmer",
        codes=["orders.view", "orders.confirm", "payments.confirm"],
        department=departments[0],
    )
    api = auth_client(confirmer)

    # Без права — только заявки своего отдела и клиентов без отдела.
    assert _ids(api.get("/api/orders/?status_group=pending&confirm_queue=1")) == {unassigned.pk}
    assert api.get(f"/api/orders/{foreign.pk}/").status_code == 404
    assert api.post(f"/api/orders/{foreign.pk}/reject/", {"reason": "нет"}, format="json").status_code == 404
    foreign.refresh_from_db()
    assert foreign.status == "pending"


def test_cashier_confirms_foreign_request_into_client_department(auth_client, mill_cashier, city_client, departments):
    order = _order(city_client, status="pending")
    item = order.items.get()
    api = auth_client(mill_cashier)

    wrong = api.post(
        f"/api/orders/{order.pk}/confirm/",
        {"department": "mill", "prices": {str(item.pk): "120.00"}},
        format="json",
    )
    assert wrong.status_code == 400
    order.refresh_from_db()
    assert order.status == "pending"

    response = api.post(
        f"/api/orders/{order.pk}/confirm/",
        {"department": "city", "prices": {str(item.pk): "120.00"}},
        format="json",
    )

    assert response.status_code == 200, response.data
    order.refresh_from_db()
    city_client.refresh_from_db()
    assert order.status == "confirmed"
    assert order.department == "city"
    assert city_client.department == departments[1]
    # Подтверждённый заказ ушёл из очереди — дальше он виден только своему отделу.
    assert api.get(f"/api/orders/{order.pk}/").status_code == 404
    assert _ids(api.get("/api/orders/?status_group=pending&confirm_queue=1")) == set()


def test_cashier_rejects_foreign_request(auth_client, mill_cashier, city_client):
    order = _order(city_client, status="pending")

    response = auth_client(mill_cashier).post(
        f"/api/orders/{order.pk}/reject/", {"reason": "Нет товара"}, format="json"
    )

    assert response.status_code == 200, response.data
    order.refresh_from_db()
    assert order.status == "rejected"
    assert order.department == "city"


def test_foreign_payments_are_in_the_queue_and_summary(auth_client, mill_cashier, city_client):
    order = _order(city_client, status="shipped")
    requested = _payment(order, status="requested")
    received = _payment(order, status="received", amount="20.00")
    _payment(order, status="confirmed", amount="30.00")
    provider = _payment(order, status="received", amount="5.00")
    ApiPayInvoice.objects.create(payment=provider, invoice_id=770001, idempotency_key="queue-provider", status="pending")
    api = auth_client(mill_cashier)

    assert _ids(api.get("/api/orders/payments-queue/")) == {requested.pk, received.pk}
    assert _ids(api.get("/api/orders/payments-queue/?department=city")) == {requested.pk, received.pk}
    assert _ids(api.get("/api/orders/payments-queue/?department=mill")) == set()
    summary = api.get("/api/orders/payments-queue/?summary=1")
    assert summary.status_code == 200
    assert summary.data == [{"currency": "KZT", "method": "cash", "amount": "30.00", "count": 2}]


def test_cashier_receives_confirms_and_rejects_foreign_payments(auth_client, mill_cashier, city_client):
    order = _order(city_client, status="shipped")
    requested = _payment(order, status="requested")
    received = _payment(order, status="received", amount="20.00")
    doubtful = _payment(order, status="received", amount="30.00")
    api = auth_client(mill_cashier)

    receive = api.post(f"/api/orders/{order.pk}/payments/{requested.pk}/receive/")
    confirm = api.post(f"/api/orders/{order.pk}/payments/{received.pk}/confirm/")
    reject = api.post(f"/api/orders/{order.pk}/payments/{doubtful.pk}/reject/", {"reason": "Не пришли"}, format="json")

    assert [receive.status_code, confirm.status_code, reject.status_code] == [200, 200, 200]
    for payment in (requested, received, doubtful):
        payment.refresh_from_db()
    assert requested.status == "confirmed"
    assert received.status == "confirmed"
    assert doubtful.status == "rejected"
    assert _ids(api.get("/api/orders/payments-queue/")) == set()


def test_everything_outside_the_queue_stays_in_own_department(auth_client, mill_cashier, city_client):
    shipped = _order(city_client, status="shipped")
    confirmed = _order(city_client, status="confirmed")
    queued = _payment(shipped, status="received")
    settled = _payment(shipped, status="confirmed", amount="20.00")
    settled.confirmed_at = timezone.now()
    settled.save(update_fields=["confirmed_at"])
    rejected = _payment(shipped, status="rejected", amount="30.00")
    provider = _payment(shipped, status="received", amount="5.00")
    ApiPayInvoice.objects.create(payment=provider, invoice_id=770002, idempotency_key="scope-provider", status="paid")
    api = auth_client(mill_cashier)

    # Кассир подтверждает оплату из очереди — остальное в чужом отделе ему всё равно не видно.
    assert api.post(f"/api/orders/{shipped.pk}/payments/{queued.pk}/confirm/").status_code == 200

    assert _ids(api.get("/api/orders/")) == set()
    assert _ids(api.get("/api/orders/?status_group=shipped")) == set()
    assert api.get(f"/api/orders/{shipped.pk}/").status_code == 404
    assert api.get(f"/api/orders/{confirmed.pk}/").status_code == 404
    assert api.get("/api/payment-transactions/").data["count"] == 0
    responses = [
        api.post(f"/api/orders/{shipped.pk}/payments/{settled.pk}/reopen/"),
        # Онлайн-оплата не стоит в ручной очереди кассы.
        api.post(f"/api/orders/{shipped.pk}/payments/{provider.pk}/confirm/"),
        api.post(f"/api/orders/{shipped.pk}/payments/{settled.pk}/reject/", {"reason": "нет"}, format="json"),
        api.post(f"/api/payment-transactions/{rejected.pk}/restore/"),
        # Подтверждённый заказ уже не заявка — разбирать его в очереди нечего.
        api.post(f"/api/orders/{confirmed.pk}/reject/", {"reason": "нет"}, format="json"),
    ]
    assert [response.status_code for response in responses] == [404] * 5
    for payment in (settled, rejected, provider):
        payment.refresh_from_db()
    assert (settled.status, rejected.status, provider.status) == ("confirmed", "rejected", "received")
