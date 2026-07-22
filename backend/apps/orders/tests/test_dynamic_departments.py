import pytest
from rest_framework.test import APIClient

from apps.catalog.models import Product
from apps.clients.models import Client, Department
from apps.orders.models import Order, OrderItem
from apps.employees.models import Employee
from apps.warehouse.models import StockItem

pytestmark = pytest.mark.django_db


def _api(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


def _product():
    product = Product.objects.create(
        name="Мука", color="Red", weight_kg="50", price="100.00")
    StockItem.objects.create(product=product, bags=100)
    return product


def test_order_selects_department_independently_from_client(manager):
    department = Department.objects.create(
        code="department-regions", name="Регионы", color="#238C6E", is_default=True)
    client = Client.objects.create(first_name="Алия", last_name="С", phone="1")
    product = _product()

    response = _api(manager).post("/api/orders/", {
        "client": client.id,
        "department": department.code,
        "items": [{"product": product.id, "quantity": 2}],
        "prices": {str(product.id): "120.00"},
    }, format="json")

    assert response.status_code == 201
    assert response.data["department"] == department.code
    assert response.data["department_name"] == "Регионы"
    assert response.data["department_color"] == "#238C6E"


def test_inactive_department_cannot_be_used_for_new_order(manager):
    department = Department.objects.create(
        code="old", name="Старый", is_active=False, is_default=False)
    client = Client.objects.create(first_name="Алия", last_name="С", phone="1")
    product = _product()

    response = _api(manager).post("/api/orders/", {
        "client": client.id,
        "department": department.code,
        "items": [{"product": product.id, "quantity": 1}],
    }, format="json")

    assert response.status_code == 400


def test_sales_employee_order_is_forced_to_assigned_department(make_user):
    assigned = Department.objects.create(
        code="assigned-sales", name="Назначенный", color="#315FD5", is_default=True)
    other = Department.objects.create(
        code="other-sales", name="Другой", color="#D68B2C")
    user = make_user(username="assigned-manager")
    Employee.objects.create(
        user=user, first_name="Менеджер", last_name="Отдела",
        sales_department=assigned,
    )
    client = Client.objects.create(first_name="Алия", last_name="С", phone="1")
    product = _product()

    response = _api(user).post("/api/orders/", {
        "client": client.id,
        "department": other.code,
        "items": [{"product": product.id, "quantity": 2}],
        "prices": {str(product.id): "120.00"},
    }, format="json")

    assert response.status_code == 201
    assert response.data["department"] == assigned.code
    assert Order.objects.get(pk=response.data["id"]).department == assigned.code


def test_department_summary_groups_orders(manager):
    first = Department.objects.create(
        code="wholesale", name="Оптовый", color="#315FD5", is_default=True)
    second = Department.objects.create(
        code="retail", name="Розница", color="#D68B2C")
    client = Client.objects.create(first_name="Алия", last_name="С", phone="1")
    product = _product()
    first_order = Order.objects.create(client=client, department=first.code, status="confirmed")
    second_order = Order.objects.create(client=client, department=second.code, status="shipped")
    OrderItem.objects.create(order=first_order, product=product, quantity=2, unit_price="100.00")
    OrderItem.objects.create(order=second_order, product=product, quantity=3, unit_price="100.00")

    response = _api(manager).get("/api/orders/department-summary/")

    assert response.status_code == 200
    rows = {row["code"]: row for row in response.data}
    assert rows["wholesale"]["orders"] == 1
    assert rows["wholesale"]["active"] == 1
    assert rows["retail"]["shipped"] == 1
    assert rows["retail"]["revenue"] == "300.00"
