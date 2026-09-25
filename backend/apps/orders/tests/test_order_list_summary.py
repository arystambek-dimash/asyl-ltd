"""Итоги «Общей» аналитики заказов считаются по всей выборке списка.

Раньше фронт складывал загруженную страницу (50 заказов): при 70 заказах
«Всего заказов» показывало 50, а «Сумма» — стоимость только первой страницы.
"""
import pytest

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem
from apps.sales.models import Department

pytestmark = pytest.mark.django_db


def _order(client, price, *, status="confirmed", currency="KZT", department=""):
    product, _ = Product.objects.get_or_create(name="P", color="Red", weight_kg="50")
    order = Order.objects.create(client=client, status=status, currency=currency, department=department)
    OrderItem.objects.create(order=order, product=product, quantity=1, unit_price=price)
    return order


def _summary(api, query=""):
    response = api.get(f"/api/orders/list-summary/{query}")
    assert response.status_code == 200, response.content
    return response.json()


def test_totals_span_the_whole_selection_not_one_page(boss, auth_client):
    client = Client.objects.create_with_user(first_name="A", last_name="B", phone="1")
    for _ in range(55):
        _order(client, "10")
    _order(client, "100", status="pending")
    _order(client, "200", status="shipped")
    _order(client, "5", currency="USD")
    _order(client, "999", status="cancelled")
    _order(client, "999", status="rejected")

    summary = _summary(auth_client(boss))

    # Отменённые и отклонённые не считаются; «в процессе» — всё, кроме отгруженных.
    assert summary["orders"] == 58
    assert summary["active"] == 57
    assert summary["total_currency"] == "KZT"
    assert summary["total_by_currency"] == {"KZT": "850.00", "USD": "5.00"}
    # Доли статусов — только в основной валюте.
    assert summary["by_status_group"] == {"confirmed": "550.00", "pending": "100.00", "shipped": "200.00"}


def test_uses_the_same_filters_and_search_as_the_list(boss, auth_client):
    department = Department.objects.create(code="sum_dep", name="Отдел", color="#000")
    wanted = Client.objects.create_with_user(first_name="Искомый", last_name="Клиент", phone="2")
    other = Client.objects.create_with_user(first_name="Другой", last_name="Клиент", phone="3")
    _order(wanted, "300", department=department.code)
    _order(wanted, "400", status="shipped", department=department.code)
    _order(wanted, "50", department="")
    _order(other, "700", department=department.code)
    api = auth_client(boss)

    query = f"?search=Искомый&department={department.code}&status_group=confirmed"
    summary = _summary(api, query)
    listed = api.get(f"/api/orders/{query}").json()

    assert summary["orders"] == len(listed) == 1
    assert summary["total_by_currency"] == {"KZT": "300.00"}


def test_employee_of_a_department_gets_totals_of_their_department(user_with_perms, auth_client):
    own = Department.objects.create(code="own_dep", name="Свой", color="#000")
    foreign = Department.objects.create(code="foreign_dep", name="Чужой", color="#000")
    own_client = Client.objects.create_with_user(first_name="С", last_name="В", phone="4", department=own)
    foreign_client = Client.objects.create_with_user(first_name="Ч", last_name="У", phone="5", department=foreign)
    _order(own_client, "100", department=own.code)
    _order(foreign_client, "900", department=foreign.code)
    user = user_with_perms("summary-scope", codes=["orders.view"], department=own)

    summary = _summary(auth_client(user))

    assert summary["orders"] == 1
    assert summary["total_by_currency"] == {"KZT": "100.00"}
