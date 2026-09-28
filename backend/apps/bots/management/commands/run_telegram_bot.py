"""Процесс Telegram-бота отчётов о вагонах: опрос Telegram и проведение отчётов."""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.bots.runner import DEGRADED, DISABLED, RUNNING, BotRunner
from apps.common.daemon import run_supervised_loop

# Пауза между кругами, когда долгий опрос не идёт: выключен — проверяем
# выключатель раз в 10 с, сбой Telegram — не чаще раза в 30 с.
IDLE_SECONDS = {DISABLED: 10.0, DEGRADED: 30.0}


def _pause(status: str, _started: float) -> float:
    # Работающий бот ждёт в долгом опросе Telegram — без паузы.
    return 0.0 if status == RUNNING else IDLE_SECONDS[status]


class Command(BaseCommand):
    help = "Telegram-бот: забирает отчёты о вагонах из Telegram и проводит их"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Один круг")

    def handle(self, *args, **options):
        runner = BotRunner()
        run_supervised_loop(
            runner.poll_once,
            once=bool(options["once"]),
            heartbeat_file=settings.TELEGRAM_BOT_HEARTBEAT_FILE,
            label="Telegram bot",
            pause=_pause,
            initial_status=RUNNING if settings.TELEGRAM_BOT_ENABLED else DISABLED,
        )
