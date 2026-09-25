from datetime import datetime

from django.utils import timezone
from django.utils.dateparse import parse_datetime


def parse_aware_datetime(value: object) -> datetime | None:
    """ISO-время с часовым поясом из внешнего ответа, иначе ``None``.

    Не строка, неразборная или несуществующая дата (``parse_datetime``
    бросает на ней ``ValueError``) и время без пояса — всё ``None``: свою
    ошибку вызывающий поднимает сам.
    """
    if not isinstance(value, str):
        return None
    try:
        parsed = parse_datetime(value)
    except ValueError:
        return None
    if parsed is None or timezone.is_naive(parsed):
        return None
    return parsed
