"""Разбор обновлений Telegram для бота отчётов о вагонах.

Бот не держит публичного вебхука: обновления забираются долгим опросом
``getUpdates`` (:class:`apps.common.telegram.TelegramClient`), а
подтверждаются следующим запросом со сдвигом ``offset`` — только после того,
как сообщение записано в базу. Неподтверждённые обновления Telegram хранит
сутки, поэтому перезагрузка сервера ничего не теряет, а повторная доставка
того же обновления гасится уникальным идентификатором сообщения.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from django.conf import settings

from apps.common.telegram import TelegramClient, as_dict, as_id, as_text

from ..models import BotMessage

PRIVATE = "private"


def bot_client() -> TelegramClient:
    """Клиент бота отчётов: токен и долгий опрос — из настроек сервиса бота."""
    return TelegramClient(
        token=settings.TELEGRAM_BOT_TOKEN,
        token_name="TELEGRAM_BOT_TOKEN",
        api_url=settings.TELEGRAM_BOT_API_URL,
        poll_timeout=settings.TELEGRAM_BOT_POLL_TIMEOUT_SECONDS,
    )


@dataclass(frozen=True)
class IncomingMessage:
    """Входящее текстовое сообщение или его правка."""

    message_id: str
    chat_id: str
    chat_name: str
    # private, group, supergroup: личный чат с ботом или группа.
    chat_type: str
    sender_id: str
    sender_name: str
    # Username отправителя без «@» в нижнем регистре; пусто — не задан в Telegram.
    sender_username: str
    kind: str  # BotMessage.MESSAGE / EDITED
    text: str
    sent_at: datetime | None
    # Правка: время правки — у каждой правки своя запись.
    edited_at: datetime | None = None

    @property
    def is_private(self) -> bool:
        return self.chat_type == PRIVATE


def _moment(value) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def normalize_username(value: str) -> str:
    """«@Dinara_K» и «https://t.me/dinara_k» → «dinara_k»: username сравнивается без регистра."""
    value = str(value).strip().lower()
    for prefix in ("https://t.me/", "http://t.me/", "t.me/", "@"):
        if value.startswith(prefix):
            value = value[len(prefix):]
    return value


def _full_name(person: dict) -> str:
    return " ".join(part for part in (as_text(person.get("first_name")), as_text(person.get("last_name"))) if part)


def incoming_message(body) -> IncomingMessage | None:
    """Текстовое сообщение (или его правка) из обновления; остальное — фото без
    подписи, стикеры, служебные сообщения, сообщения ботов и каналов — ``None``."""
    body = as_dict(body)
    kind, message = BotMessage.MESSAGE, body.get("message")
    if message is None and "edited_message" in body:
        kind, message = BotMessage.EDITED, body.get("edited_message")
    message = as_dict(message)
    chat, sender = as_dict(message.get("chat")), as_dict(message.get("from"))
    message_id, chat_id, sender_id = as_id(message.get("message_id")), as_id(chat.get("id")), as_id(
        sender.get("id"))
    if not message_id or not chat_id or not sender_id or sender.get("is_bot"):
        return None
    # Текст как есть (с переносами строк отчёта) — без strip.
    raw = message.get("text")
    if not isinstance(raw, str):
        raw = message.get("caption")
    if not isinstance(raw, str):
        return None
    chat_type = as_text(chat.get("type"))
    sender_name = _full_name(sender)
    return IncomingMessage(
        message_id=message_id,
        chat_id=chat_id,
        chat_name=as_text(chat.get("title")) or _full_name(chat) or sender_name,
        chat_type=chat_type,
        sender_id=sender_id,
        sender_name=sender_name,
        sender_username=normalize_username(as_text(sender.get("username"))),
        kind=kind,
        text=raw,
        sent_at=_moment(message.get("date")),
        edited_at=_moment(message.get("edit_date")) if kind == BotMessage.EDITED else None,
    )


