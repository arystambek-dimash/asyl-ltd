from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.apipay import (
    ApiPayAPIError,
    ApiPayConfigurationError,
    _sync_refund_totals,
    apply_refund_status,
    create_refund,
    get_invoice_refunds,
)
from apps.orders.models import (
    ApiPayInvoice,
    Order,
    OrderItem,
    Payment,
    PaymentRefund,
)
from apps.orders.refund_reconciliation import reconcile_apipay_refunds

pytestmark = pytest.mark.django_db


def _invoice(*, channel="phone", amount="100.00", invoice_id=800):
    client = Client.objects.create(
        first_name="Возврат",
        phone="87762838451",
    )
    product = Product.objects.create(
        name=f"Товар для возврата {invoice_id}",
        color="Red",
        weight_kg="50",
        price=amount,
    )
    order = Order.objects.create(
        client=client,
        status="shipped",
        currency="KZT",
    )
    OrderItem.objects.create(
        order=order,
        product=product,
        quantity=1,
        unit_price=amount,
    )
    payment = Payment.objects.create(
        order=order,
        amount=amount,
        method="kaspi" if channel == "qr" else "invoice",
        status="confirmed",
        confirmed_at=timezone.now(),
    )
    invoice = ApiPayInvoice.objects.create(
        payment=payment,
        invoice_id=invoice_id,
        idempotency_key=f"asyl-payment-{payment.id}",
        channel=channel,
        status="paid",
    )
    return invoice


def _reserve(invoice, *, amount="10.00", reason="Возврат", created_at=None):
    refund = PaymentRefund.objects.create(
        payment=invoice.payment,
        amount=amount,
        method="apipay",
        status="pending",
        reason=reason,
    )
    if created_at is not None:
        PaymentRefund.objects.filter(pk=refund.pk).update(created_at=created_at)
        refund.refresh_from_db()
    _sync_refund_totals(invoice.payment, invoice.payment.order)
    return refund


@patch("apps.orders.apipay.api_request")
def test_refund_status_read_uses_documented_invoice_refunds_endpoint(
    api_request,
):
    invoice = _invoice()
    api_request.return_value = {"refunds": [], "total": 0}

    assert get_invoice_refunds(invoice) == {"refunds": [], "total": 0}

    api_request.assert_called_once_with(
        "GET", f"/invoices/{invoice.invoice_id}/refunds"
    )


@patch("apps.orders.apipay.api_request")
def test_qr_invoice_refund_is_sent_to_apipay(api_request, accountant):
    invoice = _invoice(channel="qr")
    api_request.return_value = {
        "refund": {
            "id": 801,
            "amount": "25.00",
            "status": "pending",
            "reason": "QR возврат",
        }
    }

    refund = create_refund(
        invoice,
        accountant,
        amount="25.00",
        reason="QR возврат",
    )

    assert refund.refund_id == 801
    api_request.assert_called_once_with(
        "POST",
        f"/invoices/{invoice.invoice_id}/refund",
        {"amount": 25.0, "reason": "QR возврат"},
    )
    invoice.payment.refresh_from_db()
    assert invoice.payment.pending_refund_amount == Decimal("25.00")


@patch("apps.orders.apipay.log_event")
@patch("apps.orders.apipay.api_request")
def test_audit_log_failure_cannot_turn_accepted_refund_into_retryable_error(
    api_request, log_event, accountant,
):
    invoice = _invoice()
    api_request.return_value = {
        "refund": {
            "id": 811,
            "amount": "10.00",
            "status": "pending",
            "reason": "Проверка журнала",
        }
    }
    log_event.side_effect = RuntimeError("audit storage unavailable")

    refund = create_refund(
        invoice,
        accountant,
        amount="10.00",
        reason="Проверка журнала",
    )

    assert refund.refund_id == 811
    local = PaymentRefund.objects.get(payment=invoice.payment)
    assert local.provider_refund_id == refund.pk
    assert local.status == "pending"
    api_request.assert_called_once()


@patch("apps.orders.apipay.api_request")
def test_completed_webhook_racing_refund_response_is_not_duplicated_or_regressed(
    api_request, accountant,
):
    invoice = _invoice()

    def webhook_wins(*_args):
        apply_refund_status(
            invoice,
            {
                "id": 814,
                "amount": "10.00",
                "status": "completed",
                "reason": "Гонка ответа",
                "created_at": timezone.now().isoformat(),
            },
        )
        return {
            "refund": {
                "id": 814,
                "amount": "10.00",
                "status": "pending",
                "reason": "Гонка ответа",
            }
        }

    api_request.side_effect = webhook_wins

    refund = create_refund(
        invoice,
        accountant,
        amount="10.00",
        reason="Гонка ответа",
    )

    local = PaymentRefund.objects.get(payment=invoice.payment)
    invoice.payment.refresh_from_db()
    assert refund.status == "completed"
    assert local.status == "completed"
    assert local.provider_refund_id == refund.pk
    assert local.requested_by_id == accountant.pk
    assert PaymentRefund.objects.filter(payment=invoice.payment).count() == 1
    assert invoice.payment.refunded_amount == Decimal("10.00")
    assert invoice.payment.pending_refund_amount == Decimal("0.00")


@patch("apps.orders.apipay.api_request")
def test_second_refund_is_blocked_while_first_has_no_provider_id(
    api_request, accountant,
):
    invoice = _invoice()
    first = _reserve(invoice, amount="10.00")

    with pytest.raises(ValidationError) as raised:
        create_refund(
            invoice,
            accountant,
            amount="10.00",
            reason="Конкурентный возврат",
        )

    assert str(raised.value.detail["code"]) == "refund_submission_in_progress"
    assert PaymentRefund.objects.filter(payment=invoice.payment).count() == 1
    assert first.provider_refund_id is None
    api_request.assert_not_called()


@patch("apps.orders.apipay.api_request")
def test_provider_configuration_failure_releases_local_reservation(
    api_request, accountant,
):
    invoice = _invoice()
    api_request.side_effect = ApiPayConfigurationError("missing API key")

    with pytest.raises(ApiPayConfigurationError):
        create_refund(
            invoice,
            accountant,
            amount="10.00",
            reason="Конфигурация",
        )

    local = PaymentRefund.objects.get(payment=invoice.payment)
    invoice.payment.refresh_from_db()
    assert local.status == "failed"
    assert invoice.payment.pending_refund_amount == Decimal("0.00")
    assert invoice.payment.available_for_refund == Decimal("100.00")


@patch(
    "apps.orders.refund_reconciliation.get_invoice_refunds"
)
@patch("apps.orders.apipay.api_request")
def test_ambiguous_post_is_never_retried_and_is_released_after_observation(
    api_request, get_refunds, accountant,
):
    invoice = _invoice()
    api_request.side_effect = ApiPayAPIError(
        503,
        "apipay_unavailable",
        "Ответ возврата потерян",
        {},
    )

    with pytest.raises(ApiPayAPIError):
        create_refund(
            invoice,
            accountant,
            amount="10.00",
            reason="Сетевой timeout",
        )

    local = PaymentRefund.objects.get(payment=invoice.payment)
    assert local.status == "pending"
    assert local.provider_refund_id is None
    api_request.assert_called_once_with(
        "POST",
        f"/invoices/{invoice.invoice_id}/refund",
        {"amount": 10.0, "reason": "Сетевой timeout"},
    )

    now = timezone.now().replace(microsecond=0)
    PaymentRefund.objects.filter(pk=local.pk).update(
        created_at=now - timedelta(hours=1)
    )
    get_refunds.return_value = {"refunds": [], "total": 0}
    stats = reconcile_apipay_refunds(
        now=now,
        orphan_grace=timedelta(minutes=15),
    )

    local.refresh_from_db()
    assert stats.released_orphans == 1
    assert local.status == "failed"
    # Reconciliation only observes the documented GET endpoint. It does not
    # repeat the non-idempotent POST.
    assert api_request.call_count == 1


def test_webhook_correlates_same_amount_by_echoed_reason():
    invoice = _invoice()
    first = _reserve(invoice, amount="10.00", reason="Первая причина")
    second = _reserve(invoice, amount="10.00", reason="Вторая причина")

    apply_refund_status(
        invoice,
        {
            "id": 802,
            "amount": "10.00",
            "status": "completed",
            "reason": "Вторая причина",
            "created_at": timezone.now().isoformat(),
        },
    )

    first.refresh_from_db()
    second.refresh_from_db()
    invoice.payment.refresh_from_db()
    assert first.status == "pending"
    assert first.provider_refund_id is None
    assert second.status == "completed"
    assert second.provider_refund.refund_id == 802
    assert invoice.payment.refunded_amount == Decimal("10.00")
    assert invoice.payment.pending_refund_amount == Decimal("10.00")


def test_terminal_refund_status_cannot_regress_to_processing():
    invoice = _invoice()
    local = _reserve(invoice, amount="10.00", reason="Терминальный")
    payload = {
        "id": 807,
        "amount": "10.00",
        "status": "completed",
        "reason": "Терминальный",
    }
    assert apply_refund_status(invoice, payload) is True

    regressed = apply_refund_status(
        invoice,
        {**payload, "status": "processing"},
    )

    local.refresh_from_db()
    local.provider_refund.refresh_from_db()
    invoice.payment.refresh_from_db()
    assert regressed is False
    assert local.status == "completed"
    assert local.provider_refund.status == "completed"
    assert invoice.payment.refunded_amount == Decimal("10.00")
    assert invoice.payment.pending_refund_amount == Decimal("0.00")


def test_external_provider_refund_is_recorded_in_generic_payment_totals():
    invoice = _invoice()

    changed = apply_refund_status(
        invoice,
        {
            "id": 812,
            "amount": "15.00",
            "status": "completed",
            "reason": "Возврат кассиром Kaspi",
            "created_at": timezone.now().isoformat(),
        },
    )

    local = PaymentRefund.objects.get(payment=invoice.payment)
    invoice.payment.refresh_from_db()
    invoice.refresh_from_db()
    assert changed is True
    assert local.method == "apipay"
    assert local.status == "completed"
    assert local.provider_refund.refund_id == 812
    assert invoice.payment.refunded_amount == Decimal("15.00")
    assert invoice.payment.pending_refund_amount == Decimal("0.00")
    assert invoice.total_refunded == Decimal("15.00")


@patch(
    "apps.orders.refund_reconciliation.get_invoice_refunds"
)
def test_reconcile_correlates_legacy_concurrent_same_amount_refunds(
    get_refunds,
):
    invoice = _invoice()
    now = timezone.now().replace(microsecond=0)
    first = _reserve(
        invoice,
        amount="10.00",
        reason="Одинаковая причина",
        created_at=now - timedelta(seconds=2),
    )
    second = _reserve(
        invoice,
        amount="10.00",
        reason="Одинаковая причина",
        created_at=now - timedelta(seconds=1),
    )
    get_refunds.return_value = {
        "refunds": [
            {
                "id": 804,
                "amount": "10.00",
                "status": "completed",
                "reason": "Одинаковая причина",
                "created_at": (now - timedelta(seconds=1)).isoformat(),
            },
            {
                "id": 803,
                "amount": "10.00",
                "status": "completed",
                "reason": "Одинаковая причина",
                "created_at": (now - timedelta(seconds=2)).isoformat(),
            },
        ],
        "total": 2,
    }

    stats = reconcile_apipay_refunds(now=now)

    first.refresh_from_db()
    second.refresh_from_db()
    invoice.payment.refresh_from_db()
    assert stats.changed == 2
    assert stats.released_orphans == 0
    assert PaymentRefund.objects.filter(payment=invoice.payment).count() == 2
    assert first.provider_refund.refund_id == 803
    assert second.provider_refund.refund_id == 804
    assert first.status == second.status == "completed"
    assert invoice.payment.refunded_amount == Decimal("20.00")
    assert invoice.payment.pending_refund_amount == Decimal("0.00")


@patch(
    "apps.orders.refund_reconciliation.get_invoice_refunds"
)
def test_reconcile_advances_linked_pending_refund_to_completed(get_refunds):
    invoice = _invoice()
    local = _reserve(invoice, amount="10.00", reason="Timeout")
    get_refunds.return_value = {
        "refunds": [{
            "id": 805,
            "amount": "10.00",
            "status": "processing",
            "reason": "Timeout",
            "created_at": timezone.now().isoformat(),
        }],
        "total": 1,
    }

    first = reconcile_apipay_refunds()

    local.refresh_from_db()
    invoice.payment.refresh_from_db()
    assert first.changed == 1
    assert local.provider_refund.refund_id == 805
    assert local.status == "pending"
    assert invoice.payment.pending_refund_amount == Decimal("10.00")

    get_refunds.return_value["refunds"][0]["status"] = "completed"
    second = reconcile_apipay_refunds()

    local.refresh_from_db()
    invoice.payment.refresh_from_db()
    assert second.changed == 1
    assert local.status == "completed"
    assert invoice.payment.refunded_amount == Decimal("10.00")
    assert invoice.payment.pending_refund_amount == Decimal("0.00")


@patch(
    "apps.orders.refund_reconciliation.get_invoice_refunds"
)
def test_reconcile_failed_provider_refund_releases_reserved_amount(
    get_refunds,
):
    invoice = _invoice()
    local = _reserve(invoice, amount="10.00", reason="Неуспешный возврат")
    get_refunds.return_value = {
        "refunds": [{
            "id": 813,
            "amount": "10.00",
            "status": "failed",
            "reason": "Неуспешный возврат",
            "error_code": "refund_window_expired",
            "error_message": "Срок возврата истёк",
            "created_at": timezone.now().isoformat(),
        }],
        "total": 1,
    }

    stats = reconcile_apipay_refunds()

    local.refresh_from_db()
    invoice.payment.refresh_from_db()
    assert stats.changed == 1
    assert local.status == "failed"
    assert local.provider_refund.status == "failed"
    assert local.provider_refund.error_code == "refund_window_expired"
    assert invoice.payment.refunded_amount == Decimal("0.00")
    assert invoice.payment.pending_refund_amount == Decimal("0.00")
    assert invoice.payment.available_for_refund == Decimal("100.00")


@patch(
    "apps.orders.refund_reconciliation.get_invoice_refunds"
)
def test_complete_snapshot_releases_old_unobserved_timeout_reservation(
    get_refunds,
):
    invoice = _invoice()
    now = timezone.now().replace(microsecond=0)
    local = _reserve(
        invoice,
        amount="10.00",
        reason="Ответ потерян",
        created_at=now - timedelta(hours=1),
    )
    get_refunds.return_value = {"refunds": [], "total": 0}

    stats = reconcile_apipay_refunds(
        now=now,
        orphan_grace=timedelta(minutes=15),
    )

    local.refresh_from_db()
    invoice.payment.refresh_from_db()
    assert stats.released_orphans == 1
    assert stats.ambiguous == 0
    assert local.status == "failed"
    assert invoice.payment.pending_refund_amount == Decimal("0.00")
    assert invoice.payment.available_for_refund == Decimal("100.00")
    get_refunds.assert_called_once_with(invoice)


@patch(
    "apps.orders.refund_reconciliation.get_invoice_refunds"
)
def test_incomplete_snapshot_never_releases_ambiguous_reservation(
    get_refunds,
):
    invoice = _invoice()
    now = timezone.now().replace(microsecond=0)
    local = _reserve(
        invoice,
        amount="10.00",
        created_at=now - timedelta(hours=1),
    )
    get_refunds.return_value = {"refunds": [], "total": 1}

    stats = reconcile_apipay_refunds(
        now=now,
        orphan_grace=timedelta(minutes=15),
    )

    local.refresh_from_db()
    invoice.payment.refresh_from_db()
    assert stats.incomplete == 1
    assert stats.released_orphans == 0
    assert stats.ambiguous == 1
    assert local.status == "pending"
    assert invoice.payment.pending_refund_amount == Decimal("10.00")


@patch(
    "apps.orders.refund_reconciliation.get_invoice_refunds"
)
def test_malformed_refund_row_never_releases_ambiguous_reservation(
    get_refunds,
):
    invoice = _invoice()
    now = timezone.now().replace(microsecond=0)
    local = _reserve(
        invoice,
        amount="10.00",
        created_at=now - timedelta(hours=1),
    )
    get_refunds.return_value = {
        "refunds": [{
            "id": 815,
            "amount": "10.00",
            "status": "unknown-provider-state",
        }],
        "total": 1,
    }

    stats = reconcile_apipay_refunds(
        now=now,
        orphan_grace=timedelta(minutes=15),
    )

    local.refresh_from_db()
    invoice.payment.refresh_from_db()
    assert stats.incomplete == 1
    assert stats.released_orphans == 0
    assert stats.ambiguous == 1
    assert local.status == "pending"
    assert invoice.payment.pending_refund_amount == Decimal("10.00")


@patch(
    "apps.orders.refund_reconciliation.get_invoice_refunds"
)
def test_fractional_snapshot_total_never_releases_ambiguous_reservation(
    get_refunds,
):
    invoice = _invoice(invoice_id=816)
    now = timezone.now().replace(microsecond=0)
    local = _reserve(
        invoice,
        amount="10.00",
        created_at=now - timedelta(hours=1),
    )
    get_refunds.return_value = {"refunds": [], "total": 0.5}

    stats = reconcile_apipay_refunds(
        now=now,
        orphan_grace=timedelta(minutes=15),
    )

    local.refresh_from_db()
    invoice.payment.refresh_from_db()
    assert stats.failed == 1
    assert stats.released_orphans == 0
    assert local.status == "pending"
    assert invoice.payment.pending_refund_amount == Decimal("10.00")


@patch(
    "apps.orders.refund_reconciliation.get_invoice_refunds"
)
def test_one_observed_legacy_refund_completes_and_extra_reservation_releases(
    get_refunds,
):
    invoice = _invoice()
    now = timezone.now().replace(microsecond=0)
    absent = _reserve(
        invoice,
        amount="10.00",
        reason="Не принят",
        created_at=now - timedelta(hours=1, seconds=1),
    )
    observed = _reserve(
        invoice,
        amount="10.00",
        reason="Принят",
        created_at=now - timedelta(hours=1),
    )
    get_refunds.return_value = {
        "refunds": [{
            "id": 806,
            "amount": "10.00",
            "status": "completed",
            "reason": "Принят",
            "created_at": (now - timedelta(hours=1)).isoformat(),
        }],
        "total": 1,
    }

    stats = reconcile_apipay_refunds(
        now=now,
        orphan_grace=timedelta(minutes=15),
    )

    absent.refresh_from_db()
    observed.refresh_from_db()
    invoice.payment.refresh_from_db()
    assert observed.status == "completed"
    assert observed.provider_refund.refund_id == 806
    assert absent.status == "failed"
    assert stats.released_orphans == 1
    assert PaymentRefund.objects.filter(payment=invoice.payment).count() == 2
    assert invoice.payment.refunded_amount == Decimal("10.00")
    assert invoice.payment.pending_refund_amount == Decimal("0.00")


@patch(
    "apps.orders.refund_reconciliation.get_invoice_refunds"
)
def test_refund_reconciliation_is_oldest_first_and_round_robins_at_limit(
    get_refunds,
):
    now = timezone.now().replace(microsecond=0)
    oldest = _invoice(invoice_id=820)
    next_oldest = _invoice(invoice_id=821)
    _reserve(oldest, amount="10.00")
    _reserve(next_oldest, amount="10.00")
    ApiPayInvoice.objects.filter(pk=oldest.pk).update(
        refund_checked_at=now - timedelta(hours=2)
    )
    ApiPayInvoice.objects.filter(pk=next_oldest.pk).update(
        refund_checked_at=now - timedelta(hours=1)
    )
    get_refunds.side_effect = [
        ApiPayAPIError(503, "apipay_unavailable", "Временная ошибка", {}),
        {"refunds": [], "total": 0},
    ]

    first = reconcile_apipay_refunds(
        limit=1,
        now=now,
        orphan_grace=timedelta(days=1),
    )

    assert first.selected == 1
    assert first.failed == 1
    assert get_refunds.call_args.args[0].pk == oldest.pk
    oldest.refresh_from_db()
    assert oldest.refund_checked_at == now

    get_refunds.reset_mock()
    second = reconcile_apipay_refunds(
        limit=1,
        now=now + timedelta(seconds=1),
        orphan_grace=timedelta(days=1),
    )

    assert second.selected == 1
    assert get_refunds.call_args.args[0].pk == next_oldest.pk


@patch(
    "apps.orders.refund_reconciliation.get_invoice_refunds"
)
def test_sweep_discovers_external_completed_refund_without_local_request(
    get_refunds,
):
    invoice = _invoice(invoice_id=830)
    now = timezone.now().replace(microsecond=0)
    get_refunds.return_value = {
        "refunds": [{
            "id": 831,
            "amount": "15.00",
            "status": "completed",
            "reason": "Возврат создан вне CRM",
            "created_at": (now - timedelta(minutes=1)).isoformat(),
        }],
        "total": 1,
    }

    stats = reconcile_apipay_refunds(limit=1, now=now)

    local = PaymentRefund.objects.get(payment=invoice.payment)
    invoice.payment.refresh_from_db()
    invoice.refresh_from_db()
    assert stats.selected == 1
    assert stats.changed == 1
    assert local.provider_refund.refund_id == 831
    assert local.status == "completed"
    assert invoice.payment.refunded_amount == Decimal("15.00")
    assert invoice.total_refunded == Decimal("15.00")
    assert invoice.refund_checked_at == now


@patch(
    "apps.orders.refund_reconciliation.get_invoice_refunds"
)
def test_sweep_includes_partially_refunded_invoice_without_local_request(
    get_refunds,
):
    invoice = _invoice(invoice_id=832)
    ApiPayInvoice.objects.filter(pk=invoice.pk).update(
        status="partially_refunded"
    )
    now = timezone.now().replace(microsecond=0)
    get_refunds.return_value = {"refunds": [], "total": 0}

    stats = reconcile_apipay_refunds(limit=1, now=now)

    invoice.refresh_from_db()
    assert stats.selected == 1
    assert stats.failed == 0
    assert invoice.refund_checked_at == now
    get_refunds.assert_called_once()


@patch(
    "apps.orders.refund_reconciliation.get_invoice_refunds"
)
def test_discovery_budget_does_not_starve_local_pending_refunds(
    get_refunds,
):
    discoveries = [
        _invoice(invoice_id=840),
        _invoice(invoice_id=841),
        _invoice(invoice_id=842),
    ]
    pending = _invoice(invoice_id=843)
    _reserve(pending, amount="10.00")
    get_refunds.return_value = {"refunds": [], "total": 0}

    stats = reconcile_apipay_refunds(
        limit=2,
        orphan_grace=timedelta(days=1),
    )

    selected_ids = {
        call.args[0].pk for call in get_refunds.call_args_list
    }
    assert stats.selected == 2
    assert pending.pk in selected_ids
    assert len(selected_ids & {row.pk for row in discoveries}) == 1


@patch(
    "apps.orders.refund_reconciliation.get_invoice_refunds"
)
def test_limit_one_rotates_between_pending_and_provider_discovery(
    get_refunds,
):
    now = timezone.now().replace(microsecond=0)
    pending = _invoice(invoice_id=844)
    _reserve(pending, amount="10.00")
    provider_only = _invoice(invoice_id=845)
    get_refunds.return_value = {"refunds": [], "total": 0}

    for offset in range(3):
        reconcile_apipay_refunds(
            limit=1,
            now=now + timedelta(seconds=offset),
            orphan_grace=timedelta(days=1),
        )

    selected_ids = [
        call.args[0].pk for call in get_refunds.call_args_list
    ]
    assert selected_ids == [pending.pk, provider_only.pk, pending.pk]


@patch(
    "apps.orders.refund_reconciliation.get_invoice_refunds"
)
def test_provider_sweep_is_bounded_oldest_first_and_round_robins(
    get_refunds,
):
    now = timezone.now().replace(microsecond=0)
    oldest = _invoice(invoice_id=850)
    next_oldest = _invoice(invoice_id=851)
    newest = _invoice(invoice_id=852)
    ApiPayInvoice.objects.filter(pk=oldest.pk).update(
        refund_checked_at=now - timedelta(hours=3)
    )
    ApiPayInvoice.objects.filter(pk=next_oldest.pk).update(
        refund_checked_at=now - timedelta(hours=2)
    )
    ApiPayInvoice.objects.filter(pk=newest.pk).update(
        refund_checked_at=now - timedelta(hours=1)
    )
    get_refunds.side_effect = [
        ApiPayAPIError(503, "apipay_unavailable", "Временная ошибка", {}),
        {"refunds": [], "total": 0},
    ]

    first = reconcile_apipay_refunds(limit=1, now=now)

    assert first.selected == 1
    assert first.failed == 1
    assert get_refunds.call_args.args[0].pk == oldest.pk
    oldest.refresh_from_db()
    assert oldest.refund_checked_at == now

    get_refunds.reset_mock()
    second = reconcile_apipay_refunds(
        limit=1,
        now=now + timedelta(seconds=1),
    )

    assert second.selected == 1
    assert get_refunds.call_args.args[0].pk == next_oldest.pk


@patch(
    "apps.orders.refund_reconciliation.get_invoice_refunds"
)
def test_slower_overlapping_sweep_cannot_regress_refund_cursor(get_refunds):
    invoice = _invoice(invoice_id=853)
    observed_at = timezone.now().replace(microsecond=0)
    newer_observation = observed_at + timedelta(seconds=1)

    def finish_newer_worker_first(record):
        ApiPayInvoice.objects.filter(pk=record.pk).update(
            refund_checked_at=newer_observation
        )
        return {"refunds": [], "total": 0}

    get_refunds.side_effect = finish_newer_worker_first

    stats = reconcile_apipay_refunds(limit=1, now=observed_at)

    invoice.refresh_from_db()
    assert stats.selected == 1
    assert invoice.refund_checked_at == newer_observation


@patch(
    "apps.orders.refund_reconciliation.get_invoice_refunds"
)
def test_incomplete_sweep_applies_valid_external_refund_without_releasing_money(
    get_refunds,
):
    invoice = _invoice(invoice_id=860)
    now = timezone.now().replace(microsecond=0)
    ambiguous = _reserve(
        invoice,
        amount="10.00",
        created_at=now - timedelta(hours=1),
    )
    get_refunds.return_value = {
        "refunds": [
            {
                "id": 861,
                "amount": "15.00",
                "status": "completed",
                "reason": "Внешний возврат",
                "created_at": now.isoformat(),
            },
            {
                "id": 862,
                "amount": "broken",
                "status": "completed",
            },
        ],
        "total": 2,
    }

    stats = reconcile_apipay_refunds(
        limit=1,
        now=now,
        orphan_grace=timedelta(minutes=15),
    )

    ambiguous.refresh_from_db()
    invoice.payment.refresh_from_db()
    external = PaymentRefund.objects.get(
        payment=invoice.payment,
        provider_refund__refund_id=861,
    )
    assert stats.changed == 1
    assert stats.incomplete == 1
    assert stats.released_orphans == 0
    assert stats.ambiguous == 1
    assert ambiguous.status == "pending"
    assert external.status == "completed"
    assert invoice.payment.refunded_amount == Decimal("15.00")
    assert invoice.payment.pending_refund_amount == Decimal("10.00")


@patch(
    "apps.orders.refund_reconciliation.get_invoice_refunds"
)
def test_sweep_reconciles_refund_for_soft_deleted_order(get_refunds):
    invoice = _invoice(invoice_id=870)
    Order.all_objects.filter(pk=invoice.payment.order_id).update(
        deleted_at=timezone.now()
    )
    get_refunds.return_value = {
        "refunds": [{
            "id": 871,
            "amount": "20.00",
            "status": "completed",
            "reason": "Возврат после удаления заказа",
        }],
        "total": 1,
    }

    stats = reconcile_apipay_refunds(limit=1)

    local = PaymentRefund.objects.get(payment=invoice.payment)
    invoice.payment.refresh_from_db()
    assert stats.changed == 1
    assert local.provider_refund.refund_id == 871
    assert local.status == "completed"
    assert invoice.payment.refunded_amount == Decimal("20.00")
