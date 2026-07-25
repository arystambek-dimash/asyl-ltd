from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import call, patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.apipay import (
    ApiPayAPIError,
    create_invoice,
    recover_qr_invoice_mapping,
)
from apps.orders.management.commands.reconcile_apipay_invoices import (
    _backoff_delay,
    _request_budget_per_iteration,
)
from apps.orders.models import ApiPayInvoice, Order, OrderItem, Payment
from apps.orders.reconciliation import (
    ReconciliationStats,
    reconcile_apipay_invoices,
)
from apps.orders.refund_reconciliation import RefundReconciliationStats

pytestmark = pytest.mark.django_db


def _invoice(invoice_id: int, status: str = "pending") -> ApiPayInvoice:
    client = Client.objects.create(
        first_name=f"Клиент {invoice_id}",
        phone=f"8700{invoice_id:07d}"[-11:],
    )
    product = Product.objects.create(
        name=f"Товар {invoice_id}",
        color="Red",
        weight_kg="50",
        price="5000",
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
        unit_price=Decimal("5000.00"),
    )
    payment = Payment.objects.create(
        order=order,
        amount="5000.00",
        method="kaspi",
        status=(
            "rejected"
            if status in {"cancelled", "expired", "error", "superseded"}
            else "received"
        ),
    )
    return ApiPayInvoice.objects.create(
        payment=payment,
        invoice_id=invoice_id,
        idempotency_key=f"test-reconcile-{invoice_id}",
        status=status,
    )


def _set_observation_time(
    record: ApiPayInvoice,
    *,
    created_at,
    updated_at,
) -> ApiPayInvoice:
    ApiPayInvoice.objects.filter(pk=record.pk).update(
        created_at=created_at,
        updated_at=updated_at,
    )
    record.refresh_from_db()
    return record


@patch("apps.orders.reconciliation.check_invoice_statuses")
def test_reconcile_polls_stale_active_and_recent_closed_invoices(check_statuses):
    now = timezone.now().replace(microsecond=0)
    due_at = now - timedelta(minutes=5)
    pending = _set_observation_time(
        _invoice(101, "pending"),
        created_at=now - timedelta(hours=2),
        updated_at=due_at,
    )
    cancelled = _set_observation_time(
        _invoice(102, "cancelled"),
        created_at=now - timedelta(hours=2),
        updated_at=due_at,
    )
    paid = _set_observation_time(
        _invoice(103, "paid"),
        created_at=now - timedelta(hours=2),
        updated_at=due_at,
    )
    fresh = _set_observation_time(
        _invoice(104, "pending"),
        created_at=now - timedelta(hours=2),
        updated_at=now,
    )
    old = _set_observation_time(
        _invoice(105, "expired"),
        created_at=now - timedelta(hours=80),
        updated_at=due_at,
    )
    old_active = _set_observation_time(
        _invoice(106, "pending"),
        created_at=now - timedelta(hours=80),
        updated_at=due_at,
    )
    check_statuses.return_value = {
        "invoices": [
            {"id": 101, "status": "paid", "amount": "5000.00"},
            {"id": 102, "status": "paid", "amount": "5000.00"},
            {"id": 106, "status": "paid", "amount": "5000.00"},
        ]
    }

    stats = reconcile_apipay_invoices(
        batch_size=100,
        stale_after=timedelta(seconds=30),
        lookback=timedelta(hours=72),
        now=now,
    )

    check_statuses.assert_called_once()
    assert set(check_statuses.call_args.args[0]) == {101, 102, 106}
    assert stats == ReconciliationStats(
        selected=3,
        batches=1,
        fetched=3,
        changed=3,
    )
    pending.refresh_from_db()
    cancelled.refresh_from_db()
    paid.refresh_from_db()
    fresh.refresh_from_db()
    old.refresh_from_db()
    old_active.refresh_from_db()
    assert pending.status == "paid"
    assert pending.payment.status == "confirmed"
    assert cancelled.status == "paid"
    assert cancelled.payment.status == "confirmed"
    assert paid.status == "paid"
    assert fresh.status == "pending"
    assert old.status == "expired"
    assert old_active.status == "paid"
    assert old_active.payment.status == "confirmed"


@patch("apps.orders.apipay.api_request")
def test_ambiguous_qr_no_match_before_grace_stays_reserved(
    api_request,
):
    now = timezone.now().replace(microsecond=0)
    record = _set_observation_time(
        _invoice(151, "pending"),
        created_at=now - timedelta(minutes=10),
        updated_at=now - timedelta(minutes=5),
    )
    ApiPayInvoice.objects.filter(pk=record.pk).update(
        invoice_id=None,
        channel="qr",
        status="creating",
    )
    record.payment.status = "received"
    record.payment.save(update_fields=["status"])
    record.refresh_from_db()
    api_request.return_value = {
        "current_page": 1,
        "data": [],
        "total": 0,
    }

    recovered = recover_qr_invoice_mapping(record, checked_at=now)

    assert recovered is None
    record.refresh_from_db()
    record.payment.refresh_from_db()
    assert record.status == "creating"
    assert record.payment.status == "received"
    assert record.updated_at == now
    assert api_request.call_args.args[0] == "GET"
    assert "search=test-reconcile-151" in api_request.call_args.args[1]


@patch("apps.orders.apipay.api_request")
def test_ambiguous_qr_complete_no_match_after_grace_is_released(
    api_request,
):
    now = timezone.now().replace(microsecond=0)
    record = _set_observation_time(
        _invoice(152, "pending"),
        created_at=now - timedelta(minutes=31),
        updated_at=now - timedelta(minutes=5),
    )
    ApiPayInvoice.objects.filter(pk=record.pk).update(
        invoice_id=None,
        channel="qr",
        status="creating",
    )
    record.payment.status = "received"
    record.payment.save(update_fields=["status"])
    record.refresh_from_db()
    # Prove that all pages, not just page 1, are exhausted before release.
    first_page = [
        {"id": value, "external_order_id": f"unrelated-{value}"}
        for value in range(100)
    ]
    api_request.side_effect = [
        {
            "current_page": 1,
            "data": first_page,
            "total": 101,
        },
        {
            "current_page": 2,
            "data": [{"id": 999, "external_order_id": "unrelated-last"}],
            "total": 101,
        },
    ]

    recovered = recover_qr_invoice_mapping(record, checked_at=now)

    assert recovered is None
    assert api_request.call_count == 2
    assert "page=2" in api_request.call_args_list[1].args[1]
    record.refresh_from_db()
    record.payment.refresh_from_db()
    assert record.status == "error"
    assert record.error_code == "apipay_qr_not_found"
    assert record.payment.status == "rejected"
    assert record.response_payload["recovery"] == "not_found"


@patch("apps.orders.apipay.api_request")
def test_existing_ambiguous_qr_is_searched_and_never_posted(api_request):
    now = timezone.now().replace(microsecond=0)
    record = _set_observation_time(
        _invoice(153, "pending"),
        created_at=now - timedelta(minutes=5),
        updated_at=now - timedelta(minutes=1),
    )
    ApiPayInvoice.objects.filter(pk=record.pk).update(
        invoice_id=None,
        channel="qr",
        status="creating",
    )
    record.payment.status = "received"
    record.payment.save(update_fields=["status"])
    record.refresh_from_db()
    api_request.return_value = {
        "current_page": 1,
        "data": [],
        "total": 0,
    }

    with pytest.raises(ApiPayAPIError) as exc:
        create_invoice(record.payment, channel="qr")

    assert exc.value.error_code == "apipay_issue_recovery_pending"
    assert api_request.call_count == 1
    assert api_request.call_args.args[0] == "GET"


@patch("apps.orders.reconciliation.check_invoice_statuses")
def test_reconcile_continues_after_one_provider_batch_fails(check_statuses):
    now = timezone.now().replace(microsecond=0)
    observed = now - timedelta(minutes=5)
    invoices = [
        _set_observation_time(
            _invoice(invoice_id),
            created_at=now - timedelta(hours=1),
            updated_at=observed,
        )
        for invoice_id in (201, 202, 203)
    ]
    check_statuses.side_effect = [
        ApiPayAPIError(503, "apipay_unavailable", "temporary", {}),
        {
            "invoices": [
                {"id": 203, "status": "paid", "amount": "5000.00"},
            ]
        },
    ]

    stats = reconcile_apipay_invoices(
        batch_size=2,
        stale_after=timedelta(seconds=30),
        lookback=timedelta(hours=72),
        now=now,
    )

    assert check_statuses.call_count == 2
    assert stats.selected == 3
    assert stats.batches == 2
    assert stats.failed == 2
    assert stats.changed == 1
    for record in invoices:
        record.refresh_from_db()
    assert [record.status for record in invoices] == [
        "pending",
        "pending",
        "paid",
    ]


@patch("apps.orders.reconciliation.check_invoice_statuses")
def test_reconcile_honors_hard_provider_request_budget(check_statuses):
    now = timezone.now().replace(microsecond=0)
    observed = now - timedelta(minutes=5)
    for invoice_id in (221, 222, 223):
        _set_observation_time(
            _invoice(invoice_id),
            created_at=now - timedelta(hours=1),
            updated_at=observed,
        )
    check_statuses.return_value = {"invoices": []}

    stats = reconcile_apipay_invoices(
        batch_size=2,
        limit=999,
        request_budget=1,
        stale_after=timedelta(seconds=30),
        lookback=timedelta(hours=72),
        now=now,
    )

    check_statuses.assert_called_once_with([221, 222])
    assert stats.selected == 2
    assert stats.batches == 1


@patch("apps.orders.reconciliation.check_invoice_statuses")
@patch("apps.orders.apipay.api_request")
def test_qr_recovery_never_exceeds_monitor_request_budget(
    api_request,
    check_statuses,
):
    now = timezone.now().replace(microsecond=0)
    record = _set_observation_time(
        _invoice(231, "pending"),
        created_at=now - timedelta(minutes=31),
        updated_at=now - timedelta(minutes=5),
    )
    ApiPayInvoice.objects.filter(pk=record.pk).update(
        invoice_id=None,
        channel="qr",
        status="creating",
    )
    record.payment.status = "received"
    record.payment.save(update_fields=["status"])
    api_request.return_value = {
        "current_page": 1,
        "data": [
            {"id": value, "external_order_id": f"unrelated-{value}"}
            for value in range(100)
        ],
        "total": 101,
    }

    stats = reconcile_apipay_invoices(
        batch_size=100,
        limit=100,
        request_budget=1,
        stale_after=timedelta(seconds=30),
        now=now,
    )

    assert api_request.call_count == 1
    check_statuses.assert_not_called()
    assert stats.issue_selected == 1
    assert stats.issue_failed == 1
    record.refresh_from_db()
    record.payment.refresh_from_db()
    assert record.status == "creating"
    assert record.payment.status == "received"


@patch("apps.orders.reconciliation.apply_invoice_status")
@patch("apps.orders.reconciliation.check_invoice_statuses")
def test_reconcile_continues_after_one_payload_cannot_be_applied(
    check_statuses,
    apply_status,
):
    now = timezone.now().replace(microsecond=0)
    observed = now - timedelta(minutes=5)
    for invoice_id in (251, 252):
        _set_observation_time(
            _invoice(invoice_id),
            created_at=now - timedelta(hours=1),
            updated_at=observed,
        )
    check_statuses.return_value = {
        "invoices": [
            {"id": 251, "status": "paid", "amount": "wrong"},
            {"id": 252, "status": "paid", "amount": "5000.00"},
        ]
    }
    apply_status.side_effect = [ValueError("bad amount"), True]

    stats = reconcile_apipay_invoices(
        stale_after=timedelta(seconds=30),
        lookback=timedelta(hours=72),
        now=now,
    )

    assert apply_status.call_count == 2
    assert stats.failed == 1
    assert stats.changed == 1


@patch("apps.orders.reconciliation.check_invoice_statuses")
def test_reconcile_throttles_omitted_ids_and_ignores_unexpected_ids(
    check_statuses,
):
    now = timezone.now().replace(microsecond=0)
    invoice = _set_observation_time(
        _invoice(301),
        created_at=now - timedelta(hours=1),
        updated_at=now - timedelta(minutes=5),
    )
    check_statuses.return_value = {
        "invoices": [
            {"id": 999999, "status": "paid", "amount": "5000.00"},
        ]
    }

    stats = reconcile_apipay_invoices(
        stale_after=timedelta(seconds=30),
        lookback=timedelta(hours=72),
        now=now,
    )

    invoice.refresh_from_db()
    assert invoice.status == "pending"
    assert invoice.updated_at == now
    assert stats.missing == 1
    assert stats.unexpected == 1
    assert stats.changed == 0
    assert stats.failed == 0


@patch("apps.orders.reconciliation.check_invoice_statuses")
def test_reconcile_is_idempotent_when_status_is_unchanged(check_statuses):
    now = timezone.now().replace(microsecond=0)
    invoice = _set_observation_time(
        _invoice(401),
        created_at=now - timedelta(hours=1),
        updated_at=now - timedelta(minutes=5),
    )
    check_statuses.return_value = {
        "invoices": [
            {"id": 401, "status": "pending", "amount": "5000.00"},
        ]
    }

    stats = reconcile_apipay_invoices(
        stale_after=timedelta(seconds=30),
        lookback=timedelta(hours=72),
        now=now,
    )

    invoice.refresh_from_db()
    assert invoice.status == "pending"
    assert invoice.updated_at >= now
    assert stats.unchanged == 1
    assert stats.changed == 0


@patch(
    "apps.orders.management.commands.reconcile_apipay_invoices."
    "replay_pending_apipay_webhooks"
)
@patch("apps.orders.management.commands.reconcile_apipay_invoices._write_heartbeat")
@patch(
    "apps.orders.management.commands.reconcile_apipay_invoices.reconcile_apipay_refunds"
)
@patch(
    "apps.orders.management.commands.reconcile_apipay_invoices."
    "reconcile_apipay_invoices"
)
def test_reconcile_command_exposes_interval_staleness_and_batch_options(
    run, refund_run, heartbeat, replay
):
    run.return_value = ReconciliationStats(selected=2, changed=1)
    refund_run.return_value = RefundReconciliationStats(
        selected=1, changed=1, released_orphans=1
    )
    replay.return_value = {
        "processed": 3,
        "already_processed": 0,
        "waiting_for_invoice": 1,
        "failed": 0,
    }
    stdout = StringIO()

    call_command(
        "reconcile_apipay_invoices",
        "--once",
        "--interval=30",
        "--stale-seconds=45",
        "--lookback-hours=96",
        "--batch-size=25",
        "--refund-limit=30",
        "--refund-orphan-grace-seconds=1200",
        "--refund-sweep-stale-seconds=1300",
        "--request-budget-per-minute=80",
        stdout=stdout,
    )

    run.assert_called_once_with(
        batch_size=25,
        limit=250,
        request_budget=10,
        stale_after=timedelta(seconds=45),
        lookback=timedelta(hours=96),
    )
    refund_run.assert_called_once_with(
        limit=30,
        orphan_grace=timedelta(seconds=1200),
        sweep_stale_after=timedelta(seconds=1300),
    )
    replay.assert_called_once_with()
    assert "selected=2" in stdout.getvalue()
    assert "changed=1" in stdout.getvalue()
    assert "inbox_processed=3" in stdout.getvalue()
    assert "inbox_waiting=1" in stdout.getvalue()
    assert "refund_selected=1" in stdout.getvalue()
    assert "refund_released=1" in stdout.getvalue()
    assert "request_budget=40" in stdout.getvalue()
    assert [item.args[1] for item in heartbeat.call_args_list] == [
        "running",
        "ok",
    ]


@patch.dict(
    "os.environ",
    {
        "APIPAY_REFUND_RECONCILE_LIMIT": "17",
        "APIPAY_REFUND_ORPHAN_GRACE_SECONDS": "321",
        "APIPAY_REFUND_SWEEP_STALE_SECONDS": "654",
    },
)
@patch(
    "apps.orders.management.commands.reconcile_apipay_invoices."
    "replay_pending_apipay_webhooks"
)
@patch("apps.orders.management.commands.reconcile_apipay_invoices._write_heartbeat")
@patch(
    "apps.orders.management.commands.reconcile_apipay_invoices.reconcile_apipay_refunds"
)
@patch(
    "apps.orders.management.commands.reconcile_apipay_invoices."
    "reconcile_apipay_invoices"
)
def test_reconcile_command_reads_refund_budget_and_grace_from_environment(
    run,
    refund_run,
    heartbeat,
    replay,
):
    run.return_value = ReconciliationStats()
    refund_run.return_value = RefundReconciliationStats()
    replay.return_value = {
        "processed": 0,
        "already_processed": 0,
        "waiting_for_invoice": 0,
        "failed": 0,
    }

    call_command("reconcile_apipay_invoices", "--once")

    refund_run.assert_called_once_with(
        limit=17,
        orphan_grace=timedelta(seconds=321),
        sweep_stale_after=timedelta(seconds=654),
    )
    assert heartbeat.call_count == 2


@patch(
    "apps.orders.management.commands.reconcile_apipay_invoices."
    "replay_pending_apipay_webhooks"
)
@patch("apps.orders.management.commands.reconcile_apipay_invoices._write_heartbeat")
@patch(
    "apps.orders.management.commands.reconcile_apipay_invoices.reconcile_apipay_refunds"
)
@patch(
    "apps.orders.management.commands.reconcile_apipay_invoices."
    "reconcile_apipay_invoices"
)
def test_reconcile_command_clamps_monitor_to_half_provider_rate_limit(
    run,
    refund_run,
    heartbeat,
    replay,
):
    run.return_value = ReconciliationStats()
    refund_run.return_value = RefundReconciliationStats()
    replay.return_value = {
        "processed": 0,
        "already_processed": 0,
        "waiting_for_invoice": 0,
        "failed": 0,
    }

    call_command(
        "reconcile_apipay_invoices",
        "--once",
        "--interval=15",
        "--batch-size=500",
        "--refund-limit=500",
        "--request-budget-per-minute=10000",
    )

    # 25 requests per 15-second cycle = 100/minute maximum. One request is
    # reserved for a batch of up to 500 invoice IDs; 24 remain for refunds.
    run.assert_called_once()
    assert run.call_args.kwargs["limit"] == 500
    refund_run.assert_called_once()
    assert refund_run.call_args.kwargs["limit"] == 24
    assert heartbeat.call_count == 2


@patch(
    "apps.orders.management.commands.reconcile_apipay_invoices."
    "replay_pending_apipay_webhooks"
)
@patch("apps.orders.management.commands.reconcile_apipay_invoices._write_heartbeat")
@patch(
    "apps.orders.management.commands.reconcile_apipay_invoices.reconcile_apipay_refunds"
)
@patch(
    "apps.orders.management.commands.reconcile_apipay_invoices."
    "reconcile_apipay_invoices"
)
def test_reconcile_command_marks_reported_failures_unhealthy(
    run,
    refund_run,
    heartbeat,
    replay,
):
    run.return_value = ReconciliationStats(failed=1)
    refund_run.return_value = RefundReconciliationStats()
    replay.return_value = {
        "processed": 0,
        "already_processed": 0,
        "waiting_for_invoice": 0,
        "failed": 0,
    }

    with pytest.raises(CommandError):
        call_command("reconcile_apipay_invoices", "--once")

    assert heartbeat.call_args_list == [
        call("/tmp/apipay-monitor-heartbeat", "running"),
        call("/tmp/apipay-monitor-heartbeat", "error"),
    ]


def test_monitor_budget_and_backoff_helpers_enforce_hard_caps():
    assert (
        _request_budget_per_iteration(
            requests_per_minute=10_000,
            interval_seconds=15,
        )
        == 25
    )
    assert (
        _request_budget_per_iteration(
            requests_per_minute=80,
            interval_seconds=30,
        )
        == 40
    )
    assert (
        _request_budget_per_iteration(
            requests_per_minute=100,
            interval_seconds=3_600,
        )
        == 50
    )

    assert (
        _backoff_delay(
            interval_seconds=30,
            max_backoff_seconds=300,
            failure_streak=0,
        )
        == 30
    )
    assert (
        _backoff_delay(
            interval_seconds=30,
            max_backoff_seconds=300,
            failure_streak=2,
        )
        == 60
    )
    assert (
        _backoff_delay(
            interval_seconds=30,
            max_backoff_seconds=300,
            failure_streak=10,
        )
        == 300
    )
