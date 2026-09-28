"""Telegram без сети: сообщения чата и записывающий клиент для тестов бота."""
from datetime import UTC, datetime

from django.utils import timezone

from apps.bots.models import BotMessage
from apps.bots.providers.telegram import IncomingMessage
from apps.common.telegram import (
    TelegramError,
    TelegramUnauthorized,
    Update,
    message_ref,
)

# Группа «Отгрузка вагонов» (супергруппа) и Джин-Син в ней.
GROUP = "-1001234567890"
JIN_ID = "501"
JIN = "jin_sin"
# Получатель отчётов о вагонах (настройки бота): username и её личный чат с ботом.
DINARA = "dinara_k"
DINARA_CHAT = "777"
BOT_USERNAME = "asyl_test_bot"
SENT_AT = datetime(2026, 9, 19, 9, 30, tzinfo=UTC)


def incoming(text="", *, message_id="11", chat_id=GROUP, chat_type="supergroup", sender_id=JIN_ID,
             sender_username=JIN, kind=BotMessage.MESSAGE, sent_at=SENT_AT, edited_at=None):
    return IncomingMessage(
        message_id=message_id, chat_id=chat_id, chat_name="Отгрузка вагонов", chat_type=chat_type,
        sender_id=sender_id, sender_name="Джин-Син", sender_username=sender_username, kind=kind, text=text,
        sent_at=sent_at, edited_at=edited_at,
    )


def edited(text, *, message_id="11", sender_username=JIN, edited_at=None):
    return incoming(
        text, message_id=message_id, kind=BotMessage.EDITED, sender_username=sender_username,
        edited_at=edited_at or datetime(2026, 9, 19, 9, 35, tzinfo=UTC))


def private(text="", *, username=DINARA, chat_id=DINARA_CHAT, message_id="5"):
    """Личное сообщение боту (чат = пользователь)."""
    return incoming(text, message_id=message_id, chat_id=chat_id, chat_type="private", sender_id=chat_id,
                    sender_username=username)


def bot_alive(row, *, polled_at=None):
    """Процесс бота жив — как после удачного круга: «running», токен рабочий, свежий опрос."""
    row.runtime_status, row.bot_state = "running", "authorized"
    row.polled_at = polled_at or timezone.now()
    return row


def update(text, *, update_id=1, message_id=11, chat_id=int(GROUP), chat_type="supergroup",
           username=JIN, sender_id=int(JIN_ID), edited_at=None, **extra):
    """Обновление Telegram: сообщение Джин-Сина в группе отгрузки (или его правка)."""
    message = {
        "message_id": message_id,
        "from": {"id": sender_id, "is_bot": False, "first_name": "Джин-Син", **({"username": username} if username else {})},
        "chat": {"id": chat_id, "type": chat_type, "title": "Отгрузка вагонов"} if chat_type != "private" else {
            "id": chat_id, "type": "private", "first_name": "Джин-Син"},
        "date": int(SENT_AT.timestamp()),
        "text": text,
        **extra,
    }
    if edited_at is not None:
        return {"update_id": update_id, "edited_message": {**message, "edit_date": int(edited_at.timestamp())}}
    return {"update_id": update_id, "message": message}


class FakeTelegram:
    """Очередь обновлений и журнал вызовов вместо Telegram.

    ``get_updates(offset)`` подтверждает всё, что до ``offset``, как Telegram.
    ``fail_send`` — True (сбой Telegram) или исключение, которым падает отправка.
    """

    def __init__(self, *bodies, fail_send=False, fail_receive=False, unauthorized=False):
        self.queue = [Update(body["update_id"], body) for body in bodies]
        self.fail_send = fail_send
        self.fail_receive = fail_receive
        self.unauthorized = unauthorized
        self.offsets = []
        self.sent = []
        self.me_calls = 0

    def get_me(self):
        self.me_calls += 1
        if self.unauthorized:
            raise TelegramUnauthorized("Telegram getMe: HTTP 401 — Unauthorized")
        return BOT_USERNAME

    def get_updates(self, offset):
        if self.fail_receive:
            raise TelegramError("Telegram getUpdates: нет связи (URLError)")
        self.offsets.append(offset)
        if offset is not None:
            self.queue = [item for item in self.queue if item.update_id >= offset]
        return list(self.queue)

    def send_message(self, chat_id, text, *, reply_to=""):
        if isinstance(self.fail_send, Exception):
            raise self.fail_send
        if self.fail_send:
            raise TelegramError("Telegram sendMessage: HTTP 400 — Bad Request: chat not found")
        self.sent.append((chat_id, text, reply_to))
        return message_ref(chat_id, str(1000 + len(self.sent)))
