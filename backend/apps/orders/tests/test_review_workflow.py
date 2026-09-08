from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from apps.catalog.models import Product
from apps.clients.models import Client
from apps.clients.services import client_history
from apps.eventlog.models import EventLog
from apps.orders.models import Order, OrderItem, Payment
from apps.sales.models import Department
from apps.warehouse.models import StockItem

pytestmark = pytest.mark.django_db


@pytest.fixture
def request_order():
    dept = Department.objects.create(code="chosen", name="Выбранный отдел")
    client = Client.objects.create_with_user(first_name="New", phone="x")
    product = Product.objects.create(name="Flour", color="Red", weight_kg=50)
    StockItem.objects.create(product=product, bags=100)
    order = Order.objects.create(client=client, status="pending", department="")
    item = OrderItem.objects.create(order=order, product=product, quantity=2)
    return order, item, dept


@pytest.mark.parametrize("department", [None, "", "missing", 123])
def test_confirmation_requires_department(
    auth_client, manager, request_order, department
):
    order, item, _ = request_order
    response = auth_client(manager).post(
        f"/api/orders/{order.pk}/confirm/",
        {
            "department": department,
            "prices": {str(item.pk): "100"},
        },
        format="json",
    )
    assert response.status_code == 400
    order.refresh_from_db()
    item.refresh_from_db()
    assert order.status == "pending" and order.department == ""
    assert item.unit_price is None


def test_department_prices_atomic_and_inactive_rejected(
    auth_client, manager, request_order
):
    order, item, dept = request_order
    api = auth_client(manager)
    payload = {"department": dept.code, "prices": {str(item.pk): "0"}}
    assert (
        api.post(f"/api/orders/{order.pk}/confirm/", payload, format="json").status_code
        == 400
    )
    order.refresh_from_db()
    assert order.department == ""
    payload["prices"][str(item.pk)] = "150.25"
    dept.is_active = False
    dept.save()
    assert (
        api.post(f"/api/orders/{order.pk}/confirm/", payload, format="json").status_code
        == 400
    )
    dept.is_active = True
    dept.save()
    response = api.post(f"/api/orders/{order.pk}/confirm/", payload, format="json")
    assert response.status_code == 200
    assert response.data["department"] == dept.code
    assert response.data["total_amount"] == "300.50"
    assert response.data["status"] == "confirmed"
    assert Client.objects.get(pk=order.client_id).department_id is None


@pytest.mark.parametrize("target", ["confirmed", "shipped"])
def test_status_override_cannot_bypass_confirmation(
    auth_client, manager, request_order, target
):
    order, _, _ = request_order
    response = auth_client(manager).post(
        f"/api/orders/{order.pk}/set-status/", {"status": target}
    )
    assert response.status_code == 400
    order.refresh_from_db()
    assert order.status == "pending"


def test_review_is_idempotent_scoped_and_separates_queues(
    auth_client, manager, request_order
):
    order, _, dept = request_order
    api = auth_client(manager)
    assert api.get("/api/orders/workflow-summary/").data["new"] == 1
    assert api.post(f"/api/orders/{order.pk}/review/").status_code == 200
    assert api.post(f"/api/orders/{order.pk}/review/").data["reviewed_by"] == manager.pk
    assert EventLog.objects.filter(order=order, event_type="order_review").count() == 1
    assert api.get("/api/orders/?review_stage=new&page=1").data["count"] == 0
    assert api.get("/api/orders/?review_stage=review&page=1").data["count"] == 1
    summary = api.get("/api/orders/workflow-summary/").data
    assert summary["new"] == 0 and summary["review"] == 1
    manager.employee.sales_department = dept
    manager.employee.save()
    assert api.get("/api/orders/workflow-summary/").data["all"] == 0
    assert api.post(f"/api/orders/{order.pk}/review/").status_code == 404


def test_review_requires_permission(auth_client, user_with_perms, request_order):
    order, _, _ = request_order
    viewer = user_with_perms("viewer", codes=["orders.view"])
    assert (
        auth_client(viewer).post(f"/api/orders/{order.pk}/review/").status_code == 403
    )


@pytest.mark.parametrize("assigned", [False, True])
def test_portal_uses_client_department_without_fallback(
    auth_client, client_user, request_order, assigned
):
    _, item, dept = request_order
    client = Client.objects.create_with_user(
        user=client_user,
        first_name="Portal",
        phone="x",
        department=dept if assigned else None,
    )
    response = auth_client(client_user).post(
        "/api/portal/orders/",
        {
            "items": [{"product": item.product_id, "quantity": 1}],
        },
        format="json",
    )
    assert response.status_code == 201
    assert Order.objects.get(client=client).department == (
        dept.code if assigned else ""
    )


def test_unassigned_staff_request_waits_despite_prices(
    auth_client, manager, request_order
):
    order, item, _ = request_order
    response = auth_client(manager).post(
        "/api/orders/",
        {
            "client": order.client_id,
            "items": [{"product": item.product_id, "quantity": 1}],
            "prices": {str(item.product_id): "150"},
        },
        format="json",
    )
    assert response.status_code == 201
    assert response.data["department"] == ""
    assert response.data["status"] == "pending"
    assert response.data["department_name"] == "Отдел не выбран"


def test_history_cancel_flags_and_balances(auth_client, accountant, request_order):
    order, item, _ = request_order
    order.status = "shipped"
    order.save()
    item.unit_price = 100
    item.save()
    payment = Payment.objects.create(order=order, amount=50, status="confirmed")
    refunded = Payment.objects.create(
        order=order, amount=20, status="confirmed", refunded_amount=5
    )
    rows = {row["id"]: row for row in client_history(order.client)["payments"]}
    assert rows[payment.pk]["can_reopen"] is True
    assert rows[refunded.pk]["can_reopen"] is False
    api = auth_client(accountant)
    response = api.post(f"/api/orders/{order.pk}/payments/{payment.pk}/reopen/")
    assert response.status_code == 200
    assert Decimal(response.data["paid_total"]) == 15
    rows = {row["id"]: row for row in client_history(order.client)["payments"]}
    assert rows[payment.pk]["can_reject"] is True
    assert rows[payment.pk]["can_reopen"] is False
    assert (
        api.post(f"/api/orders/{order.pk}/payments/{payment.pk}/reject/").status_code
        == 200
    )


def test_workflow_and_history_query_count_constant(auth_client, manager, request_order):
    order, _, _ = request_order
    api = auth_client(manager)

    def queries():
        with CaptureQueriesContext(connection) as captured:
            assert api.get("/api/orders/workflow-summary/").status_code == 200
            client_history(order.client)
        return len(captured)

    Payment.objects.create(order=order, amount=10, status="confirmed")
    queries()
    small = queries()
    Payment.objects.bulk_create(
        [Payment(order=order, amount=10, status="confirmed") for _ in range(20)]
    )
    assert queries() == small


def test_unassigned_orders_are_counted_and_filterable(
    auth_client, manager, request_order
):
    order, _, _ = request_order
    api = auth_client(manager)
    rows = api.get("/api/orders/department-summary/").data
    assert next(row for row in rows if row["code"] == "__unassigned")["orders"] == 1
    assert (
        api.get("/api/orders/?department=__unassigned&page=1").data["results"][0]["id"]
        == order.pk
    )
    order.status = "confirmed"
    order.department = "chosen"
    order.save()
    assert api.patch(f"/api/orders/{order.pk}/", {"department": ""}).status_code == 400
