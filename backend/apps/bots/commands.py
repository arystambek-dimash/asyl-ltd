"""Команды Telegram-бота: /start (/help) и /report — ответ сразу, в журнал не пишутся.

Доступ — по username в настройках бота (:meth:`TelegramBotSettings.allows`):
недопущенному бот объясняет, кого попросить. /report — отчёт о вагонах за
сегодня, вчера или неделю в формате владельца (:func:`apps.bots.wagon_report.period_report`).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.utils import timezone

from apps.common.telegram import split_text

from .models import TelegramBotSettings
from .providers.telegram import IncomingMessage
from .wagon_report import period_report

START = frozenset({"start", "help"})
REPORT = "report"
# /report <период> → (сколько дней назад начало, сколько назад конец).
_PERIODS = {
    "": (0, 0), "сегодня": (0, 0), "today": (0, 0),
    "вчера": (1, 1), "yesterday": (1, 1),
    "неделя": (6, 0), "week": (6, 0),
}
_PERIOD_LABELS = {(0, 0): "сегодня", (1, 1): "вчера", (6, 0): "7 дней"}


@dataclass(frozen=True)
class Command:
    name: str
    argument: str


def parse_command(text: str, *, bot_username: str = "") -> Command | None:
    """«/report@asyl_bot вчера» → Command("report", "вчера"); не команда или
    команда другому боту в группе — ``None``."""
    text = text.strip()
    if not text.startswith("/"):
        return None
    head, _, rest = text[1:].replace("\n", " ").partition(" ")
    name, _, target = head.partition("@")
    if target and bot_username and target.lower() != bot_username.lower():
        return None
    return Command(name.lower(), " ".join(rest.split()).lower()) if name else None


HELP = (
    "Пришлите отчёт об отгрузке вагонов — бот проведёт его в CRM и ответит, что получилось.\n\n"
    "/report — отчёт о вагонах за сегодня\n"
    "/report вчера — за вчера\n"
    "/report неделя — за 7 дней"
)


def _no_access(incoming: IncomingMessage) -> str:
    if not incoming.sender_username:
        return "Задайте username в настройках Telegram — по нему администратор CRM даёт доступ к боту."
    return (
        f"У @{incoming.sender_username} нет доступа к боту. Попросите администратора CRM добавить "
        f"@{incoming.sender_username} в «Telegram-бот → Настройки»."
    )


def _start(incoming: IncomingMessage, bot_settings: TelegramBotSettings, allowed: bool) -> str:
    name = incoming.sender_name or "здравствуйте"
    lines = [f"{name}, вы допущены к боту." if allowed else _no_access(incoming)]
    recipient = bot_settings.report_recipient_username
    if incoming.is_private and recipient and incoming.sender_username == recipient:
        lines.append("Сюда будут приходить отчёты о вагонах из CRM («Отправить отчёт» у грузчика).")
    if allowed:
        lines.append(HELP)
    return "\n\n".join(lines)


def _report(argument: str) -> list[str]:
    period = _PERIODS.get(argument)
    if period is None:
        return ["Не понял период. Напишите /report, /report вчера или /report неделя."]
    today = timezone.localdate()
    date_from, date_to = today - timedelta(days=period[0]), today - timedelta(days=period[1])
    report = period_report(date_from, date_to)
    if not report.text:
        return [f"Отгрузок вагонов за {_PERIOD_LABELS[period]} нет."]
    return split_text(report.text)


def command_reply(command: Command, incoming: IncomingMessage, bot_settings: TelegramBotSettings) -> list[str]:
    """Ответ на команду — сообщения по порядку (длинный отчёт — частями)."""
    allowed = bot_settings.allows(incoming.sender_username)
    if command.name in START:
        return [_start(incoming, bot_settings, allowed)]
    if not allowed:
        return [_no_access(incoming)]
    if command.name == REPORT:
        return _report(command.argument)
    return [f"Не знаю такой команды.\n\n{HELP}"]
