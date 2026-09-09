from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.clients.reports.statements.data import build_statement_data
from apps.eventlog.models import EventLog
from apps.orders.models import Order, OrderItem, Payment, PaymentRefund
from apps.orders.reports import summary_report
from apps.orders.services import add_payment, accountant_confirm_payment
from apps.sales.models import Department
from apps.shipments.models import Shipment
from apps.warehouse.models import StockItem

pytestmark = pytest.mark.django_db


@pytest.fixture
def sale():
    department = Department.objects.create(code="sales-audit", name="Отдел клиента")
    client = Client.objects.create_with_user(
        first_name="Audit", phone="audit", department=department
    )
    client.user.is_active = True
    client.user.save(update_fields=["is_active"])
    product = Product.objects.create(name="Audit flour", color="Red", weight_kg=50)
    StockItem.objects.create(product=product, bags=100)
    return client, department, product


def test_client_request_confirmation_payment_and_statement_keep_department(
    sale, auth_client, manager, accountant, boss
):
    client, department, product = sale
    response = auth_client(client.user).post(
        "/api/portal/orders/",
        {
            "items": [{"product": product.pk, "quantity": 2}],
            "department": "spoofed",  # the portal cannot choose another department
        },
        format="json",
    )
    assert response.status_code == 201
    order = Order.objects.get(pk=response.data["id"])
    assert response.data["department_name"] == department.name
    assert order.department == department.code
    payload = {"department": "main", "prices": {str(order.items.get().pk): "100"}}
    staff = auth_client(manager)
    assert (
        staff.post(
            f"/api/orders/{order.pk}/confirm/", payload, format="json"
        ).status_code
        == 400
    )
    order.refresh_from_db()
    assert order.status == "pending"
    assert order.items.get().unit_price is None
    payload["department"] = department.code
    assert (
        staff.post(
            f"/api/orders/{order.pk}/confirm/", payload, format="json"
        ).status_code
        == 200
    )
    # Fixture for physical shipment: this test never calls hardware or payment providers.
    Order.objects.filter(pk=order.pk).update(status="shipped")
    order.refresh_from_db()
    Shipment.objects.create(order=order, shipped_at=timezone.now())
    payment = add_payment(order, "150", manager, method="cash")
    accountant_confirm_payment(payment, accountant)
    payment.refresh_from_db()
    assert payment.status == "confirmed"
    event = EventLog.objects.filter(
        order=order, payload__payment_stage="confirmed"
    ).latest("pk")
    assert event.payload["department"] == department.code
    assert event.user_id == accountant.pk
    other = Department.objects.create(code="later", name="Новый отдел")
    client.department = other
    client.save(update_fields=["department"])
    report = auth_client(boss).get("/api/reports/summary/").data
    row = next(row for row in report["departments"] if row["code"] == department.code)
    assert row["sales_by_currency"] == {"KZT": "200.00"}
    assert row["received_by_currency"] == {"KZT": "150.00"}
    statement = build_statement_data(client=client)
    totals = statement.department_totals[(department.code, "KZT")]
    assert totals["sales"] == Decimal("200")
    assert totals["payments"] == Decimal("150")
    assert (other.code, "KZT") not in statement.department_totals


def test_rejection_is_reasoned_scoped_and_visible_to_client(
    sale, auth_client, manager, user_with_perms
):
    client, dept, _ = sale
    order = Order.objects.create(client=client, department=dept.code, status="pending")
    url = f"/api/orders/{order.pk}/reject/"
    viewer = user_with_perms("audit-viewer", codes=["orders.view"])
    assert auth_client(viewer).post(url, {"reason": "Нет товара"}).status_code == 403
    api = auth_client(manager)
    for value in ("", "  ", "x" * 501, [], {"reason": "bad"}):
        assert api.post(url, {"reason": value}, format="json").status_code == 400
    assert not EventLog.objects.filter(order=order).exists()
    response = api.post(url, {"reason": "  Нужный сорт закончился  "})
    assert response.status_code == 200
    assert response.data["rejection_reason"] == "Нужный сорт закончился"
    event = EventLog.objects.get(order=order, payload__action="order_rejected")
    assert event.payload["reason"] == response.data["rejection_reason"]
    assert event.payload["department"] == dept.code
    assert event.user_id == manager.pk
    assert api.post(url, {"reason": "Повтор"}).status_code == 400
    assert (
        EventLog.objects.filter(order=order, payload__action="order_rejected").count()
        == 1
    )
    portal = auth_client(client.user).get(f"/api/portal/orders/{order.pk}/")
    assert portal.data["status"] == "rejected"
    assert portal.data["rejection_reason"] == "Нужный сорт закончился"
    assert auth_client(client.user).get("/api/portal/orders/").status_code == 200


def test_legacy_payment_prevents_department_reassignment(sale, auth_client, manager):
    client, department, _ = sale
    client.department = None
    client.save(update_fields=["department"])
    order = Order.objects.create(
        client=client, status="confirmed", department=department.code
    )
    Payment.objects.create(order=order, amount=1, method="cash", status="rejected")
    response = auth_client(manager).patch(
        f"/api/orders/{order.pk}/", {"department": "main"}
    )
    assert response.status_code == 400
    order.refresh_from_db()
    assert order.department == department.code


def test_repeat_uses_current_client_department_without_rewriting_source(
    sale, auth_client, user_with_perms
):
    client, department, product = sale
    source = Order.objects.create(client=client, status="shipped", department="main")
    OrderItem.objects.create(order=source, product=product, quantity=1, unit_price=100)
    user = user_with_perms("repeat-department", codes=["orders.view", "orders.create"])
    response = auth_client(user).post(f"/api/orders/{source.pk}/repeat/")
    assert response.status_code == 201
    assert response.data["department"] == department.code
    source.refresh_from_db()
    assert source.department == "main"
    assert response.data["status"] == "pending"


def test_department_cannot_be_redirected_by_create_or_patch(sale, auth_client, manager):
    client, department, product = sale
    api = auth_client(manager)
    payload = {
        "client": client.pk,
        "department": "main",
        "items": [{"product": product.pk, "quantity": 1}],
    }
    assert api.post("/api/orders/", payload, format="json").status_code == 400
    order = Order.objects.create(
        client=client, status="confirmed", department=department.code
    )
    assert (
        api.patch(f"/api/orders/{order.pk}/", {"department": "main"}).status_code == 400
    )
    order.refresh_from_db()
    assert order.department == department.code


def test_global_admin_creates_order_in_client_department(sale, auth_client, boss):
    client, department, product = sale
    boss.is_superuser = True
    boss.save(update_fields=["is_superuser"])
    boss.employee.sales_department = Department.objects.create(
        code="admin-office", name="Отдел администратора"
    )
    boss.employee.save(update_fields=["sales_department"])
    response = auth_client(boss).post(
        "/api/orders/",
        {"client": client.pk, "items": [{"product": product.pk, "quantity": 1}]},
        format="json",
    )
    assert response.status_code == 201
    assert response.data["department"] == department.code


def test_department_staff_cannot_reject_foreign_requests(
    sale, auth_client, user_with_perms
):
    client, dept, _ = sale
    user = user_with_perms(
        "foreign-department", codes=["orders.view", "orders.confirm", "reports.view"]
    )
    user.employee.sales_department = Department.objects.create(
        code="foreign", name="Другой отдел"
    )
    user.employee.save(update_fields=["sales_department"])
    order = Order.objects.create(client=client, department=dept.code, status="pending")
    api = auth_client(user)
    assert (
        api.post(
            f"/api/orders/{order.pk}/reject/", {"reason": "Нет товара"}
        ).status_code
        == 404
    )
    assert api.get("/api/reports/summary/").data["departments"] == []
    order.refresh_from_db()
    assert order.status == "pending"


def test_report_departments_reconcile_across_periods_currencies_and_refunds(sale):
    client, dept, product = sale
    now = timezone.now()
    old = now - timedelta(days=5)
    a = Order.objects.create(client=client, status="shipped", department=dept.code)
    b = Order.objects.create(
        client=client, status="shipped", department="", currency="USD"
    )
    for order in (a, b):
        OrderItem.objects.create(
            order=order, product=product, quantity=2, unit_price=100
        )
        Shipment.objects.create(order=order, shipped_at=now if order == b else old)
    pay = Payment.objects.create(
        order=a,
        status="confirmed",
        method="cash",
        amount=100,
        confirmed_at=old,
        refunded_amount=25,
    )
    Payment.objects.create(
        order=b, status="confirmed", method="invoice", amount=50, confirmed_at=now
    )
    Payment.objects.create(order=b, status="received", method="cash", amount=10)
    PaymentRefund.objects.create(
        payment=pay,
        amount=25,
        method="cash",
        status="completed",
        completed_at=now,
        reason="Test",
    )
    today = timezone.localdate()
    report = summary_report(Order.objects.all(), today, today)
    rows = {row["code"]: row for row in report["departments"]}
    assert rows[dept.code]["sales_by_currency"] == {}
    assert rows[dept.code]["net_by_currency"] == {"KZT": "-25.00"}
    assert rows[""]["name"] == "Нет отдела"
    assert rows[""]["sales_by_currency"] == {"USD": "200.00"}
    assert rows[""]["net_by_currency"] == {"USD": "50.00"}
    for unit, total in report["income"]["by_currency"].items():
        assert sum(
            Decimal(row["net_by_currency"].get(unit, "0")) for row in rows.values()
        ) == Decimal(total)
    income = summary_report(Order.objects.all(), today, today, income_only=True)
    assert all(row["sales_by_currency"] is None for row in income["departments"])
    assert income["income"] == report["income"]
    # Adding departments/orders must not add a query per order or department.
    with CaptureQueriesContext(connection) as small:
        summary_report(Order.objects.all())
    for index in range(8):
        order = Order.objects.create(
            client=client, status="shipped", department=f"legacy-{index}"
        )
        OrderItem.objects.create(
            order=order, product=product, quantity=1, unit_price=10
        )
        Payment.objects.create(
            order=order, amount=5, method="cash", status="confirmed", confirmed_at=now
        )
    with CaptureQueriesContext(connection) as large:
        expanded = summary_report(Order.objects.all())
    assert len(small) == len(large)
    assert len(expanded["departments"]) == 10
