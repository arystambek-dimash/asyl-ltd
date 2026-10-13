"""SELECT ассистента выполняется в транзакции только на чтение и откатывается."""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.db import DatabaseError, transaction
from django.utils import timezone

from apps.assistant import queries
from apps.assistant.queries import run_select
from apps.assistant.sql_guard import UnsafeQuery


@pytest.mark.django_db(transaction=True)
def test_select_returns_json_ready_cells_in_local_time():
    result = run_select(
        "SELECT 12.50::numeric AS amount, '2026-10-13 00:30:00+00'::timestamptz AS at, "
        "('2026-10-13 00:30:00+00'::timestamptz)::date AS local_day"
    )

    assert result.columns == ["amount", "at", "local_day"]
    # 00:30 UTC — уже 05:30 в Алматы; ::date считается по местному времени.
    assert result.rows == [["12.50", "2026-10-13 05:30", "2026-10-13"]]
    assert result.truncated is False


@pytest.mark.django_db(transaction=True)
def test_rows_are_capped_and_marked_truncated():
    result = run_select("SELECT g FROM generate_series(1, 1000) g")

    assert len(result.rows) == queries.MAX_ROWS
    assert result.truncated is True


@pytest.mark.django_db(transaction=True)
def test_transaction_is_read_only_even_if_the_guard_let_a_write_through(make_user):
    make_user("reader")
    with patch.object(queries, "checked_sql", return_value="UPDATE accounts_user SET first_name = 'x'"):
        with pytest.raises(DatabaseError, match="read-only"):
            run_select("SELECT 1")


@pytest.mark.django_db(transaction=True)
def test_statement_timeout_stops_long_queries():
    with patch.object(queries, "STATEMENT_TIMEOUT_MS", 50):
        with pytest.raises(DatabaseError, match="timeout"):
            run_select("SELECT count(*) FROM generate_series(1, 1000000000) g")


@pytest.mark.django_db
def test_refuses_to_run_inside_another_transaction():
    with transaction.atomic(), pytest.raises(RuntimeError):
        run_select("SELECT 1")


def test_unsafe_sql_never_reaches_the_database():
    with pytest.raises(UnsafeQuery):
        run_select("DELETE FROM orders_order")


def test_cells_cover_python_types():
    moment = timezone.now() - timedelta(days=1)
    assert queries._cell(Decimal("1E+3")) == "1000"
    assert queries._cell(None) is None
    assert queries._cell(memoryview(b"x")) == "<двоичные данные>"
    assert queries._cell({"a": "б"}) == '{"a": "б"}'
    assert queries._cell("x" * 400).endswith("…")
    assert queries._cell(moment) == timezone.localtime(moment).strftime("%Y-%m-%d %H:%M")
