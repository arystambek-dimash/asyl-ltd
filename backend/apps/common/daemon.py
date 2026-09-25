"""Общий цикл фоновых процессов (management-команд, живущих в своём контейнере).

Каждый круг начинается и заканчивается ``close_old_connections``: после
рестарта PostgreSQL разорванное соединение закрывается и следующий круг
подключается заново, а не падает на нём вечно. Статус круга пишется в
heartbeat (:mod:`apps.common.heartbeat`) для healthcheck контейнера.
SIGTERM/SIGINT дожидаются конца текущего круга.
"""

from __future__ import annotations

import logging
import signal
import threading
import time
from collections.abc import Callable
from typing import Any

from django.db import InterfaceError, OperationalError, close_old_connections

from apps.common.heartbeat import write_heartbeat

log = logging.getLogger(__name__)

RUNNING = "running"
DEGRADED = "degraded"
# Сбой базы или сети повторяется на следующем круге; ошибки программы
# уходят наверх, чтобы Docker перезапустил сломанный процесс.
DEPENDENCY_ERRORS: tuple[type[BaseException], ...] = (
    OSError,
    TimeoutError,
    OperationalError,
    InterfaceError,
)


def every(interval: float) -> Callable[[str, float], float]:
    """Пауза до начала следующего круга: круги стартуют раз в ``interval`` секунд."""

    def pause(_status: str, started: float) -> float:
        return max(0.0, interval - (time.monotonic() - started))

    return pause


def run_supervised_loop(
    tick: Callable[[], str],
    *,
    once: bool,
    heartbeat_file: str,
    label: str,
    pause: Callable[[str, float], float],
    initial_status: str = RUNNING,
    retry_errors: tuple[type[BaseException], ...] = DEPENDENCY_ERRORS,
) -> None:
    """Крутить ``tick`` до SIGTERM/SIGINT (или один раз при ``once``).

    ``tick`` возвращает статус круга для heartbeat. ``retry_errors`` —
    сбои, после которых круг помечается degraded и повторяется (при
    ``once`` — уходят наверх). ``pause(status, started)`` — сколько ждать
    после круга, начатого в ``started`` (``time.monotonic``).
    """
    stopped = threading.Event()

    def request_stop(_signum, _frame):
        # Текущий круг доводится до конца: прерывать его посреди записи нельзя.
        stopped.set()

    previous_handlers: dict[int, Any] = {}
    if not once and threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous_handlers[signum] = signal.signal(signum, request_stop)

    try:
        # Живость процесса — до первого круга: healthcheck не ждёт камер,
        # весов или провайдера, а зависший после этого круг устареет.
        write_heartbeat(heartbeat_file, initial_status)
        last_status = initial_status
        while not stopped.is_set():
            started = time.monotonic()
            status = DEGRADED
            close_old_connections()
            try:
                status = tick()
            except retry_errors:
                log.exception("%s iteration failed", label)
                status = DEGRADED
                if once:
                    raise
            finally:
                close_old_connections()
                write_heartbeat(heartbeat_file, status)

            if status != last_status:
                log.info("%s status=%s", label, status)
                last_status = status
            if once:
                return
            stopped.wait(pause(status, started))
    finally:
        close_old_connections()
        for restore_signum, handler in previous_handlers.items():
            signal.signal(restore_signum, handler)
