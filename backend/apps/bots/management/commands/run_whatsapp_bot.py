"""Процесс WhatsApp-бота отчётов о вагонах: опрос Green-API и проведение отчётов."""

from __future__ import annotations

import logging
import signal
import threading
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import InterfaceError, OperationalError, close_old_connections

from apps.bots.runner import DEGRADED, DISABLED, RUNNING, BotRunner
from apps.common.heartbeat import write_heartbeat

log = logging.getLogger(__name__)

# Пауза между кругами, когда долгий опрос не идёт: выключен — проверяем
# выключатель раз в 10 с, сбой провайдера — не чаще раза в 30 с.
IDLE_SECONDS = {DISABLED: 10.0, DEGRADED: 30.0}


class Command(BaseCommand):
    help = "WhatsApp-бот: забирает отчёты о вагонах из Green-API и проводит их"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Один круг")

    def handle(self, *args, **options):
        once = bool(options["once"])
        stopped = threading.Event()

        def request_stop(_signum, _frame):
            stopped.set()

        previous_handlers: dict[int, Any] = {}
        if not once and threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGTERM, signal.SIGINT):
                previous_handlers[signum] = signal.signal(signum, request_stop)

        heartbeat = settings.WHATSAPP_BOT_HEARTBEAT_FILE
        # Живость процесса — до первого сетевого круга: healthcheck не ждёт провайдера.
        write_heartbeat(heartbeat, RUNNING if settings.WHATSAPP_BOT_ENABLED else DISABLED)
        runner = BotRunner()
        last_status = ""
        try:
            while not stopped.is_set():
                close_old_connections()
                status = DEGRADED
                try:
                    status = runner.poll_once()
                except (OSError, TimeoutError, OperationalError, InterfaceError):
                    # Сбой базы или сети повторяется; ошибки программы — наверх,
                    # чтобы Docker перезапустил сломанный процесс.
                    log.exception("WhatsApp bot dependency failed")
                    if once:
                        raise
                finally:
                    close_old_connections()
                    write_heartbeat(heartbeat, status)
                if status != last_status:
                    log.info("WhatsApp bot status=%s", status)
                    last_status = status
                if once:
                    return
                # Работающий бот ждёт в долгом опросе провайдера — без паузы.
                if status != RUNNING:
                    stopped.wait(IDLE_SECONDS[status])
        finally:
            close_old_connections()
            for restore_signum, handler in previous_handlers.items():
                signal.signal(restore_signum, handler)
