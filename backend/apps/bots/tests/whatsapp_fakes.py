"""Green-API без сети: сообщения чата и записывающий клиент для тестов бота."""
from datetime import UTC, datetime

from django.utils import timezone

from apps.bots.providers.green_api import DELETED, EDITED, MESSAGE, GreenApiError, IncomingMessage, Notification

GROUP = "120363043968066561@g.us"
JIN = "998901112233@c.us"
SENT_AT = datetime(2026, 9, 19, 9, 30, tzinfo=UTC)


def incoming(text="", *, message_id="MSG1", chat_id=GROUP, sender_id=JIN, kind=MESSAGE, target_id="",
             sent_at=SENT_AT):
    return IncomingMessage(
        message_id=message_id, chat_id=chat_id, chat_name="Отгрузка вагонов", sender_id=sender_id,
        sender_name="Джин-Син", kind=kind, text=text, target_id=target_id, sent_at=sent_at,
    )


def edited(text, *, target_id="MSG1", message_id="EDIT1", sender_id=JIN):
    return incoming(text, message_id=message_id, kind=EDITED, target_id=target_id, sender_id=sender_id)


def deleted(*, target_id="MSG1", message_id="DEL1"):
    return incoming(message_id=message_id, kind=DELETED, target_id=target_id)


def bot_alive(row, *, polled_at=None):
    """Процесс бота жив — как после удачного круга: «running», номер authorized, свежий опрос."""
    row.runtime_status, row.instance_state = "running", "authorized"
    row.polled_at = polled_at or timezone.now()
    return row


def webhook_body(message: IncomingMessage) -> dict:
    """Тело уведомления incomingMessageReceived с текстом сообщения."""
    return {
        "typeWebhook": "incomingMessageReceived",
        "timestamp": int(message.sent_at.timestamp()),
        "idMessage": message.message_id,
        "senderData": {"chatId": message.chat_id, "chatName": message.chat_name, "sender": message.sender_id,
                       "senderName": message.sender_name},
        "messageData": {"typeMessage": "textMessage", "textMessageData": {"textMessage": message.text}},
    }


class FakeGreenApi:
    """Очередь уведомлений и журнал вызовов вместо провайдера.

    ``fail_send`` — True (сбой провайдера) или исключение, которым падает отправка.
    """

    def __init__(self, *bodies, state="authorized", fail_send=False, fail_receive=False):
        self.queue = [Notification(receipt, body) for receipt, body in enumerate(bodies, start=1)]
        self.state = state
        self.fail_send = fail_send
        self.fail_receive = fail_receive
        self.deleted = []
        self.sent = []
        self.state_calls = 0

    def receive_notification(self):
        if self.fail_receive:
            raise GreenApiError("Green-API receiveNotification: нет связи (URLError)")
        return self.queue[0] if self.queue else None

    def delete_notification(self, receipt_id):
        self.deleted.append(receipt_id)
        self.queue = [item for item in self.queue if item.receipt_id != receipt_id]

    def send_message(self, chat_id, message, *, quoted_message_id=""):
        if isinstance(self.fail_send, Exception):
            raise self.fail_send
        if self.fail_send:
            raise GreenApiError("Green-API sendMessage: HTTP 500")
        self.sent.append((chat_id, message, quoted_message_id))
        return f"REPLY{len(self.sent)}"

    def get_state_instance(self):
        self.state_calls += 1
        return self.state
