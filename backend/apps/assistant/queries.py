"""Выполнение SQL ассистента только на чтение.

Запрос сначала проходит :func:`sql_guard.checked_sql`, затем выполняется в
отдельной транзакции ``READ ONLY`` с таймаутом и часовым поясом Asia/Almaty
(местные дни в ``::date`` и ``now()``) и всегда откатывается.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone
from psycopg.types.datetime import TimestamptzLoader

from .sql_guard import checked_sql

MAX_ROWS = 200
STATEMENT_TIMEOUT_MS = 10_000
_MAX_CELL_CHARS = 300


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[list]
    truncated: bool


def _cell(value):
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        moment = timezone.localtime(value) if timezone.is_aware(value) else value
        return moment.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (bytes, memoryview)):
        return "<двоичные данные>"
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= _MAX_CELL_CHARS else text[:_MAX_CELL_CHARS] + "…"


def run_select(sql: str) -> QueryResult:
    """Выполнить проверенный SELECT; ``UnsafeQuery`` и ``DatabaseError`` — вызывающему."""
    safe_sql = checked_sql(sql, limit=MAX_ROWS + 1)
    if connection.in_atomic_block:
        # READ ONLY включается только первой командой транзакции: внутри чужой
        # транзакции запрос не выполняем вовсе, а не выполняем его «на запись».
        raise RuntimeError("assistant SQL must not run inside another transaction")
    with transaction.atomic():
        with connection.cursor() as cursor:
            # Загрузчик Django считает сессию базы в UTC и подменяет пояс у
            # timestamptz; здесь сессия в Asia/Almaty — берём стандартный psycopg.
            cursor.cursor.adapters.register_loader("timestamptz", TimestamptzLoader)
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute(
                "SELECT set_config('statement_timeout', %s, true), set_config('TimeZone', %s, true)",
                [str(STATEMENT_TIMEOUT_MS), settings.TIME_ZONE],
            )
            cursor.execute(safe_sql)
            columns = [column.name for column in cursor.description]
            rows = cursor.fetchall()
        transaction.set_rollback(True)
    return QueryResult(
        columns=columns,
        rows=[[_cell(value) for value in row] for row in rows[:MAX_ROWS]],
        truncated=len(rows) > MAX_ROWS,
    )
