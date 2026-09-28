"""Telegram Bot API — один клиент для всего приложения (бот отчётов, алерты камер).

Токен бота стоит в пути запроса — он не попадает ни в тексты ошибок, ни в
журнал: ошибки называют только метод API, код ответа и описание Telegram.

Сбой, после которого запрос мог выполниться (нет ответа, 5xx, непонятный
ответ), — :class:`TelegramOutcomeUnknown`: отправку сообщения по нему не
повторяют сами, иначе получатель увидит его дважды.
"""
from __future__ import annotations

import http.client
import json
import re
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

DEFAULT_API_URL = "https://api.telegram.org"
# Предел текста sendMessage; длиннее — несколькими сообщениями (:func:`split_text`).
MESSAGE_MAX_LENGTH = 4096
REQUEST_TIMEOUT_SECONDS = 15
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
# Сверх долгого опроса — на сеть и ответ Telegram.
_NETWORK_MARGIN_SECONDS = 10
_DESCRIPTION_LENGTH = 200
# Токен от @BotFather: «<id бота>:<секрет>». Пробел или перевод строки из
# копипасты сломал бы адрес запроса, а ошибка http.client показала бы токен.
_TOKEN = re.compile(r"\d+:[A-Za-z0-9_-]{20,}")


class TelegramError(RuntimeError):
    """Telegram недоступен или отказал: сообщение точно не ушло."""


class TelegramUnauthorized(TelegramError):
    """Неверный или отозванный токен бота (HTTP 401)."""


class TelegramRefused(TelegramError):
    """Telegram отказал в этом запросе (4xx): бот заблокирован, чата нет, слишком часто.

    Отказ касается одного чата или сообщения — остальные можно отправлять дальше.
    """


class TelegramOutcomeUnknown(TelegramError):
    """Запрос мог выполниться: ответа нет (тайм-аут, обрыв), 5xx или ответ непонятный.

    Остальные сбои — отказ Telegram (4xx) или запрос, который не ушёл
    (соединение отклонено, адрес не найден), — точно ничего не отправили.
    """


def _never_sent(reason) -> bool:
    """Запрос не ушёл: соединение отклонено или адрес Telegram не найден."""
    return isinstance(reason, (ConnectionRefusedError, socket.gaierror))


@dataclass(frozen=True)
class Update:
    update_id: int
    body: dict


def as_text(value) -> str:
    """Строка из ответа Telegram без пробелов по краям; не строка — пусто."""
    return value.strip() if isinstance(value, str) else ""


def as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def as_id(value) -> str:
    """Числовой идентификатор Telegram строкой; не число — пусто."""
    return str(value) if isinstance(value, int) and not isinstance(value, bool) else ""


def message_ref(chat_id: str, message_id: str) -> str:
    """Идентификатор сообщения для базы: message_id уникален только внутри чата."""
    return f"{chat_id}:{message_id}"


def _reply_target(ref: str) -> int | None:
    message_id = ref.rsplit(":", 1)[-1]
    return int(message_id) if message_id.isdigit() else None


def split_text(text: str, limit: int = MESSAGE_MAX_LENGTH) -> list[str]:
    """Текст частями не длиннее ``limit``: по пустым строкам, потом по строкам, в крайнем случае — посреди строки."""
    parts: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                parts.append(current)
                current = ""
            parts.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            parts.append(current)
            current = line
        else:
            current = candidate
    if current.strip():
        parts.append(current)
    return [part.strip("\n") for part in parts if part.strip()]


class TelegramClient:
    """Методы Bot API, которые нужны приложению. ``opener`` подменяется в тестах."""

    def __init__(self, *, token: str, token_name: str, api_url: str = DEFAULT_API_URL, poll_timeout: int = 25,
                 request_timeout: float = REQUEST_TIMEOUT_SECONDS, opener=None):
        if not token:
            raise TelegramError(f"Не задан {token_name}")
        if not _TOKEN.fullmatch(token):
            raise TelegramError(f"{token_name} не похож на токен от @BotFather (<id>:<секрет>)")
        self.api_url = api_url.rstrip("/")
        self._token = token
        self.poll_timeout = poll_timeout
        self.request_timeout = request_timeout
        self._open = opener or urllib.request.urlopen

    def _call(self, api_method: str, body: dict | None = None, *, timeout: float | None = None) -> Any:
        url = f"{self.api_url}/bot{self._token}/{api_method}"
        data = json.dumps(body or {}, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=data, method="POST", headers={
            "Content-Type": "application/json", "Accept": "application/json",
        })
        try:
            with self._open(request, timeout=timeout or self.request_timeout) as response:
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise self._http_error(api_method, exc) from None
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
            error = TelegramError if _never_sent(reason) else TelegramOutcomeUnknown
            raise error(f"Telegram {api_method}: нет связи ({type(exc).__name__})") from None
        except (ValueError, http.client.HTTPException) as exc:
            # Обрыв или мусор в ответе; текст исключения может содержать адрес с токеном.
            raise TelegramOutcomeUnknown(f"Telegram {api_method}: сбой ответа ({type(exc).__name__})") from None
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise TelegramOutcomeUnknown(f"Telegram {api_method}: слишком большой ответ")
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise TelegramOutcomeUnknown(f"Telegram {api_method}: ответ не JSON") from None
        if not isinstance(payload, dict) or payload.get("ok") is not True or "result" not in payload:
            raise TelegramOutcomeUnknown(f"Telegram {api_method}: неожиданный ответ")
        return payload["result"]

    @staticmethod
    def _http_error(api_method: str, exc: urllib.error.HTTPError) -> TelegramError:
        description = ""
        try:
            description = as_text(as_dict(json.loads(exc.read(64 * 1024))).get("description"))
        except (AttributeError, OSError, ValueError):
            # Тела нет или оно не JSON — хватит кода ответа.
            pass
        text = f"Telegram {api_method}: HTTP {exc.code}"
        if description:
            text += f" — {description[:_DESCRIPTION_LENGTH]}"
        if exc.code == 401:
            return TelegramUnauthorized(text)
        return TelegramOutcomeUnknown(text) if exc.code >= 500 else TelegramRefused(text)

    def get_me(self) -> str:
        """Username бота (без «@»): токен рабочий."""
        username = as_text(as_dict(self._call("getMe")).get("username"))
        if not username:
            raise TelegramError("Telegram getMe: нет username")
        return username

    def get_updates(self, offset: int | None) -> list[Update]:
        """Следующие обновления (долгий опрос); ``offset`` подтверждает всё, что до него."""
        body = {"timeout": self.poll_timeout, "allowed_updates": ["message", "edited_message"]}
        if offset is not None:
            body["offset"] = offset
        result = self._call("getUpdates", body, timeout=self.poll_timeout + _NETWORK_MARGIN_SECONDS)
        if not isinstance(result, list):
            raise TelegramError("Telegram getUpdates: неожиданный ответ")
        updates = []
        for item in result:
            update_id = as_dict(item).get("update_id")
            if isinstance(update_id, int) and not isinstance(update_id, bool):
                updates.append(Update(update_id, item))
        return updates

    def send_message(self, chat_id: str, text: str, *, reply_to: str = "") -> str:
        """Отправить текст (ответом на сообщение ``reply_to`` из :func:`message_ref`); вернуть его ref."""
        body: dict[str, Any] = {"chat_id": chat_id, "text": text, "link_preview_options": {"is_disabled": True}}
        target = _reply_target(reply_to) if reply_to else None
        if target is not None:
            body["reply_parameters"] = {"message_id": target, "allow_sending_without_reply": True}
        message_id = as_id(as_dict(self._call("sendMessage", body)).get("message_id"))
        if not message_id:
            # Telegram ответил, но без id: сообщение могло уйти.
            raise TelegramOutcomeUnknown("Telegram sendMessage: нет message_id")
        return message_ref(chat_id, message_id)
