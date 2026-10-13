"""Вопросы к ИИ-ассистенту: разговоры, история, лимиты, сохранение ответа."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.common.locks import claim_owned_lease, release_owned_lease
from apps.common.openai_responses import OpenAIResponseError

from . import agent
from .models import AssistantChat, AssistantMessage

log = logging.getLogger(__name__)

HISTORY_MESSAGES = 12
# Ответ держит поток gunicorn до двух-трёх минут (всего потоков 3×8): ассистент
# не должен занять их все. Аренда с запасом переживает самый долгий ответ.
PARALLEL_ANSWERS = 4
_LEASE_SECONDS = 300
_HISTORY_SQL_CHARS = 4000
_TITLE_CHARS = 80

_ERROR_TEXTS = {
    "openai_authentication_failed": "ИИ-сервис не принял ключ доступа. Сообщите администратору.",
    "openai_rate_limited": "ИИ-сервис сейчас перегружен. Повторите вопрос через минуту.",
    "openai_refused": "ИИ отказался отвечать на этот вопрос.",
    "assistant_step_limit": "Не получилось собрать ответ за отведённое время. Уточните вопрос: период, клиента, товар.",
}
_DEFAULT_ERROR_TEXT = "ИИ-сервис не ответил. Повторите вопрос."


class AssistantBusy(Exception):
    """Ответ сейчас начать нельзя: у сотрудника уже идёт ответ или заняты все места."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class AnswerSlot:
    keys: tuple[str, ...]
    owner: str

    def release(self) -> None:
        for key in self.keys:
            release_owned_lease(key, self.owner)


def claim_answer_slot(user) -> AnswerSlot:
    """Место для ответа: один ответ на сотрудника и не больше PARALLEL_ANSWERS на всех."""
    owner = uuid4().hex
    user_key = f"assistant:answering:user:{user.pk}"
    if not claim_owned_lease(user_key, owner, _LEASE_SECONDS):
        raise AssistantBusy("assistant_user_busy")
    for index in range(PARALLEL_ANSWERS):
        slot_key = f"assistant:answering:slot:{index}"
        if claim_owned_lease(slot_key, owner, _LEASE_SECONDS):
            return AnswerSlot(keys=(user_key, slot_key), owner=owner)
    release_owned_lease(user_key, owner)
    raise AssistantBusy("assistant_busy")


def enabled() -> bool:
    return bool(settings.OPENAI_API_KEY and settings.ASSISTANT_AI_MODEL)


def questions_left_today(user) -> int:
    day_start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    asked = AssistantMessage.objects.filter(
        chat__user=user, role=AssistantMessage.USER, created_at__gte=day_start,
    ).count()
    return max(0, settings.ASSISTANT_MAX_DAILY_QUESTIONS - asked)


def _title(question: str) -> str:
    line = " ".join(question.split())
    return line if len(line) <= _TITLE_CHARS else line[: _TITLE_CHARS - 1].rstrip() + "…"


@transaction.atomic
def start_question(user, question: str, chat: AssistantChat | None = None) -> tuple[AssistantChat, AssistantMessage]:
    """Записать вопрос (в новый разговор, если ``chat`` не задан)."""
    if chat is None:
        chat = AssistantChat.objects.create(user=user, title=_title(question))
    else:
        chat.save(update_fields=["updated_at"])
    message = AssistantMessage.objects.create(chat=chat, role=AssistantMessage.USER, content=question)
    return chat, message


def _history(chat: AssistantChat, before: AssistantMessage) -> list[dict[str, Any]]:
    """Последние реплики разговора для модели; к ответам — их SQL, чтобы уточнять вопрос."""
    messages = chat.messages.filter(id__lt=before.id, error="").order_by("-id")[:HISTORY_MESSAGES]
    items = []
    for message in reversed(messages):
        content = message.content
        sql = "\n".join(
            step["sql"] for step in message.trace if step.get("kind") == "sql" and step.get("status") == "done"
        )
        if sql:
            content += "\n\n[SQL этого ответа]\n" + sql[:_HISTORY_SQL_CHARS]
        items.append({"role": message.role, "content": content})
    return items


def answer_events(
    user, chat: AssistantChat, question: AssistantMessage, slot: AnswerSlot,
) -> Iterator[dict[str, Any]]:
    """Ход ответа событиями ``step``; последнее событие — сохранённый ответ (``reply``).

    Место ``slot`` освобождается, когда поток закончился или оборвался.
    """
    try:
        trace: list[dict[str, Any]] = []
        try:
            answer = yield from agent.run(user, question.content, _history(chat, question), trace)
        except (OpenAIResponseError, agent.AssistantError) as exc:
            log.warning("Assistant answer failed: %s", exc.code)
            fields = {"content": _ERROR_TEXTS.get(exc.code, _DEFAULT_ERROR_TEXT), "error": exc.code}
        else:
            fields = {"content": answer.text, "table": answer.table, "stats": answer.stats}
        reply = AssistantMessage.objects.create(chat=chat, role=AssistantMessage.ASSISTANT, trace=trace, **fields)
        chat.save(update_fields=["updated_at"])
        yield {"type": "reply", "message": reply}
    finally:
        slot.release()
