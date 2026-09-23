"""Круг процесса WhatsApp-бота (``manage.py run_whatsapp_bot``).

Один круг: состояние номера (раз в минуту) → одно уведомление из очереди
провайдера (долгий опрос) → запись сообщения в базу → подтверждение приёма
→ разбор и проведение ожидающих сообщений → ответы цитатой. Статус круга
пишется в heartbeat для healthcheck контейнера и (не чаще раза в полминуты,
если ничего не поменялось) в настройки бота — для журнала.

``WHATSAPP_BOT_ENABLED`` ≠ 1 — процесс простаивает и к провайдеру не ходит.
Выключатель в журнале («Бот проводит отчёты») останавливает приём: очередь
провайдера копит сообщения сутки.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from .models import WhatsAppBotSettings
from .providers.green_api import GreenApiClient, GreenApiError, incoming_message, state_change
from .service_user import ensure_bot_user
from .whatsapp import ingest, process_pending, send_pending_replies

log = logging.getLogger(__name__)

DISABLED = "disabled"
RUNNING = "running"
DEGRADED = "degraded"
# Номер, который может принимать и отправлять сообщения.
AUTHORIZED = "authorized"
STATE_REFRESH_SECONDS = 60
RECORD_EVERY_SECONDS = 30


class BotRunner:
    def __init__(self, client_factory: Callable[[], GreenApiClient] = GreenApiClient.from_settings,
                 clock: Callable[[], float] = time.monotonic):
        self._client_factory = client_factory
        self._clock = clock
        self._client: GreenApiClient | None = None
        self._user_id: int | None = None
        self._state_checked_at: float | None = None
        self._recorded: tuple[str, str, float] | None = None

    def _user(self):
        if self._user_id is None:
            self._user_id = ensure_bot_user().pk
        # Свежий экземпляр: права кэшируются на пользователе.
        return get_user_model().objects.select_related("employee").get(pk=self._user_id)

    def _record(self, bot_settings: WhatsAppBotSettings, status: str, error: str = "") -> None:
        now = self._clock()
        if self._recorded is not None:
            last_status, last_error, at = self._recorded
            if (last_status, last_error) == (status, error) and now - at < RECORD_EVERY_SECONDS:
                return
        WhatsAppBotSettings.objects.filter(pk=bot_settings.pk).update(
            runtime_status=status, runtime_error=error[:300], polled_at=timezone.now())
        self._recorded = (status, error, now)

    def _set_state(self, bot_settings: WhatsAppBotSettings, state: str) -> None:
        bot_settings.instance_state = state[:40]
        WhatsAppBotSettings.objects.filter(pk=bot_settings.pk).update(
            instance_state=bot_settings.instance_state, instance_state_at=timezone.now())

    def _refresh_state(self, client: GreenApiClient, bot_settings: WhatsAppBotSettings) -> None:
        now = self._clock()
        if self._state_checked_at is not None and now - self._state_checked_at < STATE_REFRESH_SECONDS:
            return
        self._state_checked_at = now
        self._set_state(bot_settings, client.get_state_instance())

    def _handle(self, body: dict, bot_settings: WhatsAppBotSettings) -> None:
        state = state_change(body)
        if state:
            self._set_state(bot_settings, state)
        message = incoming_message(body)
        if message is not None:
            ingest(message, bot_settings)

    def poll_once(self) -> str:
        """Один круг; статус для heartbeat: disabled, running или degraded.

        Сбой провайдера — degraded (круг повторится); ошибки базы — наверх,
        их обрабатывает команда, как у остальных мониторов.
        """
        if not settings.WHATSAPP_BOT_ENABLED:
            return DISABLED
        bot_settings = WhatsAppBotSettings.load()
        try:
            if self._client is None:
                self._client = self._client_factory()
            client = self._client
            self._refresh_state(client, bot_settings)
            if not bot_settings.enabled:
                self._record(bot_settings, DISABLED)
                return DISABLED
            notification = client.receive_notification()
            if notification is not None:
                # Сначала в базу, потом подтверждение: сбой между ними — повтор
                # доставки, который гасит уникальный идентификатор сообщения.
                self._handle(notification.body, bot_settings)
                client.delete_notification(notification.receipt_id)
            process_pending(user=self._user(), bot_settings=bot_settings)
            send_pending_replies(client)
        except GreenApiError as exc:
            log.warning("WhatsApp bot provider failure: %s", exc)
            self._record(bot_settings, DEGRADED, str(exc))
            return DEGRADED
        if bot_settings.instance_state and bot_settings.instance_state != AUTHORIZED:
            error = f"Номер не готов в Green-API: {bot_settings.instance_state}"
            self._record(bot_settings, DEGRADED, error)
            return DEGRADED
        self._record(bot_settings, RUNNING)
        return RUNNING
