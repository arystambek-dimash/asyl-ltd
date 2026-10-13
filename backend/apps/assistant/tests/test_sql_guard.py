"""Проверка SQL ассистента: проходит только чтение таблиц системы без секретов."""

import pytest

from apps.assistant.sql_guard import UnsafeQuery, checked_sql

ACCEPTED = [
    "SELECT o.status, count(*) FROM orders_order o WHERE o.deleted_at IS NULL GROUP BY o.status",
    "WITH t AS (SELECT order_id, sum(quantity) q FROM orders_orderitem GROUP BY 1) "
    "SELECT o.id, t.q FROM orders_order o JOIN t ON t.order_id = o.id ORDER BY t.q DESC LIMIT 5",
    "SELECT u.id, u.username, u.first_name FROM accounts_user u",
    "SELECT count(*) FROM accounts_user",
    "SELECT created_at::date d, to_char(created_at, 'DD.MM.YYYY'), age(now(), created_at) FROM orders_order",
    "SELECT g::date FROM generate_series(current_date - 7, current_date, interval '1 day') g",
    "SELECT id FROM orders_order UNION SELECT id FROM orders_payment",
    "SELECT json_agg(o) FROM orders_order o",
]

REJECTED = [
    ("SELECT 1; DELETE FROM orders_order", "ровно один"),
    ("DELETE FROM orders_order", "только запросы SELECT"),
    ("UPDATE orders_order SET status = 'shipped'", "только запросы SELECT"),
    ("COPY orders_order TO PROGRAM 'id'", "только запросы SELECT"),
    ("EXPLAIN ANALYZE SELECT 1", "только запросы SELECT"),
    ("SELECT id FROM orders_order FOR UPDATE", "только чтение"),
    ("SELECT id INTO copy_of_orders FROM orders_order", "только чтение"),
    ("SELECT * FROM pg_catalog.pg_authid", "недоступна"),
    ("SELECT table_name FROM information_schema.tables", "недоступна"),
    ("SELECT id FROM unknown_table", "нет в каталоге"),
    ("SELECT id FROM assistant_assistantmessage", "нет в каталоге"),
    ("SELECT pg_read_file('/etc/passwd')", "pg_read_file"),
    ("SELECT query_to_xml('select 1', true, true, '')", "query_to_xml"),
    ("SELECT set_config('statement_timeout', '0', false)", "set_config"),
    ("SELECT pg_sleep(60)", "pg_sleep"),
    ("SELECT dblink('host=x', 'select 1')", "dblink"),
    ("SELECT pg_catalog.age(now())", "со схемой"),
    ("SELECT password FROM accounts_user", "секретный"),
    ("SELECT (u).password FROM accounts_user u", "секретный"),
    ("SELECT apipay_api_key_encrypted FROM clients_department", "секретный"),
    ("SELECT * FROM accounts_user", "звёздочки"),
    ("SELECT u.* FROM accounts_user u", "звёздочки"),
    ("SELECT id FROM (SELECT * FROM accounts_user) s", "звёздочки"),
    ("SELECT json_agg(u) FROM accounts_user u", "целиком"),
    ("SELECT to_json(accounts_user) FROM accounts_user", "целиком"),
    ("SELEC broken", ""),
]


@pytest.mark.parametrize("sql", ACCEPTED)
def test_reads_of_system_tables_pass_and_get_an_outer_limit(sql):
    checked = checked_sql(sql, limit=201)

    assert checked.startswith("SELECT * FROM (")
    assert checked.endswith(") AS assistant_query LIMIT 201")


@pytest.mark.parametrize(("sql", "reason"), REJECTED)
def test_writes_system_catalogs_dangerous_functions_and_secrets_are_rejected(sql, reason):
    with pytest.raises(UnsafeQuery) as caught:
        checked_sql(sql, limit=201)
    assert reason in str(caught.value)


def test_executed_sql_is_rebuilt_without_comments():
    checked = checked_sql("SELECT id /* ; DROP TABLE orders_order */ FROM orders_order -- tail", limit=5)

    assert "DROP" not in checked and "--" not in checked and "/*" not in checked
