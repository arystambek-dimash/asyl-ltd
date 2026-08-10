"""Typed validation for query parameters shared by API read endpoints."""

from datetime import date
from decimal import Decimal, InvalidOperation

from rest_framework.exceptions import ValidationError


def parse_iso_date(raw: str | None) -> date | None:
    """Parse an optional ISO date while preserving the public API error contract."""
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise ValidationError(
            {"detail": "Дата в формате ГГГГ-ММ-ДД", "code": "bad_date"}
        ) from exc


def validate_date_range(date_from: date | None, date_to: date | None) -> None:
    if date_from and date_to and date_from > date_to:
        raise ValidationError(
            {"detail": "Начало периода позже конца", "code": "bad_range"}
        )


def parse_date_range(params) -> tuple[date | None, date | None]:
    """Разобрать период из query-параметров и сразу проверить его.

    Исторически сводный отчёт принимает ``from``/``to``, а остальные списки —
    ``date_from``/``date_to``. Оба написания читаются везде: старые ссылки
    продолжают работать, а ``?date_from=`` больше не игнорируется молча.
    """
    date_from = parse_iso_date(params.get("date_from") or params.get("from"))
    date_to = parse_iso_date(params.get("date_to") or params.get("to"))
    validate_date_range(date_from, date_to)
    return date_from, date_to


def filter_date_range(queryset, field: str, date_from, date_to):
    """Ограничить queryset календарным периодом по полю ``field``.

    Сравнение идёт через ``__date``, поэтому граничные дни входят целиком
    независимо от времени в колонке.
    """
    if date_from:
        queryset = queryset.filter(**{f"{field}__date__gte": date_from})
    if date_to:
        queryset = queryset.filter(**{f"{field}__date__lte": date_to})
    return queryset


def parse_money_param(raw: str | None, name: str) -> Decimal | None:
    """Parse an optional, finite, non-negative monetary query parameter."""
    if raw in (None, ""):
        return None
    try:
        value = Decimal(raw)
        if not value.is_finite():
            raise InvalidOperation
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError(
            {
                "detail": f"Некорректное значение: {name}",
                "code": "bad_amount",
            }
        ) from exc
    if value < 0:
        raise ValidationError(
            {
                "detail": f"{name} не может быть меньше нуля",
                "code": "bad_amount",
            }
        )
    return value


def parse_store_id(raw: str | None) -> int | None:
    """Parse an optional ?store= id while preserving the public error contract."""
    if not raw:
        return None
    if not raw.isdigit():
        raise ValidationError({"detail": "Некорректный магазин", "code": "bad_store"})
    return int(raw)
