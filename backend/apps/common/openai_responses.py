"""Клиент OpenAI Responses API со строгим JSON-ответом (``json_schema``).

Один транспорт для всех ИИ-проверок: черновик отчёта Telegram-бота, сверка
фото весов вывоза, номер транспорта отгрузки, ИИ-ассистент по данным. Промпты,
схемы и проверка содержимого ответа остаются у вызывающих; здесь — POST
``/v1/responses``, лимит размера ответа, проверка завершённости и разбор
единственного ``output_text`` ассистента.

Любой сбой — :class:`OpenAIResponseError` с безопасным кодом: тело ответа
провайдера, картинки и ключ в него не попадают.
"""

from __future__ import annotations

import http.client
import json
import re
from dataclasses import dataclass, field
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


def _post(body: dict[str, Any], max_response_bytes: int, timeout: float) -> object:
    connection = http.client.HTTPSConnection(HOST, timeout=timeout)
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


@dataclass(frozen=True)
class Completed:
    """Завершённый ответ: элементы ``output``, id и расход токенов."""

    outputs: list[dict[str, Any]]
    response_id: str
    usage: dict[str, int] = field(default_factory=dict)


def _count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _usage(payload: dict[str, Any]) -> dict[str, int]:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return {}
    inputs = usage.get("input_tokens_details")
    outputs = usage.get("output_tokens_details")
    return {
        "input_tokens": _count(usage.get("input_tokens")),
        "cached_tokens": _count(inputs.get("cached_tokens")) if isinstance(inputs, dict) else 0,
        "output_tokens": _count(usage.get("output_tokens")),
        "reasoning_tokens": _count(outputs.get("reasoning_tokens")) if isinstance(outputs, dict) else 0,
    }


def request_completed(
    body: dict[str, Any],
    *,
    max_response_bytes: int = MAX_RESPONSE_BYTES,
    timeout: float = TIMEOUT_SECONDS,
) -> Completed:
    """Отправить запрос и вернуть завершённый ответ (:class:`Completed`).

    Ответ принимается только завершённым (``status == "completed"``); отказ
    модели — код ``openai_refused``. Вызовы инструментов (``function_call``)
    разбирает вызывающий.
    """
    payload = _post(body, max_response_bytes, timeout)
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
    return Completed(outputs=outputs, response_id=response_id, usage=_usage(payload))


def output_texts(outputs: list[dict[str, Any]], *, response_id: str = "") -> list[object]:
    """Все ``output_text`` сообщений ассистента; отказ модели — ``openai_refused``."""
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
    return texts


def parse_single_json(texts: list[object], *, response_id: str = "") -> Any:
    """JSON из единственного ``output_text``; иначе ``openai_invalid_response``."""
    if len(texts) != 1 or not isinstance(texts[0], str):
        raise OpenAIResponseError("openai_invalid_response", response_id=response_id)
    try:
        return json.loads(texts[0])
    except (ValueError, TypeError) as exc:
        raise OpenAIResponseError("openai_invalid_response", response_id=response_id) from exc


def request_json(
    body: dict[str, Any], *, max_response_bytes: int = MAX_RESPONSE_BYTES
) -> tuple[Any, str]:
    """Отправить запрос и вернуть (JSON из ответа модели, id ответа).

    Ответ принимается только завершённым и ровно с одним ``output_text`` у
    сообщения ассистента; отказ модели — код ``openai_refused``.
    """
    completed = request_completed(body, max_response_bytes=max_response_bytes)
    texts = output_texts(completed.outputs, response_id=completed.response_id)
    return parse_single_json(texts, response_id=completed.response_id), completed.response_id
