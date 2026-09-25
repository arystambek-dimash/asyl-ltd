"""One safe ApiPay reconciliation iteration shared by CLI and Celery.

The provider-facing services remain the authority for invoice, webhook, and
refund idempotency.  This module only allocates the existing request budget,
runs those services once, and maintains the monitor heartbeat.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta

from apps.common.heartbeat import write_heartbeat
from config.apipay_reconciliation import (
    DEFAULT_TASK_LOCK_SECONDS,
    env_int,
    reconcile_timing,
    reconcile_timing_from_env,
)

from .reconciliation import ReconciliationStats, reconcile_apipay_invoices
from .refund_reconciliation import (
    RefundReconciliationStats,
    reconcile_apipay_refunds,
)
from .webhooks import replay_pending_apipay_webhooks

log = logging.getLogger(__name__)

MIN_MONITOR_REQUESTS_PER_MINUTE = 10
# ApiPay documents 200 requests/minute. Reconciliation may consume at most
# half, leaving a deterministic 100 requests/minute for live operations.
MAX_MONITOR_REQUESTS_PER_MINUTE = 100
MAX_MONITOR_REQUESTS_PER_ITERATION = 50
DEFAULT_HEARTBEAT_FILE = "/tmp/apipay-monitor-heartbeat"

InboxStats = dict[str, int]
# Активных QR-возвратов единицы, а окно после подтверждения покупателем короткое.
# Каждая сессия — один GET из бюджета итерации (выбор покупки и execute бывают
# у сессии один раз).
QR_REFUND_RECONCILE_LIMIT = 5
InvoiceReconciler = Callable[..., ReconciliationStats]
RefundReconciler = Callable[..., RefundReconciliationStats]
WebhookReplayer = Callable[[], InboxStats]
QrRefundReconciler = Callable[..., dict[str, int]]
HeartbeatWriter = Callable[[str, str], None]


def backoff_delay(
    *,
    interval_seconds: int,
    max_backoff_seconds: int,
    failure_streak: int,
) -> int:
    if failure_streak <= 0:
        return interval_seconds
    multiplier = 2 ** min(failure_streak - 1, 4)
    return min(max_backoff_seconds, interval_seconds * multiplier)


@dataclass(frozen=True)
class ApiPayReconciliationOptions:
    interval_seconds: int
    stale_seconds: int
    lookback_hours: int
    batch_size: int
    refund_limit: int
    refund_orphan_grace_seconds: int
    refund_sweep_stale_seconds: int
    requests_per_minute: int
    max_backoff_seconds: int
    heartbeat_file: str
    task_lock_seconds: int

    @classmethod
    def build(
        cls,
        *,
        interval_seconds: int,
        stale_seconds: int,
        lookback_hours: int,
        batch_size: int,
        refund_limit: int,
        refund_orphan_grace_seconds: int,
        refund_sweep_stale_seconds: int,
        requests_per_minute: int,
        max_backoff_seconds: int,
        heartbeat_file: str,
        task_lock_seconds: int = DEFAULT_TASK_LOCK_SECONDS,
    ) -> ApiPayReconciliationOptions:
        timing = reconcile_timing(
            interval_seconds=interval_seconds,
            max_backoff_seconds=max_backoff_seconds,
            task_lock_seconds=task_lock_seconds,
        )
        return cls(
            interval_seconds=timing.interval_seconds,
            stale_seconds=max(0, int(stale_seconds)),
            lookback_hours=max(1, int(lookback_hours)),
            batch_size=max(1, min(int(batch_size), 500)),
            refund_limit=max(1, min(int(refund_limit), 500)),
            refund_orphan_grace_seconds=max(
                0, int(refund_orphan_grace_seconds)
            ),
            refund_sweep_stale_seconds=max(
                0, int(refund_sweep_stale_seconds)
            ),
            requests_per_minute=max(
                MIN_MONITOR_REQUESTS_PER_MINUTE,
                min(
                    int(requests_per_minute),
                    MAX_MONITOR_REQUESTS_PER_MINUTE,
                ),
            ),
            max_backoff_seconds=timing.max_backoff_seconds,
            heartbeat_file=heartbeat_file.strip() or DEFAULT_HEARTBEAT_FILE,
            task_lock_seconds=timing.task_lock_seconds,
        )

    @classmethod
    def from_environment(cls) -> ApiPayReconciliationOptions:
        timing = reconcile_timing_from_env()
        return cls.build(
            interval_seconds=timing.interval_seconds,
            stale_seconds=env_int("APIPAY_RECONCILE_STALE_SECONDS", 30),
            lookback_hours=env_int("APIPAY_RECONCILE_LOOKBACK_HOURS", 72),
            batch_size=env_int("APIPAY_RECONCILE_BATCH_SIZE", 100),
            refund_limit=env_int("APIPAY_REFUND_RECONCILE_LIMIT", 25),
            refund_orphan_grace_seconds=env_int(
                "APIPAY_REFUND_ORPHAN_GRACE_SECONDS", 15 * 60
            ),
            refund_sweep_stale_seconds=env_int(
                "APIPAY_REFUND_SWEEP_STALE_SECONDS", 15 * 60
            ),
            requests_per_minute=env_int(
                "APIPAY_MONITOR_REQUEST_BUDGET_PER_MINUTE", 80
            ),
            max_backoff_seconds=timing.max_backoff_seconds,
            heartbeat_file=os.environ.get(
                "APIPAY_MONITOR_HEARTBEAT_FILE",
                DEFAULT_HEARTBEAT_FILE,
            ),
            task_lock_seconds=timing.task_lock_seconds,
        )

    @property
    def request_budget(self) -> int:
        # ``build`` already clamped the rate and the interval. One
        # invoice-status batch, one refund snapshot and one QR refund session
        # are reserved. The burst cap prevents a backlog plus a long interval
        # from becoming a traffic spike.
        proportional_budget = self.requests_per_minute * self.interval_seconds // 60
        return max(3, min(proportional_budget, MAX_MONITOR_REQUESTS_PER_ITERATION))

    @property
    def qr_refund_request_budget(self) -> int:
        return min(QR_REFUND_RECONCILE_LIMIT, self.request_budget - 2)

    @property
    def refund_request_budget(self) -> int:
        return min(
            self.refund_limit,
            self.request_budget - 1 - self.qr_refund_request_budget,
        )

    @property
    def invoice_request_budget(self) -> int:
        return (
            self.request_budget
            - self.refund_request_budget
            - self.qr_refund_request_budget
        )


@dataclass(frozen=True)
class ApiPayReconciliationResult:
    options: ApiPayReconciliationOptions
    invoices: ReconciliationStats
    refunds: RefundReconciliationStats
    inbox: InboxStats
    qr_refunds: dict[str, int] = field(default_factory=dict)

    @property
    def retryable_failures(self) -> int:
        # Each reported failure occurred within replay or a reconciliation
        # operation that is safe to repeat. Refund *creation* is deliberately
        # absent from this runner; refund reconciliation only fetches provider
        # snapshots and idempotently applies them.
        return (
            self.invoices.failed
            + self.refunds.failed
            + int(self.inbox.get("failed", 0))
            + int(self.qr_refunds.get("failed", 0))
        )

    def summary(self) -> str:
        return (
            "apipay-reconcile "
            f"issue_selected={self.invoices.issue_selected} "
            f"issue_recovered={self.invoices.issue_recovered} "
            f"issue_released={self.invoices.issue_released} "
            f"issue_failed={self.invoices.issue_failed} "
            f"selected={self.invoices.selected} "
            f"batches={self.invoices.batches} "
            f"fetched={self.invoices.fetched} "
            f"changed={self.invoices.changed} "
            f"unchanged={self.invoices.unchanged} "
            f"missing={self.invoices.missing} "
            f"unexpected={self.invoices.unexpected} "
            f"failed={self.invoices.failed} "
            f"unconfigured={self.invoices.unconfigured} "
            f"inbox_processed={self.inbox.get('processed', 0)} "
            f"inbox_waiting={self.inbox.get('waiting_for_invoice', 0)} "
            f"inbox_failed={self.inbox.get('failed', 0)} "
            f"inbox_rejected={self.inbox.get('rejected', 0)} "
            f"refund_selected={self.refunds.selected} "
            f"refund_fetched={self.refunds.fetched} "
            f"refund_changed={self.refunds.changed} "
            f"refund_unchanged={self.refunds.unchanged} "
            f"refund_released={self.refunds.released_orphans} "
            f"refund_ambiguous={self.refunds.ambiguous} "
            f"refund_incomplete={self.refunds.incomplete} "
            f"refund_failed={self.refunds.failed} "
            f"qr_refund_selected={self.qr_refunds.get('selected', 0)} "
            f"qr_refund_failed={self.qr_refunds.get('failed', 0)} "
            f"request_budget={self.options.request_budget} "
            f"invoice_request_budget={self.options.invoice_request_budget} "
            f"refund_request_budget={self.options.refund_request_budget} "
            f"qr_refund_request_budget={self.options.qr_refund_request_budget}"
        )


def run_apipay_reconciliation_iteration(
    options: ApiPayReconciliationOptions,
    *,
    invoice_reconciler: InvoiceReconciler = reconcile_apipay_invoices,
    refund_reconciler: RefundReconciler = reconcile_apipay_refunds,
    webhook_replayer: WebhookReplayer = replay_pending_apipay_webhooks,
    heartbeat_writer: HeartbeatWriter = write_heartbeat,
    qr_refund_reconciler: QrRefundReconciler | None = None,
) -> ApiPayReconciliationResult:
    """Run one bounded, heartbeat-observed reconciliation iteration."""

    try:
        heartbeat_writer(options.heartbeat_file, "running")
        inbox = webhook_replayer()
        invoices = invoice_reconciler(
            batch_size=options.batch_size,
            request_budget=options.invoice_request_budget,
            stale_after=timedelta(seconds=options.stale_seconds),
            lookback=timedelta(hours=options.lookback_hours),
        )
        refunds = refund_reconciler(
            limit=options.refund_request_budget,
            orphan_grace=timedelta(
                seconds=options.refund_orphan_grace_seconds
            ),
            sweep_stale_after=timedelta(
                seconds=options.refund_sweep_stale_seconds
            ),
        )
        if qr_refund_reconciler is None:
            from .qr_refunds import reconcile_qr_refunds as qr_refund_reconciler
        qr_refunds = qr_refund_reconciler(
            limit=options.qr_refund_request_budget
        )
        result = ApiPayReconciliationResult(
            options=options,
            invoices=invoices,
            refunds=refunds,
            inbox=inbox,
            qr_refunds=qr_refunds,
        )
        heartbeat_writer(
            options.heartbeat_file,
            "error" if result.retryable_failures else "ok",
        )
        return result
    except Exception:
        try:
            heartbeat_writer(options.heartbeat_file, "error")
        except Exception:
            log.exception("Could not write ApiPay reconciliation heartbeat")
        raise
