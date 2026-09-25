"""Celery entry points for payment reconciliation only."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from uuid import uuid4

from celery import shared_task
from django.db import InterfaceError, OperationalError

from apps.common.heartbeat import write_heartbeat
from apps.common.locks import (
    claim_owned_lease,
    refresh_owned_lease,
    release_owned_lease,
)

from .reconciliation_runner import (
    ApiPayReconciliationOptions,
    backoff_delay,
    run_apipay_reconciliation_iteration,
)

log = logging.getLogger(__name__)

APIPAY_RECONCILIATION_TASK = "orders.reconcile_apipay"
APIPAY_RECONCILIATION_LOCK_KEY = "orders:apipay:reconciliation:singleton"


class RetryableApiPayIterationError(RuntimeError):
    """A completed, safe-to-repeat iteration reported partial failures."""


def _task_owner(task) -> str:
    # Celery retries retain their task id, which deliberately retains ownership
    # of the singleton lease across the exponential-backoff chain.
    request_id = getattr(task.request, "id", None)
    return str(request_id or f"direct-{os.getpid()}-{uuid4().hex}")


def _claim_lease(owner: str, timeout: int) -> bool:
    if claim_owned_lease(APIPAY_RECONCILIATION_LOCK_KEY, owner, timeout):
        return True
    # Celery retries keep their task id, so the same owner may atomically renew
    # its lease. A stale worker can never overwrite a newly acquired owner.
    return refresh_owned_lease(APIPAY_RECONCILIATION_LOCK_KEY, owner, timeout)


def _seed_worker_heartbeat_if_missing(path: str) -> None:
    """Prove replacement-worker liveness without hiding a current failure."""
    if Path(path).exists():
        # In particular, preserve an ``error`` heartbeat written by the failed
        # iteration whose retry owns this lease.
        return
    try:
        write_heartbeat(path, "running")
    except OSError:
        log.exception("Could not write skipped ApiPay task heartbeat")


def _retry_iteration(task, options, owner: str, exc: Exception):
    failure_streak = int(getattr(task.request, "retries", 0)) + 1
    countdown = backoff_delay(
        interval_seconds=options.interval_seconds,
        max_backoff_seconds=options.max_backoff_seconds,
        failure_streak=failure_streak,
    )
    # Scheduled beat messages cannot bypass the retry backoff: the retry keeps
    # the same task id/owner while fresh periodic task ids skip this lease.
    retained = refresh_owned_lease(
        APIPAY_RECONCILIATION_LOCK_KEY,
        owner,
        timeout=max(options.task_lock_seconds, countdown + 60),
    )
    if not retained:
        log.warning(
            "ApiPay reconciliation lease changed owner before retry scheduling"
        )
    log.warning(
        "Retrying ApiPay reconciliation in %ss after failure streak %s",
        countdown,
        failure_streak,
    )
    raise task.retry(
        exc=exc,
        countdown=countdown,
        # Beat messages expire before the next tick, but an intentional retry
        # must remain live until its later ETA.
        expires=countdown + options.interval_seconds,
    )


def _run_reconciliation_task(task) -> None:
    options = ApiPayReconciliationOptions.from_environment()
    owner = _task_owner(task)
    if not _claim_lease(owner, options.task_lock_seconds):
        log.info("Skipping overlapping ApiPay reconciliation task")
        # A replacement worker can inherit a valid lease from a hard-killed
        # late-acked task. Fresh beat messages still prove this worker/queue is
        # alive while the old lease expires or the broker redelivers the task.
        _seed_worker_heartbeat_if_missing(options.heartbeat_file)
        return

    try:
        result = run_apipay_reconciliation_iteration(options)
    except (OperationalError, InterfaceError) as exc:
        # Database connectivity failures are explicitly retryable. Arbitrary
        # programming/configuration exceptions are left to the next periodic
        # run instead of being hidden in an infinite autoretry loop.
        _retry_iteration(task, options, owner, exc)
    except Exception:
        release_owned_lease(APIPAY_RECONCILIATION_LOCK_KEY, owner)
        raise

    if result.retryable_failures:
        log.warning(result.summary())
        _retry_iteration(
            task,
            options,
            owner,
            RetryableApiPayIterationError(
                f"{result.retryable_failures} reconciliation operation(s) failed"
            ),
        )

    release_owned_lease(APIPAY_RECONCILIATION_LOCK_KEY, owner)
    log.info(result.summary())


@shared_task(
    bind=True,
    name=APIPAY_RECONCILIATION_TASK,
    ignore_result=True,
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=None,
)
def reconcile_apipay_task(self) -> None:
    """Run one bounded reconciliation iteration on the payments queue.

    This task never creates a provider refund. It only invokes the shared
    webhook/invoice/refund reconciliation runner, whose writes are idempotent.
    """

    _run_reconciliation_task(self)
