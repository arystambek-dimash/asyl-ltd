"""Клиент OpenAI Responses API со строгим JSON-ответом (``json_schema``).

Один транспорт для всех ИИ-проверок: черновик отчёта WhatsApp-бота, сверка
фото весов вывоза, номер транспорта отгрузки. Промпты, схемы и проверка
содержимого ответа остаются у вызывающих; здесь — POST ``/v1/responses``,
лимит размера ответа и разбор единственного ``output_text`` ассистента.

Любой сбой — :class:`OpenAIResponseError` с безопасным кодом: тело ответа
провайдера, картинки и ключ в него не попадают.
"""

from __future__ import annotations

import http.client
import json
import re
from typing import Any

from django.conf import settings

HOST = "api.openai.com"
PATH = "/v1/responses"
TIMEOUT_SECONDS = 45
MAX_RESPONSE_BYTES = 128 * 1024

_RESPONSE_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,100}")


def safe_response_id(value: object) -> str:
    """Идентификатор ответа для журнала и БД; всё непохожее на id — пусто."""
    return value if isinstance(value, str) and _RESPONSE_ID_RE.fullmatch(value) else ""


class OpenAIResponseError(ValueError):
    """Безопасная причина сбоя; ``retryable`` — временный сбой, запрос стоит повторить."""

    def __init__(self, code: str, *, retryable: bool = False, response_id: object = ""):
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.response_id = safe_response_id(response_id)


def _http_error(status: int) -> OpenAIResponseError:
    if status in (401, 403):
        return OpenAIResponseError("openai_authentication_failed")
    if status == 429:
        return OpenAIResponseError("openai_rate_limited", retryable=True)
    if status in (408, 409, 425) or 500 <= status <= 599:
        return OpenAIResponseError("openai_unavailable", retryable=True)
    return OpenAIResponseError("openai_request_rejected")


def _post(body: dict[str, Any], max_response_bytes: int) -> object:
    connection = http.client.HTTPSConnection(HOST, timeout=TIMEOUT_SECONDS)
    try:
        connection.request(
            "POST",
            PATH,
            body=json.dumps(body).encode(),
            headers={
                "Authorization": "Bearer " + settings.OPENAI_API_KEY,
                "Content-Type": "application/json",
            },
        )
        response = connection.getresponse()
        if response.status != 200:
            # Тело ошибки не читаем: в нём может быть что угодно от провайдера.
            raise _http_error(response.status)
        raw = response.read(max_response_bytes + 1)
    except (http.client.HTTPException, OSError) as exc:
        raise OpenAIResponseError("openai_unavailable", retryable=True) from exc
    finally:
        connection.close()
    if len(raw) > max_response_bytes:
        raise OpenAIResponseError("openai_invalid_response")
    try:
        return json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise OpenAIResponseError("openai_invalid_response") from exc


def request_json(
    body: dict[str, Any], *, max_response_bytes: int = MAX_RESPONSE_BYTES
) -> tuple[Any, str]:
    """Отправить запрос и вернуть (JSON из ответа модели, id ответа).

    Ответ принимается только завершённым (``status == "completed"``) и ровно
    с одним ``output_text`` у сообщения ассистента; отказ модели — код
    ``openai_refused``.
    """
    payload = _post(body, max_response_bytes)
    if not isinstance(payload, dict):
        raise OpenAIResponseError("openai_invalid_response")
    response_id = safe_response_id(payload.get("id"))
    if payload.get("status") != "completed":
        details = payload.get("incomplete_details")
        reason = details.get("reason") if isinstance(details, dict) else None
        if reason == "max_output_tokens":
            raise OpenAIResponseError("openai_output_limit", response_id=response_id)
        if reason == "content_filter":
            raise OpenAIResponseError("openai_refused", response_id=response_id)
        raise OpenAIResponseError("openai_incomplete", retryable=True, response_id=response_id)
    outputs = payload.get("output")
    if not isinstance(outputs, list) or any(not isinstance(item, dict) for item in outputs):
        raise OpenAIResponseError("openai_invalid_response", response_id=response_id)
    texts: list[object] = []
    for output in outputs:
        if output.get("type") != "message" or output.get("role") != "assistant":
            continue
        parts = output.get("content")
        if not isinstance(parts, list) or any(not isinstance(part, dict) for part in parts):
            raise OpenAIResponseError("openai_invalid_response", response_id=response_id)
        if any(part.get("type") == "refusal" for part in parts):
            raise OpenAIResponseError("openai_refused", response_id=response_id)
        texts.extend(part.get("text") for part in parts if part.get("type") == "output_text")
    if len(texts) != 1 or not isinstance(texts[0], str):
        raise OpenAIResponseError("openai_invalid_response", response_id=response_id)
    try:
        return json.loads(texts[0]), response_id
    except (ValueError, TypeError) as exc:
        raise OpenAIResponseError("openai_invalid_response", response_id=response_id) from exc
