"""Возврат по Kaspi QR через ссылку покупателю: ссылка, подтверждение, один execute."""

import hashlib
import hmac
import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.clients.models import Client
from apps.orders.apipay import ApiPayAPIError
from apps.orders.models import ApiPayInvoice, ApiPayQrRefund, Order, Payment, PaymentRefund
from apps.orders.qr_refunds import reconcile_qr_refunds
from apps.orders.refund_reconciliation import reconcile_apipay_refunds

pytestmark = pytest.mark.django_db

LINK = "https://qr.apipay.kz/refund/secret-token"


@pytest.fixture(autouse=True)
def _department_key(apipay_department):
    return apipay_department


@pytest.fixture
def paid_qr():
    client = Client.objects.create_with_user(first_name="Покупатель", phone="+7 700 123-45-67")
    order = Order.objects.create(client=client, status="shipped", currency="KZT")
    payment = Payment.objects.create(order=order, amount="5000.00", method="kaspi", status="confirmed")
    paid_at = timezone.now() - timedelta(hours=1)
    invoice = ApiPayInvoice.objects.create(
        payment=payment, invoice_id=355523, channel="qr", status="paid", paid_at=paid_at,
        idempotency_key=f"asyl-payment-{payment.pk}-v1",
    )
    return payment, invoice


class FakeApiPay:
    """Отвечает как ApiPay по методу и пути; ведёт журнал вызовов."""

    def __init__(self, paid_at, *, operations=None, execute=None, snapshot="customer_identified"):
        self.calls = []
        self.operations = operations if operations is not None else [
            {"ref": "op-order", "amount": 5000, "date": paid_at.isoformat(), "returnable": "full"},
            {"ref": "op-other", "amount": 700, "date": paid_at.isoformat(), "returnable": "full"},
        ]
        self.execute = execute or {"id": 42, "status": "completed", "refunded_amount": "5000.00"}
        self.snapshot = snapshot

    def __call__(self, method, path, payload=None, *, credentials, timeout=None):
        self.calls.append((method, path, payload))
        if (method, path) == ("POST", "/qr-refunds/links"):
            return {"id": 42, "status": "awaiting_customer", "customer_url": LINK,
                    "link_expires_at": (timezone.now() + timedelta(hours=24)).isoformat()}
        if (method, path) == ("GET", "/qr-refunds/42/operations"):
            return {"operations": self.operations, "has_more": False}
        if (method, path) == ("POST", "/qr-refunds/42/execute"):
            if isinstance(self.execute, Exception):
                raise self.execute
            return self.execute
        if (method, path) == ("GET", "/qr-refunds/42"):
            return {"id": 42, "status": self.snapshot, "client_name": "Иван И."}
        if (method, path) == ("DELETE", "/qr-refunds/links/42"):
            return {"id": 42, "status": "expired", "error_code": "qr_refund_link_revoked"}
        raise AssertionError(f"unexpected ApiPay call {method} {path}")

    def executes(self):
        return [call for call in self.calls if call[1].endswith("/execute")]


def _signed_post(api_client, payload, secret="webhook-secret"):
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return api_client.post("/api/webhooks/apipay/", data=body, content_type="application/json",
                           HTTP_X_WEBHOOK_SIGNATURE=signature)


def _identified(session_id=42):
    return {"event": "qr_refund.identified", "timestamp": timezone.now().isoformat(),
            "qr_refund": {"id": session_id, "status": "customer_identified", "client_name": "Иван И."}}


def _start(auth_client, user, payment, fake, amount="5000.00"):
    with patch("apps.orders.apipay.api_request", side_effect=fake):
        return auth_client(user).post(
            f"/api/payment-transactions/{payment.pk}/refund/",
            {"reason": "Ошибочная оплата", "amount": amount},
            format="json",
        )


def test_qr_payment_refund_issues_customer_link_instead_of_invoice_refund(auth_client, accountant, paid_qr):
    payment, invoice = paid_qr
    fake = FakeApiPay(invoice.paid_at)

    response = _start(auth_client, accountant, payment, fake)

    assert response.status_code == 201, response.data
    assert response.data["method"] == "apipay_qr"
    assert response.data["qr_refund"]["customer_url"] == LINK
    assert fake.calls == [("POST", "/qr-refunds/links", {})]
    refund = PaymentRefund.objects.get(payment=payment)
    assert (refund.method, refund.status) == ("apipay_qr", "pending")
    payment.refresh_from_db()
    assert payment.pending_refund_amount == Decimal("5000.00")
    session = ApiPayQrRefund.objects.get()
    assert LINK not in session.customer_url_encrypted
    state = auth_client(accountant).get(f"/api/payment-transactions/{payment.pk}/qr-refund/").data
    assert (state["status"], state["customer_url"]) == ("awaiting_customer", LINK)
    # Второй возврат по той же оплате не выпускает вторую ссылку.
    assert _start(auth_client, accountant, payment, fake, amount="100").status_code == 400


def test_identified_webhook_refunds_the_order_payment_once(
    auth_client, accountant, api_client, paid_qr, django_capture_on_commit_callbacks
):
    payment, invoice = paid_qr
    fake = FakeApiPay(invoice.paid_at)
    _start(auth_client, accountant, payment, fake)

    with patch("apps.orders.apipay.api_request", side_effect=fake):
        with django_capture_on_commit_callbacks(execute=True):
            assert _signed_post(api_client, _identified()).status_code == 200
        with django_capture_on_commit_callbacks(execute=True):
            _signed_post(api_client, _identified())

    assert fake.executes() == [("POST", "/qr-refunds/42/execute", {"operation_ref": "op-order"})]
    session = ApiPayQrRefund.objects.get()
    assert session.status == "completed"
    payment.refresh_from_db()
    assert payment.refunded_amount == Decimal("5000.00")
    assert payment.pending_refund_amount == Decimal("0.00")


def test_partial_refund_sends_amount(auth_client, accountant, api_client, paid_qr, django_capture_on_commit_callbacks):
    payment, invoice = paid_qr
    fake = FakeApiPay(invoice.paid_at, execute={"id": 42, "status": "completed", "refunded_amount": "1500.00"})
    _start(auth_client, accountant, payment, fake, amount="1500.00")

    with patch("apps.orders.apipay.api_request", side_effect=fake), django_capture_on_commit_callbacks(execute=True):
        _signed_post(api_client, _identified())

    assert fake.executes() == [("POST", "/qr-refunds/42/execute", {"operation_ref": "op-order", "amount": 1500.0})]
    payment.refresh_from_db()
    assert payment.refunded_amount == Decimal("1500.00")


def test_ambiguous_purchases_wait_for_cashier_choice(
    auth_client, accountant, api_client, paid_qr, django_capture_on_commit_callbacks
):
    payment, invoice = paid_qr
    moment = invoice.paid_at.isoformat()
    fake = FakeApiPay(invoice.paid_at, operations=[
        {"ref": "op-a", "amount": 5000, "date": moment, "returnable": "full"},
        {"ref": "op-b", "amount": 5000, "date": moment, "returnable": "full"},
    ])
    _start(auth_client, accountant, payment, fake)

    with patch("apps.orders.apipay.api_request", side_effect=fake):
        with django_capture_on_commit_callbacks(execute=True):
            _signed_post(api_client, _identified())
        state = auth_client(accountant).get(f"/api/payment-transactions/{payment.pk}/qr-refund/").data
        assert [op["ref"] for op in state["operations"]] == ["op-a", "op-b"]
        assert fake.executes() == []

        url = f"/api/payment-transactions/{payment.pk}/qr-refund/execute/"
        response = auth_client(accountant).post(url, {"operation_ref": "op-b"}, format="json")
        assert response.status_code == 200, response.data
        assert response.data["status"] == "completed"
        assert auth_client(accountant).post(url, {"operation_ref": "op-b"}, format="json").status_code == 400

    assert fake.executes() == [("POST", "/qr-refunds/42/execute", {"operation_ref": "op-b"})]


def test_uncertain_execute_holds_reservation_and_is_never_repeated(
    auth_client, accountant, api_client, paid_qr, django_capture_on_commit_callbacks
):
    payment, invoice = paid_qr
    fake = FakeApiPay(invoice.paid_at, execute=ApiPayAPIError(503, "apipay_unavailable", "timeout", {}))
    _start(auth_client, accountant, payment, fake)

    with patch("apps.orders.apipay.api_request", side_effect=fake):
        with django_capture_on_commit_callbacks(execute=True):
            _signed_post(api_client, _identified())
        assert ApiPayQrRefund.objects.get().status == "executing"

        fake.snapshot = "execution_uncertain"
        reconcile_qr_refunds(limit=5)
        reconcile_qr_refunds(limit=5)

    session = ApiPayQrRefund.objects.get()
    assert session.status == "execution_uncertain"
    assert "Не повторяйте" in session.error_message
    assert len(fake.executes()) == 1
    payment.refresh_from_db()
    assert payment.pending_refund_amount == Decimal("5000.00")


def test_pre_money_rejection_releases_reservation(
    auth_client, accountant, api_client, paid_qr, django_capture_on_commit_callbacks
):
    payment, invoice = paid_qr
    fake = FakeApiPay(invoice.paid_at, execute=ApiPayAPIError(422, "refund_insufficient_funds", "no funds", {}))
    _start(auth_client, accountant, payment, fake)

    with patch("apps.orders.apipay.api_request", side_effect=fake), django_capture_on_commit_callbacks(execute=True):
        _signed_post(api_client, _identified())

    session = ApiPayQrRefund.objects.get()
    assert session.status == "failed"
    assert "не хватает денег" in session.error_message
    payment.refresh_from_db()
    assert payment.pending_refund_amount == Decimal("0.00")
    assert PaymentRefund.objects.get().status == "failed"


def test_reconciliation_executes_when_identified_webhook_was_lost(auth_client, accountant, paid_qr):
    payment, invoice = paid_qr
    fake = FakeApiPay(invoice.paid_at)
    _start(auth_client, accountant, payment, fake)

    with patch("apps.orders.apipay.api_request", side_effect=fake):
        stats = reconcile_qr_refunds(limit=5)

    assert stats["checked"] == 1
    assert ApiPayQrRefund.objects.get().status == "completed"
    assert len(fake.executes()) == 1


def test_cashier_revokes_unopened_link(auth_client, accountant, paid_qr):
    payment, invoice = paid_qr
    fake = FakeApiPay(invoice.paid_at)
    _start(auth_client, accountant, payment, fake)

    with patch("apps.orders.apipay.api_request", side_effect=fake):
        response = auth_client(accountant).post(f"/api/payment-transactions/{payment.pk}/qr-refund/revoke/")

    assert response.status_code == 200, response.data
    assert response.data["status"] == "expired"
    assert ("DELETE", "/qr-refunds/links/42", None) in fake.calls
    payment.refresh_from_db()
    assert payment.pending_refund_amount == Decimal("0.00")


def test_generic_refund_sweep_does_not_release_waiting_qr_refund(auth_client, accountant, paid_qr):
    payment, invoice = paid_qr
    _start(auth_client, accountant, payment, FakeApiPay(invoice.paid_at))
    PaymentRefund.objects.update(created_at=timezone.now() - timedelta(days=1))

    with patch("apps.orders.apipay.api_request", side_effect=AssertionError("no provider call expected")):
        reconcile_apipay_refunds(limit=10, orphan_grace=timedelta(0), sweep_stale_after=timedelta(0))

    assert PaymentRefund.objects.get().status == "pending"


def test_uncertain_refund_settles_when_apipay_later_confirms_it(
    auth_client, accountant, api_client, paid_qr, django_capture_on_commit_callbacks
):
    payment, invoice = paid_qr
    fake = FakeApiPay(invoice.paid_at, execute={"id": 42, "status": "execution_uncertain", "error_code": "qr_refund_execution_uncertain"})
    _start(auth_client, accountant, payment, fake)
    with patch("apps.orders.apipay.api_request", side_effect=fake), django_capture_on_commit_callbacks(execute=True):
        _signed_post(api_client, _identified())
    assert ApiPayQrRefund.objects.get().status == "execution_uncertain"

    completed = {"event": "qr_refund.completed", "timestamp": timezone.now().isoformat(),
                 "qr_refund": {"id": 42, "status": "completed", "refunded_amount": "5000.00"}}
    assert _signed_post(api_client, completed).status_code == 200

    assert ApiPayQrRefund.objects.get().status == "completed"
    payment.refresh_from_db()
    assert (payment.refunded_amount, payment.pending_refund_amount) == (Decimal("5000.00"), Decimal("0.00"))
    assert len(fake.executes()) == 1
