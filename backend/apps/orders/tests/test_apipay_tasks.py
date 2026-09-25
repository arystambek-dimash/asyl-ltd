import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from celery.exceptions import Retry
from django.core.cache import cache
from django.db import OperationalError

from apps.common.heartbeat import write_heartbeat
from apps.orders.reconciliation import ReconciliationStats
from apps.orders.reconciliation_runner import (
    ApiPayReconciliationOptions,
    ApiPayReconciliationResult,
)
from apps.orders.refund_reconciliation import RefundReconciliationStats
from apps.orders.tasks import (
    APIPAY_RECONCILIATION_LOCK_KEY,
    _run_reconciliation_task,
    _seed_worker_heartbeat_if_missing,
    reconcile_apipay_task,
)


@pytest.fixture(autouse=True)
def clear_reconciliation_lease():
    cache.delete(APIPAY_RECONCILIATION_LOCK_KEY)
    yield
    cache.delete(APIPAY_RECONCILIATION_LOCK_KEY)


def _options() -> ApiPayReconciliationOptions:
    return ApiPayReconciliationOptions.build(
        interval_seconds=30,
        stale_seconds=30,
        lookback_hours=72,
        batch_size=100,
        refund_limit=25,
        refund_orphan_grace_seconds=900,
        refund_sweep_stale_seconds=900,
        requests_per_minute=80,
        max_backoff_seconds=300,
        heartbeat_file="/tmp/test-apipay-heartbeat",
    )


def _result(
    options: ApiPayReconciliationOptions,
    *,
    failures: int = 0,
) -> ApiPayReconciliationResult:
    return ApiPayReconciliationResult(
        options=options,
        invoices=ReconciliationStats(failed=failures),
        refunds=RefundReconciliationStats(),
        inbox={
            "processed": 0,
            "already_processed": 0,
            "waiting_for_invoice": 0,
            "failed": 0,
        },
    )


@contextmanager
def _reconciliation(options: ApiPayReconciliationOptions, **iteration):
    """Задача берёт ``options`` из окружения; итерацию сверки подменяет mock с ``iteration``."""
    with (
        patch(
            "apps.orders.tasks.ApiPayReconciliationOptions.from_environment",
            return_value=options,
        ),
        patch(
            "apps.orders.tasks.run_apipay_reconciliation_iteration",
            **iteration,
        ) as run,
    ):
        yield run


def _fake_task(*, retries: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        request=SimpleNamespace(id="task-123", retries=retries),
        retry=Mock(side_effect=Retry("retry requested")),
    )


def test_task_contract_is_late_acked_and_resultless() -> None:
    assert reconcile_apipay_task.ignore_result is True
    assert reconcile_apipay_task.acks_late is True
    assert reconcile_apipay_task.reject_on_worker_lost is True
    assert reconcile_apipay_task.max_retries is None


def test_reported_iteration_failure_retries_with_exponential_backoff() -> None:
    options = _options()
    task = _fake_task(retries=1)

    with _reconciliation(options, return_value=_result(options, failures=1)) as run:
        with pytest.raises(Retry):
            _run_reconciliation_task(task)

    run.assert_called_once_with(options)
    task.retry.assert_called_once()
    retry_kwargs = task.retry.call_args.kwargs
    assert retry_kwargs["countdown"] == 60
    assert retry_kwargs["expires"] == 90
    # Ретрай сохраняет аренду за тем же task id.
    assert cache.get(APIPAY_RECONCILIATION_LOCK_KEY) == "task-123"


def test_database_connectivity_failure_is_explicitly_retryable() -> None:
    options = _options()
    task = _fake_task()
    error = OperationalError("database unavailable")

    with _reconciliation(options, side_effect=error):
        with pytest.raises(Retry):
            _run_reconciliation_task(task)

    assert task.retry.call_args.kwargs["exc"] is error
    assert task.retry.call_args.kwargs["countdown"] == 30


def test_arbitrary_failure_is_not_hidden_by_celery_autoretry() -> None:
    options = _options()
    task = _fake_task()

    with _reconciliation(options, side_effect=ValueError("unsafe to retry blindly")):
        with pytest.raises(ValueError, match="unsafe to retry blindly"):
            _run_reconciliation_task(task)

    task.retry.assert_not_called()
    assert cache.get(APIPAY_RECONCILIATION_LOCK_KEY) is None


def test_fresh_periodic_task_skips_an_existing_retry_chain_lease() -> None:
    options = _options()
    task = _fake_task()

    with (
        _reconciliation(options) as run,
        patch("apps.orders.tasks._seed_worker_heartbeat_if_missing") as seed_heartbeat,
    ):
        cache.set(APIPAY_RECONCILIATION_LOCK_KEY, "older-task", 60)
        _run_reconciliation_task(task)

    run.assert_not_called()
    task.retry.assert_not_called()
    seed_heartbeat.assert_called_once_with(options.heartbeat_file)
    assert cache.get(APIPAY_RECONCILIATION_LOCK_KEY) == "older-task"


def test_skipped_task_seeds_only_a_missing_heartbeat(tmp_path) -> None:
    heartbeat = tmp_path / "apipay-heartbeat"

    _seed_worker_heartbeat_if_missing(str(heartbeat))

    assert json.loads(heartbeat.read_text(encoding="utf-8"))["status"] == "running"
    write_heartbeat(str(heartbeat), "error", now=123.0)

    _seed_worker_heartbeat_if_missing(str(heartbeat))

    assert json.loads(heartbeat.read_text(encoding="utf-8")) == {
        "status": "error",
        "updated_at": 123.0,
    }
