"""Manual/legacy runner for the shared ApiPay reconciliation iteration."""

import logging
import time

from django.core.management.base import BaseCommand, CommandError

from apps.common.heartbeat import write_heartbeat
from apps.orders.reconciliation import reconcile_apipay_invoices
from apps.orders.reconciliation_runner import (
    ApiPayReconciliationOptions,
    backoff_delay,
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
        # Умолчания — те же переменные окружения и ограничения, что у задачи
        # Celery: ApiPayReconciliationOptions.from_environment().
        defaults = ApiPayReconciliationOptions.from_environment()
        parser.add_argument(
            "--once",
            action="store_true",
            help="Run exactly one reconciliation iteration",
        )
        parser.add_argument(
            "--interval",
            type=int,
            default=defaults.interval_seconds,
            help="Seconds between reconciliation starts",
        )
        parser.add_argument(
            "--stale-seconds",
            type=int,
            default=defaults.stale_seconds,
            help="Minimum age of the last local observation before polling",
        )
        parser.add_argument(
            "--lookback-hours",
            type=int,
            default=defaults.lookback_hours,
            help="How far back to reconsider non-final and late-paid invoices",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=defaults.batch_size,
            help="Maximum invoice IDs per provider request (1-500)",
        )
        parser.add_argument(
            "--refund-limit",
            type=int,
            default=defaults.refund_limit,
            help="Maximum provider refund snapshots per iteration (1-500)",
        )
        parser.add_argument(
            "--refund-orphan-grace-seconds",
            type=int,
            default=defaults.refund_orphan_grace_seconds,
            help=(
                "Minimum age before a complete empty provider snapshot can "
                "release an ambiguous local refund reservation"
            ),
        )
        parser.add_argument(
            "--refund-sweep-stale-seconds",
            type=int,
            default=defaults.refund_sweep_stale_seconds,
            help=(
                "Minimum age before rechecking a paid invoice for refunds "
                "created directly at the provider"
            ),
        )
        parser.add_argument(
            "--request-budget-per-minute",
            type=int,
            default=defaults.requests_per_minute,
            help=(
                "Shared monitor request budget per minute (10-100; ApiPay's "
                "remaining documented capacity is reserved for live traffic)"
            ),
        )
        parser.add_argument(
            "--max-backoff-seconds",
            type=int,
            default=defaults.max_backoff_seconds,
            help="Maximum retry delay after failed reconciliation iterations",
        )
        parser.set_defaults(heartbeat_file=defaults.heartbeat_file)

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
            heartbeat_file=options["heartbeat_file"],
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
                    heartbeat_writer=write_heartbeat,
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
            delay = backoff_delay(
                interval_seconds=config.interval_seconds,
                max_backoff_seconds=config.max_backoff_seconds,
                failure_streak=failure_streak,
            )
            time.sleep(max(1, delay - elapsed))
