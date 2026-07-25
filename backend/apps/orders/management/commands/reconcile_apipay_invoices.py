import logging
import os
import time
from datetime import timedelta
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.orders.reconciliation import reconcile_apipay_invoices
from apps.orders.refund_reconciliation import reconcile_apipay_refunds
from apps.orders.webhooks import replay_pending_apipay_webhooks

log = logging.getLogger(__name__)

MIN_INTERVAL_SECONDS = 15
MIN_MONITOR_REQUESTS_PER_MINUTE = 10
# ApiPay documents 200 requests/minute. The monitor may consume at most half,
# preserving a deterministic 100 requests/minute for live create/cancel/refund
# traffic even if an operator supplies an unsafe CLI/environment value.
MAX_MONITOR_REQUESTS_PER_MINUTE = 100
MAX_MONITOR_REQUESTS_PER_ITERATION = 50
DEFAULT_HEARTBEAT_FILE = "/tmp/apipay-monitor-heartbeat"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        log.warning("Invalid integer in %s; using %s", name, default)
        return default


def _request_budget_per_iteration(
    *,
    requests_per_minute: int,
    interval_seconds: int,
) -> int:
    requests_per_minute = max(
        MIN_MONITOR_REQUESTS_PER_MINUTE,
        min(int(requests_per_minute), MAX_MONITOR_REQUESTS_PER_MINUTE),
    )
    interval_seconds = max(MIN_INTERVAL_SECONDS, int(interval_seconds))
    proportional_budget = requests_per_minute * interval_seconds // 60
    # One invoice-status batch and one refund snapshot are reserved. The burst
    # cap avoids a large backlog turning a long interval into a traffic spike.
    return max(
        2,
        min(proportional_budget, MAX_MONITOR_REQUESTS_PER_ITERATION),
    )


def _backoff_delay(
    *,
    interval_seconds: int,
    max_backoff_seconds: int,
    failure_streak: int,
) -> int:
    if failure_streak <= 0:
        return interval_seconds
    multiplier = 2 ** min(failure_streak - 1, 4)
    return min(max_backoff_seconds, interval_seconds * multiplier)


def _write_heartbeat(path: str, state: str) -> None:
    heartbeat = Path(path)
    heartbeat.parent.mkdir(parents=True, exist_ok=True)
    temporary = heartbeat.with_name(f".{heartbeat.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            f"{state} {time.time():.6f}\n",
            encoding="utf-8",
        )
        os.replace(temporary, heartbeat)
    finally:
        temporary.unlink(missing_ok=True)


class Command(BaseCommand):
    help = "Continuously reconcile recent ApiPay invoices with provider state"

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
            default=_env_int("APIPAY_REFUND_ORPHAN_GRACE_SECONDS", 15 * 60),
            help=(
                "Minimum age before a complete empty provider snapshot can "
                "release an ambiguous local refund reservation"
            ),
        )
        parser.add_argument(
            "--refund-sweep-stale-seconds",
            type=int,
            default=_env_int("APIPAY_REFUND_SWEEP_STALE_SECONDS", 15 * 60),
            help=(
                "Minimum age before rechecking a paid invoice for refunds "
                "created directly at the provider"
            ),
        )
        parser.add_argument(
            "--request-budget-per-minute",
            type=int,
            default=_env_int(
                "APIPAY_MONITOR_REQUEST_BUDGET_PER_MINUTE",
                80,
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
        interval = max(MIN_INTERVAL_SECONDS, options["interval"])
        stale_seconds = max(0, options["stale_seconds"])
        lookback_hours = max(1, options["lookback_hours"])
        batch_size = max(1, min(options["batch_size"], 500))
        refund_limit = max(1, min(options["refund_limit"], 500))
        requests_per_minute = max(
            MIN_MONITOR_REQUESTS_PER_MINUTE,
            min(
                options["request_budget_per_minute"],
                MAX_MONITOR_REQUESTS_PER_MINUTE,
            ),
        )
        iteration_request_budget = _request_budget_per_iteration(
            requests_per_minute=requests_per_minute,
            interval_seconds=interval,
        )
        refund_request_budget = min(
            refund_limit,
            iteration_request_budget - 1,
        )
        invoice_request_budget = iteration_request_budget - refund_request_budget
        invoice_limit = invoice_request_budget * batch_size
        max_backoff = max(interval, options["max_backoff_seconds"])
        refund_orphan_grace = timedelta(
            seconds=max(0, options["refund_orphan_grace_seconds"])
        )
        refund_sweep_stale = timedelta(
            seconds=max(0, options["refund_sweep_stale_seconds"])
        )
        heartbeat_file = os.environ.get(
            "APIPAY_MONITOR_HEARTBEAT_FILE",
            DEFAULT_HEARTBEAT_FILE,
        )
        failure_streak = 0

        while True:
            started = time.monotonic()
            iteration_failed = False
            try:
                _write_heartbeat(heartbeat_file, "running")
                inbox = replay_pending_apipay_webhooks()
                stats = reconcile_apipay_invoices(
                    batch_size=batch_size,
                    limit=invoice_limit,
                    request_budget=invoice_request_budget,
                    stale_after=timedelta(seconds=stale_seconds),
                    lookback=timedelta(hours=lookback_hours),
                )
                refund_stats = reconcile_apipay_refunds(
                    limit=refund_request_budget,
                    orphan_grace=refund_orphan_grace,
                    sweep_stale_after=refund_sweep_stale,
                )
                self.stdout.write(
                    "apipay-reconcile "
                    f"issue_selected={stats.issue_selected} "
                    f"issue_recovered={stats.issue_recovered} "
                    f"issue_released={stats.issue_released} "
                    f"issue_failed={stats.issue_failed} "
                    f"selected={stats.selected} batches={stats.batches} "
                    f"fetched={stats.fetched} changed={stats.changed} "
                    f"unchanged={stats.unchanged} missing={stats.missing} "
                    f"unexpected={stats.unexpected} failed={stats.failed} "
                    f"inbox_processed={inbox['processed']} "
                    f"inbox_waiting={inbox['waiting_for_invoice']} "
                    f"inbox_failed={inbox['failed']} "
                    f"refund_selected={refund_stats.selected} "
                    f"refund_fetched={refund_stats.fetched} "
                    f"refund_changed={refund_stats.changed} "
                    f"refund_unchanged={refund_stats.unchanged} "
                    f"refund_released={refund_stats.released_orphans} "
                    f"refund_ambiguous={refund_stats.ambiguous} "
                    f"refund_incomplete={refund_stats.incomplete} "
                    f"refund_failed={refund_stats.failed} "
                    f"request_budget={iteration_request_budget} "
                    f"invoice_request_budget={invoice_request_budget} "
                    f"refund_request_budget={refund_request_budget}"
                )
                provider_failures = stats.failed + refund_stats.failed + inbox["failed"]
                if provider_failures:
                    iteration_failed = True
                    failure_streak += 1
                    _write_heartbeat(heartbeat_file, "error")
                    log.warning(
                        "ApiPay reconciliation iteration reported %s "
                        "failure(s); next retry uses backoff",
                        provider_failures,
                    )
                else:
                    failure_streak = 0
                    _write_heartbeat(heartbeat_file, "ok")
            except Exception:
                # A transient database/configuration failure must not kill the
                # long-running monitor. The heartbeat marks it unhealthy while
                # the next exponentially-backed-off iteration retries.
                iteration_failed = True
                failure_streak += 1
                log.exception("ApiPay reconciliation iteration failed")
                try:
                    _write_heartbeat(heartbeat_file, "error")
                except Exception:
                    log.exception("Could not write ApiPay monitor heartbeat")
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
                interval_seconds=interval,
                max_backoff_seconds=max_backoff,
                failure_streak=failure_streak,
            )
            time.sleep(max(1, delay - elapsed))
