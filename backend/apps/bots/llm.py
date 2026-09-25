"""ИИ-черновик отчёта о вагонах, когда сообщение не разобралось по формату.

Запрос идёт через общий клиент OpenAI (:mod:`apps.common.openai_responses`),
здесь — только промпт и схема черновика. Включается флагом
``WHATSAPP_BOT_LLM_ENABLED`` (по умолчанию выключен). Результат — текст в
формате владельца для человека на разборе: бот по нему никогда не проводит.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.conf import settings

from apps.common import openai_responses
from apps.common.openai_responses import OpenAIResponseError

from .parsing import format_rail_report

log = logging.getLogger(__name__)

_MAX_RESPONSE_BYTES = 256 * 1024
_INSTRUCTIONS = (
    "Ты разбираешь сообщение из WhatsApp об отгрузке вагонов с мукой. Верни дату отгрузки "
    "(ДД.ММ.ГГГГ, пусто — если её нет), страну, клиента (как в сообщении), станцию назначения "
    "и список вагонов: код товара как в сообщении, номер вагона (8 цифр, как написан) и вес в "
    "тоннах. Ничего не придумывай: чего нет в сообщении — пустая строка или пустой список."
)
_WAGON = {
    "type": "object",
    "additionalProperties": False,
    "required": ["code", "number", "tons"],
    "properties": {"code": {"type": "string"}, "number": {"type": "string"}, "tons": {"type": "string"}},
}
_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["day", "country", "client_name", "station", "wagons"],
    "properties": {
        "day": {"type": "string"},
        "country": {"type": "string"},
        "client_name": {"type": "string"},
        "station": {"type": "string"},
        "wagons": {"type": "array", "items": _WAGON},
    },
}


def enabled() -> bool:
    return settings.WHATSAPP_BOT_LLM_ENABLED and bool(settings.OPENAI_API_KEY)


def _request(text: str) -> object:
    body = {
        "model": settings.WHATSAPP_BOT_LLM_MODEL,
        "store": False,
        "instructions": _INSTRUCTIONS,
        "input": text,
        "max_output_tokens": 4000,
        "text": {"format": {"type": "json_schema", "name": "rail_report", "strict": True, "schema": _SCHEMA}},
    }
    data, _ = openai_responses.request_json(body, max_response_bytes=_MAX_RESPONSE_BYTES)
    return data


def _day(value: str, fallback: date) -> date:
    for pattern in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), pattern).date()
        except ValueError:
            continue
    return fallback


def _tons(value: str) -> Decimal | None:
    try:
        tons = Decimal(str(value).replace(",", ".").strip())
    except InvalidOperation:
        return None
    return tons if tons.is_finite() and tons > 0 else None


def draft_report(text: str, *, sent_on: date) -> str:
    """Черновик в формате владельца или «» (выключено, сбой, ничего не нашлось).

    Дата без даты в сообщении — день отправки: человек увидит её в
    предпросмотре и поправит.
    """
    if not enabled() or not text.strip():
        return ""
    try:
        data = _request(text)
    except OpenAIResponseError as exc:
        log.warning("WhatsApp bot LLM draft failed: %s", exc.code)
        return ""
    if not isinstance(data, dict):
        return ""
    wagons = [
        (str(wagon.get("code") or "").strip(), str(wagon.get("number") or "").strip(), tons)
        for wagon in data.get("wagons") or []
        if isinstance(wagon, dict) and (tons := _tons(wagon.get("tons") or "")) is not None
    ]
    if not wagons:
        return ""
    return format_rail_report(
        day=_day(str(data.get("day") or ""), sent_on),
        country=str(data.get("country") or "").strip(),
        client_name=str(data.get("client_name") or "").strip(),
        station=str(data.get("station") or "").strip(),
        wagons=wagons,
    )
