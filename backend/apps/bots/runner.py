"""Круг процесса Telegram-бота (``manage.py run_telegram_bot``).

Один круг: токен бота (getMe, раз в минуту) → обновления из Telegram
(долгий опрос) → запись сообщений в базу или ответ на команду → разбор и
проведение ожидающих сообщений → ответы в чат → отчёты о вагонах из
истории грузчика («Отправить отчёт», :mod:`apps.bots.wagon_report`).
Обновления подтверждаются следующим опросом (``offset``) — только после
записи в базу. Статус круга пишется в heartbeat для healthcheck контейнера
и (не чаще раза в полминуты, если ничего не поменялось) в настройки бота —
для журнала.

``TELEGRAM_BOT_ENABLED`` ≠ 1 — процесс простаивает и в Telegram не ходит.
Выключатель в журнале («Бот проводит отчёты») останавливает приём: Telegram
копит обновления сутки, а отчёты в очереди ждут включения.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.common.telegram import (
    TelegramClient,
    TelegramError,
    TelegramUnauthorized,
    Update,
    message_ref,
)

from .commands import command_reply, parse_command
from .messages import ingest, process_pending, remember_chat, send_pending_replies
from .models import BOT_AUTHORIZED, RUNTIME_RUNNING, TelegramBotSettings
from .providers.telegram import bot_client, incoming_message
from .service_user import ensure_bot_user
from .wagon_report import send_pending_reports

log = logging.getLogger(__name__)

DISABLED = "disabled"
RUNNING = RUNTIME_RUNNING
DEGRADED = "degraded"
AUTHORIZED = BOT_AUTHORIZED
UNAUTHORIZED = "unauthorized"
UNAUTHORIZED_ERROR = "Telegram отверг токен бота — проверьте TELEGRAM_BOT_TOKEN"
STATE_REFRESH_SECONDS = 60
RECORD_EVERY_SECONDS = 30


class BotRunner:
    def __init__(self, client_factory: Callable[[], TelegramClient] = bot_client,
                 clock: Callable[[], float] = time.monotonic):
        self._client_factory = client_factory
        self._clock = clock
        self._client: TelegramClient | None = None
        self._user_id: int | None = None
        self._state_checked_at: float | None = None
        self._recorded: tuple[str, str, float] | None = None
        # Следующее обновление: всё до него записано и подтверждается следующим опросом.
        self._offset: int | None = None

    def _user(self):
        if self._user_id is None:
            self._user_id = ensure_bot_user().pk
        # Свежий экземпляр: права кэшируются на пользователе.
        return get_user_model().objects.select_related("employee").get(pk=self._user_id)

    def _record(self, bot_settings: TelegramBotSettings, status: str, error: str = "") -> None:
        now = self._clock()
        if self._recorded is not None:
            last_status, last_error, at = self._recorded
            if (last_status, last_error) == (status, error) and now - at < RECORD_EVERY_SECONDS:
                return
        TelegramBotSettings.objects.filter(pk=bot_settings.pk).update(
            runtime_status=status, runtime_error=error[:300], polled_at=timezone.now())
        self._recorded = (status, error, now)

    def _set_state(self, bot_settings: TelegramBotSettings, state: str, username: str = "") -> None:
        bot_settings.bot_state = state
        fields = {"bot_state": state, "bot_state_at": timezone.now()}
        if username:
            bot_settings.bot_username = fields["bot_username"] = username[:64]
        TelegramBotSettings.objects.filter(pk=bot_settings.pk).update(**fields)

    def _refresh_state(self, client: TelegramClient, bot_settings: TelegramBotSettings) -> None:
        now = self._clock()
        if self._state_checked_at is not None and now - self._state_checked_at < STATE_REFRESH_SECONDS:
            return
        self._state_checked_at = now
        try:
            username = client.get_me()
        except TelegramUnauthorized:
            self._set_state(bot_settings, UNAUTHORIZED)
            raise
        self._set_state(bot_settings, AUTHORIZED, username)

    def _handle(self, update: Update, client: TelegramClient, bot_settings: TelegramBotSettings) -> None:
        incoming = incoming_message(update.body)
        if incoming is None:
            return
        command = parse_command(incoming.text, bot_username=bot_settings.bot_username)
        if command is None:
            ingest(incoming)
            return
        remember_chat(incoming)
        # Ответ на команду — сразу. Отказ Telegram (бот заблокирован, чат
        # закрыт) не держит очередь: команду можно прислать ещё раз.
        reply_to = message_ref(incoming.chat_id, incoming.message_id)
        for part in command_reply(command, incoming, bot_settings):
            try:
                client.send_message(incoming.chat_id, part, reply_to=reply_to)
            except TelegramError as exc:
                log.warning("Telegram bot command reply failed: %s", exc)
                return
            reply_to = ""

    def poll_once(self) -> str:
        """Один круг; статус для heartbeat: disabled, running или degraded.

        Сбой Telegram — degraded (круг повторится); ошибки базы — наверх,
        их обрабатывает команда, как у остальных мониторов.
        """
        if not settings.TELEGRAM_BOT_ENABLED:
            return DISABLED
        bot_settings = TelegramBotSettings.load()
        try:
            if self._client is None:
                self._client = self._client_factory()
            client = self._client
            self._refresh_state(client, bot_settings)
            if not bot_settings.enabled:
                self._record(bot_settings, DISABLED)
                return DISABLED
            for update in client.get_updates(self._offset):
                # Сначала в базу, потом подтверждение (следующим опросом): сбой
                # между ними — повтор доставки, который гасит идентификатор сообщения.
                self._handle(update, client, bot_settings)
                self._offset = update.update_id + 1
            process_pending(user=self._user(), bot_settings=bot_settings)
            send_pending_replies(client)
            send_pending_reports(client)
        except TelegramUnauthorized:
            log.warning("Telegram bot token rejected")
            self._record(bot_settings, DEGRADED, UNAUTHORIZED_ERROR)
            return DEGRADED
        except TelegramError as exc:
            log.warning("Telegram bot provider failure: %s", exc)
            self._record(bot_settings, DEGRADED, str(exc))
            return DEGRADED
        self._record(bot_settings, RUNNING)
        return RUNNING
