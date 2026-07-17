"""Typed validation for query parameters shared by API read endpoints."""

from datetime import date

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


def parse_store_id(raw: str | None) -> int | None:
    """Parse an optional ?store= id while preserving the public error contract."""
    if not raw:
        return None
    if not raw.isdigit():
        raise ValidationError({"detail": "Некорректный магазин", "code": "bad_store"})
    return int(raw)
