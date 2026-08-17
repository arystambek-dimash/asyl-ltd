"""Manual/legacy runner for the shared ApiPay reconciliation iteration."""

import logging
import os
import time

from django.core.management.base import BaseCommand, CommandError

from apps.orders.reconciliation import reconcile_apipay_invoices
from apps.orders.reconciliation_runner import (
    DEFAULT_HEARTBEAT_FILE,
    ApiPayReconciliationOptions,
    _backoff_delay,
    _env_int,
    _write_heartbeat,
    run_apipay_reconciliation_iteration,
)
from apps.orders.refund_reconciliation import reconcile_apipay_refunds
from apps.orders.webhooks import replay_pending_apipay_webhooks

log = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Reconcile ApiPay invoices once, or run the legacy continuous loop "
        "for manual fallback"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Run exactly one reconciliation iteration",
        )
        parser.add_argument(
            "--interval",
            type=int,
            default=_env_int("APIPAY_RECONCILE_INTERVAL_SECONDS", 30),
            help="Seconds between reconciliation starts",
        )
        parser.add_argument(
            "--stale-seconds",
            type=int,
            default=_env_int("APIPAY_RECONCILE_STALE_SECONDS", 30),
            help="Minimum age of the last local observation before polling",
        )
        parser.add_argument(
            "--lookback-hours",
            type=int,
            default=_env_int("APIPAY_RECONCILE_LOOKBACK_HOURS", 72),
            help="How far back to reconsider non-final and late-paid invoices",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=_env_int("APIPAY_RECONCILE_BATCH_SIZE", 100),
            help="Maximum invoice IDs per provider request (1-500)",
        )
        parser.add_argument(
            "--refund-limit",
            type=int,
            default=_env_int("APIPAY_REFUND_RECONCILE_LIMIT", 25),
            help="Maximum provider refund snapshots per iteration (1-500)",
        )
        parser.add_argument(
            "--refund-orphan-grace-seconds",
            type=int,
            default=_env_int(
                "APIPAY_REFUND_ORPHAN_GRACE_SECONDS", 15 * 60
            ),
            help=(
                "Minimum age before a complete empty provider snapshot can "
                "release an ambiguous local refund reservation"
            ),
        )
        parser.add_argument(
            "--refund-sweep-stale-seconds",
            type=int,
            default=_env_int(
                "APIPAY_REFUND_SWEEP_STALE_SECONDS", 15 * 60
            ),
            help=(
                "Minimum age before rechecking a paid invoice for refunds "
                "created directly at the provider"
            ),
        )
        parser.add_argument(
            "--request-budget-per-minute",
            type=int,
            default=_env_int(
                "APIPAY_MONITOR_REQUEST_BUDGET_PER_MINUTE", 80
            ),
            help=(
                "Shared monitor request budget per minute (10-100; ApiPay's "
                "remaining documented capacity is reserved for live traffic)"
            ),
        )
        parser.add_argument(
            "--max-backoff-seconds",
            type=int,
            default=_env_int("APIPAY_MONITOR_MAX_BACKOFF_SECONDS", 300),
            help="Maximum retry delay after failed reconciliation iterations",
        )

    def handle(self, *args, **options):
        config = ApiPayReconciliationOptions.build(
            interval_seconds=options["interval"],
            stale_seconds=options["stale_seconds"],
            lookback_hours=options["lookback_hours"],
            batch_size=options["batch_size"],
            refund_limit=options["refund_limit"],
            refund_orphan_grace_seconds=options[
                "refund_orphan_grace_seconds"
            ],
            refund_sweep_stale_seconds=options[
                "refund_sweep_stale_seconds"
            ],
            requests_per_minute=options["request_budget_per_minute"],
            max_backoff_seconds=options["max_backoff_seconds"],
            heartbeat_file=os.environ.get(
                "APIPAY_MONITOR_HEARTBEAT_FILE",
                DEFAULT_HEARTBEAT_FILE,
            ),
        )
        failure_streak = 0

        while True:
            started = time.monotonic()
            iteration_failed = False
            try:
                result = run_apipay_reconciliation_iteration(
                    config,
                    invoice_reconciler=reconcile_apipay_invoices,
                    refund_reconciler=reconcile_apipay_refunds,
                    webhook_replayer=replay_pending_apipay_webhooks,
                    heartbeat_writer=_write_heartbeat,
                )
                self.stdout.write(result.summary())
                if result.retryable_failures:
                    iteration_failed = True
                    failure_streak += 1
                    log.warning(
                        "ApiPay reconciliation iteration reported %s "
                        "failure(s); next retry uses backoff",
                        result.retryable_failures,
                    )
                else:
                    failure_streak = 0
            except Exception:
                # Preserve the legacy operator fallback: an unexpected failure
                # is retried with bounded backoff in loop mode and propagated by
                # --once for automation or direct diagnosis.
                iteration_failed = True
                failure_streak += 1
                log.exception("ApiPay reconciliation iteration failed")
                if options["once"]:
                    raise

            if options["once"]:
                if iteration_failed:
                    raise CommandError(
                        "ApiPay reconciliation iteration reported failures"
                    )
                return

            elapsed = time.monotonic() - started
            delay = _backoff_delay(
                interval_seconds=config.interval_seconds,
                max_backoff_seconds=config.max_backoff_seconds,
                failure_streak=failure_streak,
            )
            time.sleep(max(1, delay - elapsed))
