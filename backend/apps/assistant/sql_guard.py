"""Проверка SQL ассистента: ровно один SELECT по таблицам каталога.

Запрос разбирается sqlglot (диалект postgres) и собирается заново из дерева
без комментариев, поэтому база выполняет ровно то, что здесь проверено.
Правила: один запрос-выборка; таблицы — только из :func:`schema.catalog`
(схема public или CTE запроса); функции без типа в sqlglot — только из
белого списка (файлы, сеть, настройки сервера и динамический SQL — вне его);
секретные столбцы не называются, а таблицы с ними не читаются звёздочкой и
целой строкой. Сверху ставится LIMIT: в память не тянется больше строк,
чем покажут.

Это второй рубеж. Первый — транзакция READ ONLY с таймаутом (:mod:`.queries`).
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from . import schema

_RESULT_ALIAS = "assistant_query"
_MESSAGE_LIMIT = 300

_FORBIDDEN_NODES = (
    exp.Into, exp.Lock, exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop,
    exp.Alter, exp.Command, exp.Copy, exp.Set, exp.Transaction, exp.Commit, exp.Rollback,
    exp.Pragma, exp.Use, exp.Grant, exp.Revoke, exp.TruncateTable, exp.Analyze, exp.Kill,
    exp.LoadData, exp.Cache, exp.Uncache, exp.Refresh,
)

# Встроенные функции PostgreSQL, которые sqlglot не знает по типу (Anonymous).
# Только вычисления над значениями: строки, даты, числа, JSON, массивы, окна.
SAFE_FUNCTIONS = frozenset({
    "age", "make_date", "make_time", "make_timestamp", "make_timestamptz", "make_interval",
    "date_part", "date_bin", "to_timestamp", "to_number", "isfinite", "justify_days",
    "justify_hours", "timezone", "clock_timestamp", "statement_timestamp", "localtimestamp",
    "initcap", "lpad", "rpad", "ltrim", "rtrim", "btrim", "left", "right", "reverse", "repeat",
    "concat_ws", "format", "strpos", "char_length", "character_length", "octet_length",
    "regexp_replace", "regexp_match", "regexp_matches", "regexp_like", "regexp_count",
    "regexp_substr", "regexp_split_to_array", "regexp_split_to_table", "translate",
    "string_to_array", "array_to_string", "array_length", "array_position", "array_remove",
    "array_append", "array_cat", "cardinality", "trunc", "sign", "mod", "power", "sqrt", "cbrt",
    "exp", "ln", "log", "log10", "div", "width_bucket", "scale", "num_nulls", "num_nonnulls",
    "rank", "dense_rank", "percent_rank", "cume_dist", "ntile", "first_value", "last_value",
    "nth_value", "mode", "every", "bit_and", "bit_or", "bool_or", "stddev_pop", "stddev_samp",
    "var_pop", "var_samp", "covar_pop", "covar_samp", "regr_slope", "regr_intercept",
    "jsonb_array_length", "json_array_length", "jsonb_typeof", "json_typeof",
    "jsonb_extract_path", "jsonb_extract_path_text", "json_extract_path",
    "json_extract_path_text", "jsonb_object_keys", "json_object_keys", "jsonb_each",
    "jsonb_each_text", "json_each", "json_each_text", "jsonb_array_elements",
    "jsonb_array_elements_text", "json_array_elements", "json_array_elements_text",
    "jsonb_build_object", "json_build_object", "jsonb_build_array", "json_build_array",
    "jsonb_agg", "jsonb_object_agg", "json_object_agg", "jsonb_strip_nulls",
    "to_jsonb", "to_json",
})


class UnsafeQuery(ValueError):
    """Запрос отклонён; текст — подсказка модели, что исправить."""


def _reject(message: str) -> UnsafeQuery:
    return UnsafeQuery(message[:_MESSAGE_LIMIT])


def _parse(sql: str) -> exp.Expression:
    try:
        statements = [statement for statement in sqlglot.parse(sql, dialect="postgres") if statement]
    except SqlglotError as exc:
        raise _reject(f"SQL не разобран: {exc}") from exc
    if len(statements) != 1:
        raise _reject("Нужен ровно один запрос SELECT, без точки с запятой между запросами.")
    tree = statements[0]
    if not isinstance(tree, (exp.Select, exp.SetOperation)):
        raise _reject("Разрешены только запросы SELECT (можно с WITH и UNION).")
    for node in tree.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            raise _reject("Разрешено только чтение: без INTO, FOR UPDATE, изменений и команд.")
    return tree


def _check_tables(tree: exp.Expression) -> set[str]:
    """Проверить таблицы; вернуть имена/алиасы источников с секретными столбцами."""
    tables = schema.catalog()
    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    with_secrets = set()
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if not name:
            continue  # табличная функция (generate_series): проверяется как функция
        if table.catalog or (table.db and table.db.lower() != "public"):
            raise _reject(f"Таблица {table.sql(dialect='postgres')} недоступна: только таблицы системы.")
        if not table.db and name in cte_names:
            continue
        known = tables.get(name)
        if known is None:
            raise _reject(f"Таблицы {name} нет в каталоге — найдите нужную через search_schema.")
        if known.has_secrets:
            with_secrets.update({name, table.alias_or_name.lower()})
    return with_secrets


def _check_functions(tree: exp.Expression) -> None:
    for function in tree.find_all(exp.Func):
        if isinstance(function.parent, exp.Dot) and function.parent.expression is function:
            raise _reject("Функции со схемой (schema.func) недоступны — вызывайте без схемы.")
        anonymous = isinstance(function, (exp.Anonymous, exp.AnonymousAggFunc))
        if anonymous and function.name.lower() not in SAFE_FUNCTIONS:
            raise _reject(f"Функция {function.name} недоступна ассистенту.")


def _check_secrets(tree: exp.Expression, with_secrets: set[str]) -> None:
    for identifier in tree.find_all(exp.Identifier):
        if schema.is_secret(identifier.name):
            raise _reject(f"Столбец {identifier.name} секретный и недоступен.")
    if not with_secrets:
        return
    hint = "перечислите нужные столбцы явно: в " + ", ".join(sorted(with_secrets)) + " есть секретные столбцы"
    for star in tree.find_all(exp.Star):
        if not isinstance(star.parent, exp.Count):
            raise _reject(f"Без звёздочки — {hint}.")
    for column in tree.find_all(exp.Column):
        # Целая строка таблицы (json_agg(u), row_to_json(accounts_user)) несёт и секреты.
        if not column.table and column.name.lower() in with_secrets:
            raise _reject(f"Нельзя брать строку таблицы целиком — {hint}.")


def checked_sql(sql: str, *, limit: int) -> str:
    """Проверенный SQL, собранный заново, с внешним ``LIMIT limit``; иначе :class:`UnsafeQuery`."""
    tree = _parse(sql)
    with_secrets = _check_tables(tree)
    _check_functions(tree)
    _check_secrets(tree, with_secrets)
    wrapped = exp.select("*").from_(tree.subquery(_RESULT_ALIAS)).limit(limit)
    return wrapped.sql(dialect="postgres", comments=False)


def referenced_tables(sql: str) -> list[str]:
    """Таблицы каталога, которые читает запрос (для карты данных); не разобран — пусто."""
    try:
        tree = _parse(sql)
    except UnsafeQuery:
        return []
    tables = schema.catalog()
    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    names = []
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if name in tables and name not in cte_names and name not in names:
            names.append(name)
    return names
