from decimal import Decimal

import pytest
from django.utils import timezone

from apps.catalog.models import Product
from apps.clients.models import Client, Store
from apps.eventlog.models import EventLog
from apps.orders.models import Order, OrderItem, Payment
from apps.sales.models import Department
from apps.warehouse.models import StockItem


pytestmark = pytest.mark.django_db


@pytest.fixture
def ownership_scope(user_with_perms):
    first_department = Department.objects.create(
        code="ownership-a",
        name="Владельцы A",
    )
    second_department = Department.objects.create(
        code="ownership-b",
        name="Владельцы B",
    )
    first_client = Client.objects.create_with_user(
        first_name="Клиент",
        last_name="A",
        phone="1",
        department=first_department,
    )
    second_client = Client.objects.create_with_user(
        first_name="Клиент",
        last_name="B",
        phone="2",
        department=second_department,
    )
    user = user_with_perms(
        "department-owner-a",
        codes=[
            "orders.view",
            "orders.create",
            "orders.edit",
            "payments.view",
            "payments.create",
            "payments.confirm",
            "reports.view",
        ],
    )
    user.employee.sales_department = first_department
    user.employee.save(update_fields=["sales_department"])
    return {
        "user": user,
        "first_department": first_department,
        "second_department": second_department,
        "first_client": first_client,
        "second_client": second_client,
    }


def _order(client, *, department, status="draft", amount="100.00"):
    product = Product.objects.create(
        name=f"Товар {client.pk} {department}",
        color="White",
        weight_kg="50",
    )
    order = Order.objects.create(
        client=client,
        department=department,
        status=status,
    )
    OrderItem.objects.create(
        order=order,
        product=product,
        quantity=1,
        unit_price=amount,
    )
    return order


def test_order_scope_covers_list_detail_and_trash(
    auth_client,
    ownership_scope,
):
    scope = ownership_scope
    first = _order(
        scope["first_client"],
        department=scope["second_department"].code,
    )
    second = _order(
        scope["second_client"],
        department=scope["first_department"].code,
    )
    api = auth_client(scope["user"])

    listed = api.get("/api/orders/")

    assert listed.status_code == 200
    assert [row["id"] for row in listed.data] == [first.pk]
    assert api.get(f"/api/orders/{second.pk}/").status_code == 404
    assert api.delete(f"/api/orders/{second.pk}/").status_code == 404

    deleted_at = timezone.now()
    Order.objects.filter(pk__in=[first.pk, second.pk]).update(
        deleted_at=deleted_at,
        deleted_by=scope["user"],
    )

    trash = api.get("/api/orders/trash/")
    preview = api.get("/api/orders/trash-preview/")

    assert [row["id"] for row in trash.data] == [first.pk]
    assert preview.data["count"] == 1
    assert [row["id"] for row in preview.data["results"]] == [first.pk]
    assert api.post(f"/api/orders/{second.pk}/restore/").status_code == 400


def test_unassigned_and_superuser_keep_global_order_access(
    auth_client,
    make_user,
    ownership_scope,
    user_with_perms,
):
    scope = ownership_scope
    first = _order(
        scope["first_client"],
        department=scope["first_department"].code,
    )
    second = _order(
        scope["second_client"],
        department=scope["second_department"].code,
    )
    unassigned = user_with_perms("global-orders", codes=["orders.view"])
    superuser = make_user(username="global-superuser")
    superuser.is_staff = True
    superuser.is_superuser = True
    superuser.save(update_fields=["is_staff", "is_superuser"])

    for user in (unassigned, superuser):
        response = auth_client(user).get("/api/orders/")
        assert response.status_code == 200
        assert {row["id"] for row in response.data} == {first.pk, second.pk}


def test_order_form_options_and_write_references_are_ownership_scoped(
    auth_client,
    ownership_scope,
):
    scope = ownership_scope
    first_store = Store.objects.create(
        client=scope["first_client"],
        name="Магазин A",
    )
    second_store = Store.objects.create(
        client=scope["second_client"],
        name="Магазин B",
    )
    foreign_template = _order(
        scope["second_client"],
        department=scope["second_department"].code,
    )
    product = Product.objects.create(
        name="Новый товар",
        color="Blue",
        weight_kg="25",
    )
    StockItem.objects.create(product=product, bags=100)
    api = auth_client(scope["user"])

    options = api.get("/api/orders/form-options/")

    assert options.status_code == 200
    assert {row["id"] for row in options.data["clients"]} == {
        scope["first_client"].pk
    }
    assert {row["id"] for row in options.data["stores"]} == {first_store.pk}

    base = {
        "client": scope["first_client"].pk,
        "items": [{"product": product.pk, "quantity": 1}],
    }
    foreign_client = api.post(
        "/api/orders/",
        {**base, "client": scope["second_client"].pk},
        format="json",
    )
    foreign_store = api.post(
        "/api/orders/",
        {**base, "store": second_store.pk},
        format="json",
    )
    foreign_source = api.post(
        "/api/orders/",
        {**base, "template_order": foreign_template.pk},
        format="json",
    )

    assert foreign_client.status_code == 400
    assert "client" in foreign_client.data["detail"]
    assert foreign_store.status_code == 400
    assert "store" in foreign_store.data["detail"]
    assert foreign_source.status_code == 400
    assert "template_order" in foreign_source.data["detail"]
    assert not Order.objects.filter(
        client=scope["first_client"],
        repeated_from=foreign_template,
    ).exists()


def test_reports_transactions_queue_log_and_department_summary_are_scoped(
    auth_client,
    ownership_scope,
):
    scope = ownership_scope
    order_department = scope["second_department"].code
    first_order = _order(
        scope["first_client"],
        department=order_department,
        status="shipped",
        amount="100.00",
    )
    second_order = _order(
        scope["second_client"],
        department=order_department,
        status="shipped",
        amount="900.00",
    )
    first_confirmed = Payment.objects.create(
        order=first_order,
        amount="40.00",
        method="cash",
        status="confirmed",
        confirmed_at=timezone.now(),
    )
    Payment.objects.create(
        order=second_order,
        amount="900.00",
        method="cash",
        status="confirmed",
        confirmed_at=timezone.now(),
    )
    first_pending = Payment.objects.create(
        order=first_order,
        amount="10.00",
        method="cash",
        status="received",
    )
    second_pending = Payment.objects.create(
        order=second_order,
        amount="20.00",
        method="cash",
        status="received",
    )
    EventLog.objects.create(
        event_type="payment",
        message="Оплата A",
        order=first_order,
        payload={"payment_id": first_confirmed.pk},
    )
    EventLog.objects.create(
        event_type="payment",
        message="Оплата B",
        order=second_order,
        payload={"payment_id": second_pending.pk},
    )
    api = auth_client(scope["user"])

    report = api.get("/api/reports/summary/")
    transactions = api.get("/api/payment-transactions/")
    filtered_transactions = api.get(
        "/api/payment-transactions/",
        {"department": order_department},
    )
    wrong_order_department = api.get(
        "/api/payment-transactions/",
        {"department": scope["first_department"].code},
    )
    queue = api.get("/api/orders/payments-queue/")
    cashier_log = api.get("/api/orders/cashier-log/")
    department_summary = api.get("/api/orders/department-summary/")

    assert report.status_code == 200
    assert report.data["income"]["total"] == "40.00"
    assert report.data["shipped"]["revenue"] == "100.00"
    assert transactions.status_code == 200
    assert transactions.data["count"] == 2
    assert {row["id"] for row in transactions.data["results"]} == {
        first_confirmed.pk,
        first_pending.pk,
    }
    assert transactions.data["summary"]["paid_by_currency"]["KZT"] == "40.00"
    assert filtered_transactions.data["count"] == 2
    assert wrong_order_department.data["count"] == 0
    assert [row["id"] for row in queue.data] == [first_pending.pk]
    assert [row["order"] for row in cashier_log.data] == [first_order.pk]
    by_code = {row["code"]: row for row in department_summary.data}
    assert by_code[order_department]["orders"] == 1
    assert by_code[order_department]["revenue"] == "100.00"


def test_top_level_payment_actions_hide_foreign_department_ids(
    auth_client,
    ownership_scope,
):
    scope = ownership_scope
    order = _order(
        scope["second_client"],
        department=scope["second_department"].code,
        status="shipped",
    )
    confirmed = Payment.objects.create(
        order=order,
        amount=Decimal("50.00"),
        method="cash",
        status="confirmed",
    )
    received = Payment.objects.create(
        order=order,
        amount=Decimal("20.00"),
        method="cash",
        status="received",
    )
    rejected = Payment.objects.create(
        order=order,
        amount=Decimal("10.00"),
        method="cash",
        status="rejected",
    )
    issuable = Payment.objects.create(
        order=order,
        amount=Decimal("20.00"),
        method="invoice",
        status="received",
    )
    api = auth_client(scope["user"])

    responses = [
        api.get(f"/api/payment-transactions/{confirmed.pk}/receipt/"),
        api.post(
            f"/api/payment-transactions/{confirmed.pk}/refund/",
            {"mode": "cash", "amount": "1.00", "reason": "Проверка"},
            format="json",
        ),
        api.post(
            f"/api/payment-transactions/{received.pk}/reject/",
            {"reason": "Проверка"},
            format="json",
        ),
        api.post(
            f"/api/payment-transactions/{rejected.pk}/restore/",
            {},
            format="json",
        ),
        api.post(
            f"/api/payment-transactions/{issuable.pk}/issue/",
            {"phone_number": "+77000000000"},
            format="json",
        ),
    ]

    assert [response.status_code for response in responses] == [404] * 5
    confirmed.refresh_from_db()
    received.refresh_from_db()
    rejected.refresh_from_db()
    issuable.refresh_from_db()
    assert confirmed.refunded_amount == Decimal("0.00")
    assert received.status == "received"
    assert rejected.status == "rejected"
    assert issuable.status == "received"
