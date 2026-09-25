"""Строгий разбор UUID, которыми CRM и ПК камер помечают одну операцию."""

from uuid import UUID


def parse_canonical_uuid(raw) -> UUID | None:
    """UUID только в канонической записи (строчные hex с дефисами), иначе None.

    ``UUID()`` принимает и верхний регистр, и фигурные скобки, и запись без
    дефисов; ключ идемпотентности должен совпадать с сохранённым посимвольно.
    """
    if not isinstance(raw, str):
        return None
    try:
        value = UUID(raw)
    except ValueError:
        return None
    return value if str(value) == raw else None
