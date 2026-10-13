"""Каталог базы для ИИ-ассистента: какие таблицы и столбцы он видит.

Каталог строится из моделей Django приложений ``apps.*``: таблица, модель,
докстринг, столбцы с типом, подписью, вариантами и внешним ключом. Это
«справочник» ассистента: модель ищет по нему таблицы (:func:`search`) и
читает их структуру (:func:`describe`), прежде чем писать SQL.

Секретные столбцы (пароли, ключи, ссылки с токенами) в каталог не попадают,
а :mod:`.sql_guard` отклоняет запрос, который их называет.
"""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass
from functools import cache

from django.apps import apps
from django.db import connection, models
from django.utils.text import camel_case_to_spaces

# Разговоры ассистента — личное дело каждого сотрудника, а не данные системы.
_EXCLUDED_APP_LABELS = frozenset({"assistant"})
_SECRET_NAMES = frozenset({"password"})
_SECRET_PARTS = ("secret", "token", "encrypted", "api_key")
_DOC_LIMIT = 700
_WORD_RE = re.compile(r"[\w]+", re.UNICODE)


def is_secret(column: str) -> bool:
    """Столбец (или любой идентификатор SQL), который ассистенту читать нельзя."""
    name = column.lower()
    return name in _SECRET_NAMES or any(part in name for part in _SECRET_PARTS)


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    label: str
    null: bool
    primary_key: bool
    references: str
    choices: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Table:
    name: str
    model: str
    label: str
    doc: str
    columns: tuple[Column, ...]
    has_secrets: bool

    def header(self) -> str:
        return f"{self.name} ({self.model})" + (f" — {self.label}" if self.label else "")

    def summary(self) -> str:
        first_line = self.doc.split("\n", 1)[0] if self.doc else ""
        return self.header() + (f" — {first_line}" if first_line else "")


def _custom_label(value, default: str) -> str:
    label = str(value or "")
    return "" if label.lower() == default.lower() else label


def _model_doc(model) -> str:
    doc = inspect.getdoc(model) or ""
    # Django подставляет «Order(id, client, …)», если докстринга нет.
    if doc.startswith(f"{model.__name__}("):
        return ""
    return doc[:_DOC_LIMIT]


def _column(field: models.Field) -> Column:
    references = ""
    if field.is_relation and field.related_model is not None:
        target = field.target_field
        references = f"{field.related_model._meta.db_table}.{target.column}"
    choices = tuple((str(value), str(label)) for value, label in (field.flatchoices or ()))
    return Column(
        name=field.column,
        type=field.db_type(connection) or "",
        label=_custom_label(field.verbose_name, field.name.replace("_", " ")),
        null=field.null,
        primary_key=field.primary_key,
        references=references,
        choices=choices,
    )


def _table(model) -> Table:
    meta = model._meta
    fields = [field for field in meta.concrete_fields]
    columns = tuple(_column(field) for field in fields if not is_secret(field.column))
    return Table(
        name=meta.db_table,
        model=meta.label,
        label=_custom_label(meta.verbose_name, camel_case_to_spaces(model.__name__)),
        doc=_model_doc(model),
        columns=columns,
        has_secrets=len(columns) != len(fields),
    )


@cache
def catalog() -> dict[str, Table]:
    """Таблицы системы по имени; модели строятся один раз на процесс."""
    tables: dict[str, Table] = {}
    for model in apps.get_models(include_auto_created=True):
        meta = model._meta
        if not meta.app_config.name.startswith("apps.") or meta.app_label in _EXCLUDED_APP_LABELS:
            continue
        if meta.proxy or not meta.managed:
            continue
        tables[meta.db_table] = _table(model)
    return dict(sorted(tables.items()))


def table_index() -> str:
    """Короткий список всех таблиц для инструкции модели: имя, модель, назначение."""
    return "\n".join(table.summary() for table in catalog().values())


def _column_line(column: Column) -> str:
    line = f"- {column.name} {column.type}"
    if column.primary_key:
        line += " PK"
    if column.references:
        line += f" → {column.references}"
    if column.null:
        line += " NULL"
    if column.label:
        line += f" — {column.label}"
    if column.choices:
        line += " (варианты: " + ", ".join(f"{value}={label}" for value, label in column.choices) + ")"
    return line


def describe(names: list[str]) -> str:
    """Структура таблиц: докстринг модели и столбцы. Неизвестные имена — подсказкой."""
    tables = catalog()
    blocks = []
    for name in names:
        table = tables.get(name.strip().lower())
        if table is None:
            blocks.append(f"{name}: такой таблицы нет — найдите нужную через search_schema.")
            continue
        lines = [table.header()]
        if table.doc:
            lines.append(table.doc)
        lines.extend(_column_line(column) for column in table.columns)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _stems(text: str) -> set[str]:
    # Грубая основа слова: «долги», «долгов», «долгу» → «долг»; без словарей.
    return {word[:5] for word in _WORD_RE.findall(text.lower()) if len(word) >= 3}


def search(query: str, *, limit: int = 8) -> list[str]:
    """Таблицы, где слова запроса встречаются в имени, докстринге или столбцах."""
    wanted = _stems(query)
    if not wanted:
        return []
    scored = []
    for table in catalog().values():
        score = 3 * len(wanted & _stems(table.name.replace("_", " ") + " " + table.model))
        score += 2 * len(wanted & _stems(table.label + " " + table.doc))
        score += len(wanted & _stems(" ".join(f"{c.name.replace('_', ' ')} {c.label}" for c in table.columns)))
        if score:
            scored.append((score, table.name))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [catalog()[name].summary() for _score, name in scored[:limit]]


def links(names: list[str]) -> list[list[str]]:
    """Внешние ключи между таблицами ``names``: пары [откуда, куда] для карты данных."""
    tables = catalog()
    wanted = set(names)
    pairs = []
    for name in names:
        for column in tables[name].columns if name in tables else ():
            target = column.references.split(".", 1)[0]
            if target in wanted and target != name and [name, target] not in pairs:
                pairs.append([name, target])
    return pairs


def data_map(names: list[str]) -> dict[str, list]:
    """Карта данных для страницы: таблицы каталога из ``names`` по порядку и связи между ними."""
    known = catalog()
    tables = list(dict.fromkeys(name for name in names if name in known))
    return {"tables": tables, "links": links(tables)}
