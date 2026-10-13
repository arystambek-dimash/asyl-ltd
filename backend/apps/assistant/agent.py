"""Цикл ИИ-ассистента: модель OpenAI с инструментами по схеме базы и SQL.

Модель получает инструкцию (:mod:`.knowledge`) и историю разговора, вызывает
инструменты — поиск таблиц, их структура, SELECT только на чтение, — пока не
соберёт ответ, и отвечает строгим JSON ``{"answer", "show_query", "chart"}``.

Ход работы отдаётся событиями по мере работы: ``step`` — шаг (раздумье модели,
поиск, структура таблиц, SQL) в состоянии running, затем тот же ``id`` готовым,
со временем и числом строк; ``map`` — какие таблицы уже затронуты и как они
связаны. Готовый ответ — значение генератора: текст, таблица, ход работы и
статистика (время модели и SQL, токены).
"""

from __future__ import annotations

import json
import time
from collections.abc import Generator
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from django.conf import settings
from django.db import DatabaseError

from apps.common import openai_responses

from . import knowledge, schema
from .queries import run_select
from .sql_guard import UnsafeQuery, referenced_tables

MODEL_CALL_LIMIT = 10
TIME_BUDGET_SECONDS = 150
REQUEST_TIMEOUT_SECONDS = 90
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_OUTPUT_TOKENS = 16_000
# Сколько строк результата видит модель; таблица в ответе — до queries.MAX_ROWS.
_MODEL_ROWS = 100
_MODEL_RESULT_CHARS = 40_000
_MAX_SQL_CHARS = 10_000
_MAX_DESCRIBE_TABLES = 10
_PREVIEW_ROWS = 3
_PROMPT_CACHE_KEY = "asyl-assistant"
CHART_TYPES = ("bar", "line")

Event = dict[str, Any]


def _function(name: str, description: str, properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "name": name,
        "description": description,
        "strict": True,
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": list(properties),
            "properties": properties,
        },
    }


TOOLS = [
    _function(
        "search_schema",
        "Найти таблицы по смыслу: слова на русском или английском («долг клиента», «вагон вес», "
        "«iin»). Возвращает подходящие таблицы с назначением.",
        {"query": {"type": "string"}},
    ),
    _function(
        "describe_tables",
        "Столбцы таблиц: тип, связи (→ таблица.столбец), подписи, варианты значений и описание модели.",
        {"tables": {"type": "array", "items": {"type": "string"}}},
    ),
    _function(
        "run_sql",
        "Выполнить один SELECT (PostgreSQL) только на чтение. Таблицы — только из списка системы, "
        "без звёздочки. Результат — столбцы и строки JSON.",
        {
            "purpose": {"type": "string", "description": "Что считаем — коротко по-русски, это видит сотрудник."},
            "sql": {"type": "string"},
        },
    ),
]

ANSWER_FORMAT = {
    "type": "json_schema",
    "name": "assistant_answer",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["answer", "show_query", "chart"],
        "properties": {
            "answer": {"type": "string"},
            "show_query": {"type": ["integer", "null"]},
            "chart": {
                "type": "object",
                "additionalProperties": False,
                "required": ["type", "x", "y"],
                "properties": {
                    "type": {"type": "string", "enum": ["none", *CHART_TYPES]},
                    "x": {"type": "string"},
                    "y": {"type": "string"},
                },
            },
        },
    },
}


class AssistantError(Exception):
    """Ответ не собран по причине самого ассистента (не провайдера)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass
class QueryRecord:
    purpose: str
    sql: str
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    truncated: bool = False
    error: str = ""
    ms: int = 0


@dataclass(frozen=True)
class Answer:
    text: str
    table: dict[str, Any] | None
    stats: dict[str, Any]


def _ms_since(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


class _Run:
    """Ход одного ответа: шаги, затронутые таблицы и счётчики."""

    def __init__(self, model: str, steps: list[dict[str, Any]]):
        self.started = time.monotonic()
        self.steps = steps
        self.tables: list[str] = []
        self._step_started: dict[int, float] = {}
        self.stats: dict[str, Any] = {
            "model": model,
            "model_calls": 0,
            "model_ms": 0,
            "sql_queries": 0,
            "sql_ms": 0,
            "input_tokens": 0,
            "cached_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
        }

    def start(self, kind: str, text: str, **fields: Any) -> Event:
        step = {"id": len(self.steps) + 1, "kind": kind, "status": "running", "text": text, **fields}
        self.steps.append(step)
        self._step_started[step["id"]] = time.monotonic()
        return {"type": "step", "step": dict(step)}

    def finish(self, step_id: int, *, status: str = "done", **fields: Any) -> Event:
        step = self.steps[step_id - 1]
        step.update(status=status, ms=_ms_since(self._step_started.pop(step_id)), **fields)
        return {"type": "step", "step": dict(step)}

    def touch(self, names: list[str]) -> Generator[Event, None, None]:
        touched = schema.data_map([*self.tables, *names])
        if touched["tables"] != self.tables:
            self.tables = touched["tables"]
            yield {"type": "map", **touched}

    def model_call(self, usage: dict[str, int], ms: int) -> None:
        self.stats["model_calls"] += 1
        self.stats["model_ms"] += ms
        for key, value in usage.items():
            self.stats[key] = self.stats.get(key, 0) + value

    def finished_stats(self) -> dict[str, Any]:
        return {**self.stats, "total_ms": _ms_since(self.started)}


def _request(
    instructions: str, items: list[dict[str, Any]], *, model: str, effort: str, final: bool,
) -> openai_responses.Completed:
    body: dict[str, Any] = {
        "model": model,
        "store": False,
        "instructions": instructions,
        "input": list(items),
        "tools": TOOLS,
        # Последний шаг: инструменты закрыты, модель обязана ответить тем, что уже знает.
        "tool_choice": "none" if final else "auto",
        "text": {"format": ANSWER_FORMAT},
        "max_output_tokens": _MAX_OUTPUT_TOKENS,
        # Неизменная часть инструкции одинакова у всех вопросов: провайдер кэширует её.
        "prompt_cache_key": _PROMPT_CACHE_KEY,
    }
    if effort:
        body["reasoning"] = {"effort": effort}
        # store=False: рассуждение между шагами передаётся зашифрованным в input.
        body["include"] = ["reasoning.encrypted_content"]
    return openai_responses.request_completed(
        body, max_response_bytes=_MAX_RESPONSE_BYTES, timeout=REQUEST_TIMEOUT_SECONDS,
    )


def _as_input(output: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in output.items() if key != "status"}


def _database_error(exc: DatabaseError) -> str:
    return (str(exc).strip().split("\n", 1)[0] or type(exc).__name__)[:300]


def _execute(record: QueryRecord) -> None:
    started = time.monotonic()
    try:
        result = run_select(record.sql)
    except UnsafeQuery as exc:
        record.error = str(exc)
    except DatabaseError as exc:
        record.error = _database_error(exc)
    else:
        record.columns, record.rows, record.truncated = result.columns, result.rows, result.truncated
    record.ms = _ms_since(started)


def _result_for_model(record: QueryRecord) -> str:
    if record.error:
        return f"Ошибка: {record.error}"
    shown = record.rows[:_MODEL_ROWS]
    payload = {
        "columns": record.columns,
        "rows": shown,
        "row_count": len(record.rows),
        "more_rows_exist": record.truncated or len(record.rows) > len(shown),
    }
    text = json.dumps(payload, ensure_ascii=False)
    if len(text) > _MODEL_RESULT_CHARS:
        text = text[:_MODEL_RESULT_CHARS] + "… (результат обрезан: считайте итоги в SQL)"
    return text


def _call_tool(call: dict[str, Any], run: _Run, queries: list[QueryRecord]) -> Generator[Event, None, str]:
    try:
        args = json.loads(call.get("arguments") or "{}")
    except (TypeError, ValueError):
        args = None
    if not isinstance(args, dict):
        return "Ошибка: аргументы инструмента — не JSON-объект."
    name = call.get("name")
    if name == "search_schema":
        query = str(args.get("query") or "")[:200]
        event = run.start("search", f"Ищу в справочнике: {query}")
        yield event
        found = schema.search(query)
        yield run.finish(event["step"]["id"], found=len(found))
        return "\n".join(found) or "Ничего не найдено — выберите таблицу из списка в инструкции."
    if name == "describe_tables":
        names = [str(table).strip().lower() for table in args.get("tables") or []][:_MAX_DESCRIBE_TABLES]
        event = run.start("schema", "Изучаю структуру таблиц", tables=names)
        yield event
        yield from run.touch(names)
        description = schema.describe(names)
        yield run.finish(event["step"]["id"])
        return description
    if name == "run_sql":
        record = QueryRecord(
            purpose=str(args.get("purpose") or "Запрос к базе")[:200],
            sql=str(args.get("sql") or "")[:_MAX_SQL_CHARS],
        )
        tables = referenced_tables(record.sql)
        event = run.start("sql", record.purpose, sql=record.sql, tables=tables)
        yield event
        yield from run.touch(tables)
        _execute(record)
        queries.append(record)
        run.stats["sql_queries"] += 1
        run.stats["sql_ms"] += record.ms
        if record.error:
            yield run.finish(event["step"]["id"], status="error", error=record.error)
        else:
            preview = {"columns": record.columns, "rows": record.rows[:_PREVIEW_ROWS]}
            yield run.finish(event["step"]["id"], row_count=len(record.rows), preview=preview)
        return _result_for_model(record)
    return f"Ошибка: инструмента {name} нет."


def _is_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    try:
        return isinstance(value, str) and Decimal(value).is_finite()
    except InvalidOperation:
        return False


def _chart(spec: Any, table: dict[str, Any]) -> dict[str, str] | None:
    """График к таблице ответа, если модель назвала существующие столбцы и ось Y — числа."""
    if not isinstance(spec, dict) or spec.get("type") not in CHART_TYPES:
        return None
    columns = table["columns"]
    x, y = spec.get("x"), spec.get("y")
    if x not in columns or y not in columns or x == y:
        return None
    index = columns.index(y)
    values = [row[index] for row in table["rows"] if row[index] is not None]
    if not values or not all(_is_number(value) for value in values):
        return None
    return {"type": spec["type"], "x": x, "y": y}


def _answer(outputs: list[dict[str, Any]], queries: list[QueryRecord]) -> tuple[str, dict[str, Any] | None]:
    data = openai_responses.parse_single_json(openai_responses.output_texts(outputs))
    if not isinstance(data, dict) or not str(data.get("answer") or "").strip():
        raise AssistantError("assistant_empty_answer")
    show = data.get("show_query")
    table = None
    if isinstance(show, int) and not isinstance(show, bool) and 1 <= show <= len(queries):
        record = queries[show - 1]
        if not record.error and record.rows:
            table = {"columns": record.columns, "rows": record.rows, "truncated": record.truncated}
            chart = _chart(data.get("chart"), table)
            if chart:
                table["chart"] = chart
    return str(data["answer"]).strip(), table


def run(
    user,
    question: str,
    history: list[dict[str, Any]],
    trace: list[dict[str, Any]],
    *,
    model: str | None = None,
    effort: str | None = None,
) -> Generator[Event, None, Answer]:
    """Ответить на ``question``; шаги копятся в ``trace`` даже при сбое.

    ``model`` и ``effort`` по умолчанию — из настроек (проверка качества
    подставляет свои). Ошибки — :class:`AssistantError` и ``OpenAIResponseError``.
    """
    model = model or settings.ASSISTANT_AI_MODEL
    effort = settings.ASSISTANT_AI_REASONING_EFFORT if effort is None else effort
    run_state = _Run(model, trace)
    queries: list[QueryRecord] = []
    instructions = knowledge.instructions(user)
    items: list[dict[str, Any]] = [*history, {"role": "user", "content": question}]
    for call_number in range(1, MODEL_CALL_LIMIT + 1):
        final = call_number == MODEL_CALL_LIMIT or time.monotonic() - run_state.started > TIME_BUDGET_SECONDS
        thinking = run_state.start("think", "Разбираю вопрос" if call_number == 1 else "Думаю над следующим шагом")
        yield thinking
        started = time.monotonic()
        completed = _request(instructions, items, model=model, effort=effort, final=final)
        run_state.model_call(completed.usage, _ms_since(started))
        calls = [output for output in completed.outputs if output.get("type") == "function_call"]
        if not calls:
            yield run_state.finish(thinking["step"]["id"], text="Формулирую ответ")
            text, table = _answer(completed.outputs, queries)
            return Answer(text=text, table=table, stats=run_state.finished_stats())
        yield run_state.finish(thinking["step"]["id"])
        if final:
            break
        items.extend(_as_input(output) for output in completed.outputs)
        for call in calls:
            result = yield from _call_tool(call, run_state, queries)
            items.append({"type": "function_call_output", "call_id": call.get("call_id"), "output": result})
    raise AssistantError("assistant_step_limit")
