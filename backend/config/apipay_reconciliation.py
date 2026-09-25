"""Интервал, backoff и lease сверки ApiPay — без импортов Django и моделей.

Settings берёт отсюда расписание beat и visibility_timeout брокера, а
ApiPayReconciliationOptions — lease задачи. Эти значения обязаны совпадать:
задача, упавшая без ack, становится видна брокеру не позже, чем истекает её
lease, поэтому формула живёт в одном месте.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

log = logging.getLogger(__name__)

MIN_INTERVAL_SECONDS = 15
DEFAULT_INTERVAL_SECONDS = 30
DEFAULT_MAX_BACKOFF_SECONDS = 300
DEFAULT_TASK_LOCK_SECONDS = 1_200


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        log.warning("Invalid integer in %s; using %s", name, default)
        return default


@dataclass(frozen=True)
class ReconcileTiming:
    interval_seconds: int
    max_backoff_seconds: int
    task_lock_seconds: int


def reconcile_timing(
    *,
    interval_seconds: int,
    max_backoff_seconds: int,
    task_lock_seconds: int = DEFAULT_TASK_LOCK_SECONDS,
) -> ReconcileTiming:
    interval = max(MIN_INTERVAL_SECONDS, int(interval_seconds))
    max_backoff = max(interval, int(max_backoff_seconds))
    # The singleton lease spans the longest retry countdown plus enough
    # time for the next bounded iteration. The dedicated worker remains the
    # primary serialization boundary; this lease also guards accidental
    # duplicate workers and beat messages during a retry chain.
    minimum_lock_seconds = max_backoff + interval + 60
    return ReconcileTiming(
        interval_seconds=interval,
        max_backoff_seconds=max_backoff,
        task_lock_seconds=max(minimum_lock_seconds, int(task_lock_seconds)),
    )


def reconcile_timing_from_env() -> ReconcileTiming:
    return reconcile_timing(
        interval_seconds=env_int(
            "APIPAY_RECONCILE_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS
        ),
        max_backoff_seconds=env_int(
            "APIPAY_MONITOR_MAX_BACKOFF_SECONDS", DEFAULT_MAX_BACKOFF_SECONDS
        ),
        task_lock_seconds=env_int(
            "APIPAY_RECONCILE_TASK_LOCK_SECONDS", DEFAULT_TASK_LOCK_SECONDS
        ),
    )
