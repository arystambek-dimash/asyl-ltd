"""Заявки клиентов без отдела: касса любого отдела видит их и забирает клиента себе."""

import pytest

from apps.clients.models import Client
from apps.orders.models import Order
from apps.sales.models import Department

pytestmark = pytest.mark.django_db


@pytest.fixture
def departments():
    mill = Department.objects.create(code="mill", name="Мельница")
    city = Department.objects.create(code="city", name="Нью-Сити")
    return mill, city


@pytest.fixture
def mill_cashier(user_with_perms, departments):
    user = user_with_perms("mill-cashier", codes=["orders.view", "orders.confirm"])
    user.employee.sales_department = departments[0]
    user.employee.save(update_fields=["sales_department"])
    return user


def _client(name, department=None):
    return Client.objects.create_with_user(first_name=name, phone="+7 (705) 565-65-65", department=department)


def _pending_ids(api, query=""):
    response = api.get(f"/api/orders/?status_group=pending{query}")
    assert response.status_code == 200
    return {row["id"] for row in response.data}


def test_department_queue_shows_unassigned_requests_only(auth_client, mill_cashier, departments):
    mill, city = departments
    waiting = _client("Новый")
    request = Order.objects.create(client=waiting, status="pending", department="")
    already_confirmed = Order.objects.create(client=waiting, status="confirmed", department="city")
    foreign = Order.objects.create(client=_client("Чужой", city), status="pending", department="city")
    api = auth_client(mill_cashier)

    assert _pending_ids(api) == {request.pk}
    assert _pending_ids(api, "&department=mill&confirm_queue=1") == {request.pk}
    # Прежнее имя флага очереди у касс, открытых до обновления страницы.
    assert _pending_ids(api, "&department=mill&with_unassigned=1") == {request.pk}
    assert _pending_ids(api, "&department=mill") == set()
    assert api.get(f"/api/orders/{already_confirmed.pk}/").status_code == 404
    # Заявки других отделов — только с правом orders.confirm_all (test_shared_confirm_queue.py).
    assert api.get(f"/api/orders/{foreign.pk}/").status_code == 404


def test_order_without_department_counts_in_client_department(auth_client, mill_cashier, departments):
    order = Order.objects.create(client=_client("Свой", departments[0]), status="pending", department="")

    assert _pending_ids(auth_client(mill_cashier), "&department=mill") == {order.pk}


def test_confirming_unassigned_request_assigns_client(auth_client, mill_cashier, departments):
    waiting = _client("Новый")
    order = Order.objects.create(client=waiting, status="pending", department="")

    response = auth_client(mill_cashier).post(f"/api/orders/{order.pk}/confirm/", {"department": "mill"}, format="json")

    assert response.status_code == 200, response.data
    order.refresh_from_db()
    waiting.refresh_from_db()
    assert order.status == "confirmed"
    assert order.department == "mill"
    assert waiting.department == departments[0]


def test_cashier_cannot_confirm_into_another_department(auth_client, mill_cashier, departments):
    waiting = _client("Новый")
    order = Order.objects.create(client=waiting, status="pending", department="")

    response = auth_client(mill_cashier).post(f"/api/orders/{order.pk}/confirm/", {"department": "city"}, format="json")

    assert response.status_code == 403
    order.refresh_from_db()
    waiting.refresh_from_db()
    assert order.status == "pending"
    assert waiting.department is None


def test_cashier_can_reject_unassigned_request(auth_client, mill_cashier):
    order = Order.objects.create(client=_client("Новый"), status="pending", department="")

    response = auth_client(mill_cashier).post(
        f"/api/orders/{order.pk}/reject/", {"reason": "Нет товара"}, format="json"
    )

    assert response.status_code == 200
    order.refresh_from_db()
    assert order.status == "rejected"
