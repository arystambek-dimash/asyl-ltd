"""Green-API (WhatsApp) в режиме опроса — всё сетевое общение бота здесь.

Бот не держит публичного вебхука: уведомления забираются долгим опросом
``receiveNotification`` и подтверждаются ``deleteNotification`` только после
того, как сообщение записано в базу. Очередь у провайдера хранит уведомления
сутки, поэтому перезагрузка сервера ничего не теряет, а повторная доставка
того же уведомления гасится уникальным идентификатором сообщения.

Токен инстанса стоит в пути запроса — он не попадает ни в тексты ошибок, ни
в журнал: ошибки называют только метод API и код ответа.

Сбой, после которого запрос мог выполниться (нет ответа, 5xx, непонятный
ответ), — :class:`GreenApiOutcomeUnknown`: отправку сообщения по нему не
повторяют сами, иначе получатель увидит его дважды.
"""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from django.conf import settings

from ..models import BotMessage

# Тип сообщения → (контейнер данных, поле текста). Ответ цитатой приходит
# как quotedMessage с тем же контейнером, что и расширенный текст.
_TEXT_TYPES = {
    "textMessage": ("textMessageData", "textMessage"),
    "extendedTextMessage": ("extendedTextMessageData", "text"),
    "quotedMessage": ("extendedTextMessageData", "text"),
}
_MAX_RESPONSE_BYTES = 1024 * 1024
# Сверх долгого опроса — на сеть и ответ провайдера.
_NETWORK_MARGIN_SECONDS = 10
_REQUEST_TIMEOUT_SECONDS = 15


class GreenApiError(RuntimeError):
    """Провайдер недоступен или ответил не так, как описано в документации."""


class GreenApiOutcomeUnknown(GreenApiError):
    """Запрос мог выполниться: ответа нет (тайм-аут, обрыв), 5xx или ответ непонятный.

    Остальные сбои — отказ провайдера (4xx) или запрос, который не ушёл
    (соединение отклонено, адрес не найден), — точно ничего не отправили.
    """


def _never_sent(reason) -> bool:
    """Запрос не ушёл: соединение отклонено или адрес провайдера не найден."""
    return isinstance(reason, (ConnectionRefusedError, socket.gaierror))


@dataclass(frozen=True)
class Notification:
    receipt_id: int
    body: dict


@dataclass(frozen=True)
class IncomingMessage:
    """Входящее текстовое сообщение, его правка или удаление."""

    message_id: str
    chat_id: str
    chat_name: str
    sender_id: str
    sender_name: str
    kind: str  # BotMessage.MESSAGE / EDITED / DELETED
    text: str
    # Правка и удаление: идентификатор исходного сообщения (stanzaId).
    target_id: str
    sent_at: datetime | None


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _sent_at(value) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def incoming_message(body) -> IncomingMessage | None:
    """Входящее текстовое сообщение из тела уведомления; остальное (фото,
    голосовые, статусы доставки, свои исходящие) — ``None``."""
    if not isinstance(body, dict) or body.get("typeWebhook") != "incomingMessageReceived":
        return None
    sender, data = _dict(body.get("senderData")), _dict(body.get("messageData"))
    message_id, chat_id = _text(body.get("idMessage")), _text(sender.get("chatId"))
    if not message_id or not chat_id:
        return None
    message_type = data.get("typeMessage")
    kind, text, target_id = BotMessage.MESSAGE, "", ""
    if isinstance(message_type, str) and message_type in _TEXT_TYPES:
        container, key = _TEXT_TYPES[message_type]
        raw = _dict(data.get(container)).get(key)
        # Текст как есть (с переносами строк отчёта) — без strip.
        text = raw if isinstance(raw, str) else ""
    elif message_type == "editedMessage":
        payload = _dict(data.get("editedMessageData"))
        kind, target_id = BotMessage.EDITED, _text(payload.get("stanzaId"))
        raw = payload.get("textMessage") or payload.get("caption")
        text = raw if isinstance(raw, str) else ""
    elif message_type == "deletedMessage":
        kind, target_id = BotMessage.DELETED, _text(_dict(data.get("deletedMessageData")).get("stanzaId"))
    else:
        return None
    return IncomingMessage(
        message_id=message_id,
        chat_id=chat_id,
        chat_name=_text(sender.get("chatName")),
        sender_id=_text(sender.get("sender")) or chat_id,
        sender_name=_text(sender.get("senderContactName")) or _text(sender.get("senderName")),
        kind=kind,
        text=text,
        target_id=target_id,
        sent_at=_sent_at(body.get("timestamp")),
    )


def state_change(body) -> str | None:
    """Новое состояние номера из уведомления stateInstanceChanged."""
    if isinstance(body, dict) and body.get("typeWebhook") == "stateInstanceChanged":
        return _text(body.get("stateInstance")) or None
    return None


class GreenApiClient:
    """Четыре метода API, которые нужны боту. ``opener`` подменяется в тестах."""

    def __init__(self, *, api_url: str, instance_id: str, token: str, receive_timeout: int, opener=None):
        if not instance_id or not token:
            raise GreenApiError("Не заданы WHATSAPP_BOT_INSTANCE_ID и WHATSAPP_BOT_API_TOKEN")
        self.api_url = api_url.rstrip("/")
        self.instance_id = instance_id
        self._token = token
        self.receive_timeout = receive_timeout
        self._open = opener or urllib.request.urlopen

    @classmethod
    def from_settings(cls) -> GreenApiClient:
        return cls(
            api_url=settings.WHATSAPP_BOT_API_URL,
            instance_id=settings.WHATSAPP_BOT_INSTANCE_ID,
            token=settings.WHATSAPP_BOT_API_TOKEN,
            receive_timeout=settings.WHATSAPP_BOT_RECEIVE_TIMEOUT_SECONDS,
        )

    def _call(self, http_method: str, api_method: str, *, suffix: str = "", query: str = "",
              body: dict | None = None, timeout: float = _REQUEST_TIMEOUT_SECONDS) -> Any:
        url = f"{self.api_url}/waInstance{self.instance_id}/{api_method}/{self._token}{suffix}{query}"
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=data, method=http_method, headers={
            "Content-Type": "application/json", "Accept": "application/json",
        })
        try:
            with self._open(request, timeout=timeout) as response:
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            error = GreenApiOutcomeUnknown if exc.code >= 500 else GreenApiError
            raise error(f"Green-API {api_method}: HTTP {exc.code}") from None
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
            error = GreenApiError if _never_sent(reason) else GreenApiOutcomeUnknown
            raise error(f"Green-API {api_method}: нет связи ({type(exc).__name__})") from None
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise GreenApiOutcomeUnknown(f"Green-API {api_method}: слишком большой ответ")
        if not raw.strip():
            return None
        try:
            return json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise GreenApiOutcomeUnknown(f"Green-API {api_method}: ответ не JSON") from None

    def receive_notification(self) -> Notification | None:
        """Следующее уведомление очереди или ``None``, если за время ожидания ничего не пришло."""
        payload = self._call(
            "GET", "receiveNotification", query=f"?receiveTimeout={self.receive_timeout}",
            timeout=self.receive_timeout + _NETWORK_MARGIN_SECONDS,
        )
        if payload is None:
            return None
        receipt_id = _dict(payload).get("receiptId")
        if isinstance(receipt_id, bool) or not isinstance(receipt_id, int):
            raise GreenApiError("Green-API receiveNotification: нет receiptId")
        return Notification(receipt_id, _dict(_dict(payload).get("body")))

    def delete_notification(self, receipt_id: int) -> None:
        """Подтвердить приём: уведомление уходит из очереди провайдера."""
        payload = self._call("DELETE", "deleteNotification", suffix=f"/{int(receipt_id)}")
        # result=false — уведомления уже нет в очереди (удалено раньше): это не ошибка.
        if not isinstance(payload, dict) or "result" not in payload:
            raise GreenApiError("Green-API deleteNotification: неожиданный ответ")

    def send_message(self, chat_id: str, message: str, *, quoted_message_id: str = "") -> str:
        """Отправить текст в чат (цитатой, если задан ``quoted_message_id``); вернуть id сообщения."""
        body = {"chatId": chat_id, "message": message}
        if quoted_message_id:
            body["quotedMessageId"] = quoted_message_id
        payload = self._call("POST", "sendMessage", body=body)
        message_id = _text(_dict(payload).get("idMessage"))
        if not message_id:
            # Провайдер ответил, но без id: сообщение могло уйти.
            raise GreenApiOutcomeUnknown("Green-API sendMessage: нет idMessage")
        return message_id

    def get_state_instance(self) -> str:
        """Состояние номера: authorized, notAuthorized, blocked, sleepMode, starting, yellowCard."""
        payload = self._call("GET", "getStateInstance")
        state = _text(_dict(payload).get("stateInstance"))
        if not state:
            raise GreenApiError("Green-API getStateInstance: нет stateInstance")
        return state
