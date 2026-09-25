from contextlib import contextmanager
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from apps.catalog.models import Product
from apps.clients.models import Client, Store
from apps.eventlog.models import EventLog
from apps.orders import apipay as apipay_services
from apps.orders.apipay import (
    cancel_invoice,
    create_invoice,
    create_refund,
)
from apps.orders.models import (
    ApiPayInvoice,
    Order,
    OrderItem,
    Payment,
    PaymentRefund,
    StatusChangeRequest,
)
from apps.orders.refunds import create_cash_refund
from apps.sales.models import Department
from apps.warehouse.models import StockItem

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("apipay_department")]


@pytest.fixture
def ownership_scope(user_with_perms):
    first_department = Department.objects.create(
        code="ownership-a",
        name="Владельцы A",
    )
    # Провайдерские пути берут ключ ApiPay из отдела заказа.
    first_department.set_apipay_api_key("ownership-a-key")
    first_department.save()
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
            "orders.confirm",
            "orders.correct_price",
            "payments.view",
            "payments.create",
            "payments.confirm",
            "reports.view",
            "monoblock.view",
            "loader.confirm",
            "orders.rollback",
        ],
        department=first_department,
    )
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
        # Заявка (draft/pending) — общая очередь кассы, её карточку открывает
        # любой отдел (test_shared_confirm_queue.py). Здесь — обычный заказ.
        status="confirmed",
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
    assert api.delete(f"/api/orders/{second.pk}/purge/").status_code == 400
    second.refresh_from_db()
    assert second.deleted_at == deleted_at
    assert second.purged_at is None


def test_unassigned_and_superuser_keep_global_order_access(
    auth_client,
    ownership_scope,
    user_with_perms,
    admin_user,
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

    for user in (unassigned, admin_user):
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
    # Очередь подтверждения общая для всех отделов (test_shared_confirm_queue.py);
    # отчёт, транзакции и сводка — только свой отдел.
    assert [row["id"] for row in queue.data] == [first_pending.pk, second_pending.pk]
    # Закреплённый сотрудник видит одну карточку — своего отдела, куда идут все его заказы.
    assert [row["code"] for row in department_summary.data] == [scope["first_department"].code]
    assert department_summary.data[0]["orders"] == 1
    assert department_summary.data[0]["revenue"] == "100.00"


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

    assert [response.status_code for response in responses] == [404] * 4
    confirmed.refresh_from_db()
    rejected.refresh_from_db()
    issuable.refresh_from_db()
    assert confirmed.refunded_amount == Decimal("0.00")
    assert rejected.status == "rejected"
    assert issuable.status == "received"


def test_post_board_and_dashboard_projection_are_ownership_scoped(
    auth_client,
    ownership_scope,
):
    scope = ownership_scope
    own = _order(
        scope["first_client"],
        department=scope["first_department"].code,
        status="arrived",
    )
    foreign = _order(
        scope["second_client"],
        department=scope["first_department"].code,
        status="arrived",
    )
    Payment.objects.create(order=own, amount="10.00", status="received")
    Payment.objects.create(order=foreign, amount="90.00", status="received")
    api = auth_client(scope["user"])

    post_board = api.get("/api/orders/", {"post_board": "1"})
    dashboard = api.get("/api/orders/dashboard-operational/")

    assert post_board.status_code == 200
    assert [row["id"] for row in post_board.data] == [own.pk]
    assert dashboard.status_code == 200
    assert [row["id"] for row in dashboard.data["queue"]] == [own.pk]
    # «Подтвердить оплаты» ведёт в «Кассу», а её очередь общая для всех
    # отделов: число на дашборде совпадает с очередью, а не со своими заказами.
    queue = api.get("/api/orders/payments-queue/")
    assert queue.status_code == 200
    assert dashboard.data["attention"]["pending_payments"] == len(queue.data) == 2


def test_dashboard_pending_payments_counts_the_cashier_queue(auth_client, ownership_scope):
    """Онлайн-оплата ждёт клиента, а не кассу; заявка на наличные — наоборот."""
    scope = ownership_scope
    order = _order(
        scope["first_client"],
        department=scope["first_department"].code,
        status="shipped",
    )
    online = Payment.objects.create(order=order, amount="10.00", method="kaspi", status="received")
    ApiPayInvoice.objects.create(
        payment=online, invoice_id=770001, channel="qr", status="pending",
        idempotency_key=f"asyl-payment-{online.pk}",
    )
    cash_requests = [
        Payment.objects.create(order=order, amount="20.00", method="cash", status="requested"),
        Payment.objects.create(order=order, amount="30.00", method="remote", status="requested"),
    ]
    api = auth_client(scope["user"])

    dashboard = api.get("/api/orders/dashboard-operational/")
    queue = api.get("/api/orders/payments-queue/")

    assert sorted(row["id"] for row in queue.data) == [row.pk for row in cash_requests]
    assert dashboard.data["attention"]["pending_payments"] == 2


@pytest.mark.parametrize(
    ("method", "suffix", "payload"),
    [
        ("patch", "", {"notes": "cross-tenant edit"}),
        ("post", "correct-price/", {"total_amount": "1.00"}),
        ("post", "payments/", {"amount": "1.00", "method": "cash"}),
        ("post", "payments/{payment}/receive/", {}),
        ("post", "payments/{payment}/confirm/", {}),
        ("post", "payments/{payment}/reopen/", {}),
        ("post", "payments/{payment}/reject/", {"reason": "no"}),
        # confirm/ и reject/ заявки — общая очередь кассы: test_shared_confirm_queue.py.
        ("post", "set-status/", {"status": "confirmed"}),
        ("post", "rollback-shipment/", {"reason": "no"}),
        ("post", "status-requests/{status_request}/approve/", {}),
        ("post", "status-requests/{status_request}/reject/", {}),
    ],
)
def test_foreign_order_detail_actions_are_hidden_before_action_logic(
    auth_client,
    ownership_scope,
    method,
    suffix,
    payload,
):
    scope = ownership_scope
    foreign = _order(
        scope["second_client"],
        department=scope["second_department"].code,
    )
    # Подтверждённая оплата не стоит в общей очереди кассы — её действия
    # остаются только отделу клиента.
    payment = Payment.objects.create(
        order=foreign,
        amount="10.00",
        method="cash",
        status="confirmed",
    )
    status_request = StatusChangeRequest.objects.create(
        order=foreign,
        to_status="confirmed",
    )
    path = (
        f"/api/orders/{foreign.pk}/"
        + suffix.format(
            payment=payment.pk,
            status_request=status_request.pk,
        )
    )
    api = auth_client(scope["user"])

    if method == "get":
        response = api.get(path)
    else:
        response = getattr(api, method)(path, payload, format="json")

    assert response.status_code == 404
    foreign.refresh_from_db()
    payment.refresh_from_db()
    status_request.refresh_from_db()
    assert foreign.notes == ""
    assert foreign.status == "draft"
    assert payment.status == "confirmed"
    assert status_request.status == "pending"


@pytest.mark.parametrize(
    "suffix",
    [
        "payments/{payment}/receive/",
        "payments/{payment}/confirm/",
        "payments/{payment}/reopen/",
        "payments/{payment}/reject/",
        "status-requests/{status_request}/approve/",
        "status-requests/{status_request}/reject/",
    ],
)
def test_nested_child_ids_cannot_cross_from_foreign_to_owned_order(
    auth_client,
    ownership_scope,
    suffix,
):
    scope = ownership_scope
    own = _order(
        scope["first_client"],
        department=scope["first_department"].code,
    )
    foreign = _order(
        scope["second_client"],
        department=scope["second_department"].code,
    )
    payment = Payment.objects.create(
        order=foreign,
        amount="10.00",
        method="cash",
        status="received",
    )
    status_request = StatusChangeRequest.objects.create(
        order=foreign,
        to_status="confirmed",
    )
    path = (
        f"/api/orders/{own.pk}/"
        + suffix.format(
            payment=payment.pk,
            status_request=status_request.pk,
        )
    )

    response = auth_client(scope["user"]).post(
        path,
        {"reason": "cross-tenant child"},
        format="json",
    )

    assert response.status_code == 404
    payment.refresh_from_db()
    status_request.refresh_from_db()
    assert payment.status == "received"
    assert status_request.status == "pending"


def test_locked_provider_and_refund_services_recheck_department_ownership(
    ownership_scope,
    monkeypatch,
):
    scope = ownership_scope
    order = _order(
        scope["first_client"],
        department=scope["first_department"].code,
        status="shipped",
    )
    issue_payment = Payment.objects.create(
        order=order,
        amount="10.00",
        method="invoice",
        status="received",
    )
    cash_payment = Payment.objects.create(
        order=order,
        amount="20.00",
        method="cash",
        status="confirmed",
    )
    provider_payment = Payment.objects.create(
        order=order,
        amount="30.00",
        method="invoice",
        status="confirmed",
    )
    provider_invoice = ApiPayInvoice.objects.create(
        payment=provider_payment,
        invoice_id=123456,
        idempotency_key=f"scope-refund-{provider_payment.pk}",
        status="paid",
        channel="phone",
    )
    scope["first_client"].department = scope["second_department"]
    scope["first_client"].save(update_fields=["department"])

    def unexpected_provider_call(*args, **kwargs):
        pytest.fail("department rejection must happen before an ApiPay call")

    monkeypatch.setattr("apps.orders.apipay.api_request", unexpected_provider_call)

    with pytest.raises(PermissionDenied):
        create_invoice(issue_payment, user=scope["user"])
    with pytest.raises(PermissionDenied):
        create_cash_refund(
            cash_payment,
            scope["user"],
            amount="1.00",
            reason="scope changed",
        )
    with pytest.raises(PermissionDenied):
        create_refund(
            provider_invoice,
            scope["user"],
            amount="1.00",
            reason="scope changed",
        )
    with pytest.raises(PermissionDenied):
        cancel_invoice(provider_invoice, user=scope["user"])

    assert not ApiPayInvoice.objects.filter(payment=issue_payment).exists()
    assert not PaymentRefund.objects.filter(
        payment__in=[cash_payment, provider_payment]
    ).exists()


@pytest.mark.parametrize("operation", ["invoice", "refund"])
def test_provider_side_effect_rechecks_scope_after_local_reservation(
    ownership_scope,
    monkeypatch,
    operation,
):
    scope = ownership_scope
    order = _order(
        scope["first_client"],
        department=scope["first_department"].code,
        status="shipped",
    )
    payment = Payment.objects.create(
        order=order,
        amount="30.00",
        method="invoice",
        status="confirmed" if operation == "refund" else "received",
    )
    provider_invoice = None
    if operation == "refund":
        provider_invoice = ApiPayInvoice.objects.create(
            payment=payment,
            invoice_id=654321,
            idempotency_key=f"scope-gap-refund-{payment.pk}",
            status="paid",
            channel="phone",
        )

    original_fence = apipay_services.provider_scope_fence

    @contextmanager
    def transfer_after_reservation(order_id, user, **kwargs):
        # Change real ownership at the transaction boundary. The authorization
        # check may live in a shared service; do not replace or bypass it.
        if operation == "refund":
            assert PaymentRefund.objects.filter(
                payment=payment, status="pending",
            ).exists()
        else:
            assert ApiPayInvoice.objects.filter(payment=payment).exists()
        Client.objects.filter(pk=order.client_id).update(
            department=scope["second_department"]
        )
        with original_fence(order_id, user, **kwargs) as locked_order:
            yield locked_order

    def unexpected_provider_call(*args, **kwargs):
        pytest.fail("the second scope fence must reject before an ApiPay call")

    monkeypatch.setattr(
        apipay_services,
        "provider_scope_fence",
        transfer_after_reservation,
    )
    monkeypatch.setattr(
        apipay_services,
        "api_request",
        unexpected_provider_call,
    )

    with pytest.raises(PermissionDenied):
        if operation == "invoice":
            create_invoice(
                payment,
                user=scope["user"],
                phone_number="87771234567",
            )
        else:
            create_refund(
                provider_invoice,
                scope["user"],
                amount="1.00",
                reason="scope moved between phases",
            )

    if operation == "invoice":
        payment.refresh_from_db()
        assert payment.status == "rejected"
        assert ApiPayInvoice.objects.get(payment=payment).status == "error"
    else:
        payment.refresh_from_db()
        refund = PaymentRefund.objects.get(payment=payment)
        assert refund.status == "failed"
        assert payment.pending_refund_amount == Decimal(0)
