"""Celery entry points for payment reconciliation only."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from uuid import uuid4

from celery import shared_task
from django.core.cache import cache, caches
from django.db import InterfaceError, OperationalError

from .reconciliation_runner import (
    ApiPayReconciliationOptions,
    _backoff_delay,
    _write_heartbeat,
    run_apipay_reconciliation_iteration,
)

log = logging.getLogger(__name__)

APIPAY_RECONCILIATION_TASK = "orders.reconcile_apipay"
APIPAY_RECONCILIATION_LOCK_KEY = "orders:apipay:reconciliation:singleton"

_COMPARE_AND_EXPIRE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('expire', KEYS[1], tonumber(ARGV[2]))
end
return 0
"""
_COMPARE_AND_DELETE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""


class RetryableApiPayIterationError(RuntimeError):
    """A completed, safe-to-repeat iteration reported partial failures."""


def _task_owner(task) -> str:
    # Celery retries retain their task id, which deliberately retains ownership
    # of the singleton lease across the exponential-backoff chain.
    request_id = getattr(task.request, "id", None)
    return str(request_id or f"direct-{os.getpid()}-{uuid4().hex}")


def _redis_compare_owned_lease(
    owner: str,
    *,
    timeout: int | None = None,
    delete: bool = False,
) -> bool | None:
    """Atomically mutate an owned lease on Django's production Redis cache.

    ``None`` means the configured cache is not Django's Redis backend. Local
    tests may then use the process-local fallback below; production Compose
    always configures Redis.
    """
    backend = caches["default"]
    adapter = getattr(backend, "_cache", None)
    get_client = getattr(adapter, "get_client", None)
    serializer = getattr(adapter, "_serializer", None)
    if not callable(get_client) or serializer is None:
        return None

    key = backend.make_and_validate_key(APIPAY_RECONCILIATION_LOCK_KEY)
    client = get_client(key, write=True)
    encoded_owner = serializer.dumps(owner)
    if delete:
        return bool(client.eval(_COMPARE_AND_DELETE, 1, key, encoded_owner))
    if timeout is None:
        raise ValueError("timeout is required when refreshing a lease")
    return bool(
        client.eval(
            _COMPARE_AND_EXPIRE,
            1,
            key,
            encoded_owner,
            max(1, int(timeout)),
        )
    )


def _refresh_owned_lease(owner: str, timeout: int) -> bool:
    refreshed = _redis_compare_owned_lease(owner, timeout=timeout)
    if refreshed is not None:
        return refreshed
    # The fallback is only for non-Redis local/test caches. Production uses the
    # Lua compare-and-expire operation above, so it cannot refresh a new owner.
    if cache.get(APIPAY_RECONCILIATION_LOCK_KEY) != owner:
        return False
    cache.set(APIPAY_RECONCILIATION_LOCK_KEY, owner, timeout=timeout)
    return True


def _claim_lease(owner: str, timeout: int) -> bool:
    if cache.add(APIPAY_RECONCILIATION_LOCK_KEY, owner, timeout=timeout):
        return True
    # Celery retries keep their task id, so the same owner may atomically renew
    # its lease. A stale worker can never overwrite a newly acquired owner.
    return _refresh_owned_lease(owner, timeout)


def _retain_lease(owner: str, timeout: int) -> bool:
    return _refresh_owned_lease(owner, timeout)


def _release_lease(owner: str) -> None:
    released = _redis_compare_owned_lease(owner, delete=True)
    if released is not None:
        return
    # Non-Redis local/test fallback; production release is an atomic Lua CAS.
    if cache.get(APIPAY_RECONCILIATION_LOCK_KEY) == owner:
        cache.delete(APIPAY_RECONCILIATION_LOCK_KEY)


def _seed_worker_heartbeat_if_missing(path: str) -> None:
    """Prove replacement-worker liveness without hiding a current failure."""
    if Path(path).exists():
        # In particular, preserve an ``error`` heartbeat written by the failed
        # iteration whose retry owns this lease.
        return
    try:
        _write_heartbeat(path, "running")
    except OSError:
        log.exception("Could not write skipped ApiPay task heartbeat")


def _retry_iteration(task, options, owner: str, exc: Exception):
    failure_streak = int(getattr(task.request, "retries", 0)) + 1
    countdown = _backoff_delay(
        interval_seconds=options.interval_seconds,
        max_backoff_seconds=options.max_backoff_seconds,
        failure_streak=failure_streak,
    )
    # Scheduled beat messages cannot bypass the retry backoff: the retry keeps
    # the same task id/owner while fresh periodic task ids skip this lease.
    retained = _retain_lease(
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
        _release_lease(owner)
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

    _release_lease(owner)
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
