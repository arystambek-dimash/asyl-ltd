"""Области грузчика: фуры и вагоны отгружают разные люди.

Страница (loader.view) и кнопка «Отгружено» (loader.confirm) — общие, а какой
транспорт человек видит и отгружает, решает область: loader.trucks или
loader.wagons. Проверку отгрузки вызывают сервисы под блокировкой заказа —
её не обойти другим эндпоинтом, ботом или скриптом.
"""

from rest_framework.exceptions import PermissionDenied, ValidationError

# Вид транспорта заказа -> право области.
TRANSPORT_PERMISSIONS = {"truck": "loader.trucks", "train": "loader.wagons"}
_AREA_NAMES = {"truck": "фур", "train": "вагонов"}


def allowed_transports(user) -> tuple[str, ...]:
    """Виды транспорта, открытые грузчику; суперпользователю — все."""
    return tuple(kind for kind, code in TRANSPORT_PERMISSIONS.items() if user.has_perm_code(code))


def _refuse_area(transport_type: str) -> PermissionDenied:
    return PermissionDenied(f"Отгрузка {_AREA_NAMES.get(transport_type, 'этого транспорта')} вам не открыта")


def assert_can_ship(user, order) -> None:
    """Отгружает грузчик с кнопкой (loader.confirm) и областью транспорта заказа.

    ``user=None`` — системная автоматика камер: у неё нет областей.
    """
    assert_can_ship_transport(user, order.transport_type)


def assert_can_ship_transport(user, transport_type: str) -> None:
    """:func:`assert_can_ship` до появления заказа — например, перед созданием
    вагонного заказа по отчёту, чтобы отказ пришёл раньше записи."""
    if user is None:
        return
    if not user.has_perm_code("loader.confirm"):
        raise PermissionDenied("Нет права отгружать заказы")
    if transport_type not in allowed_transports(user):
        raise _refuse_area(transport_type)


def requested_transport(user, raw) -> str | None:
    """Вкладка «Фуры | Вагоны» из ``?transport=``; пусто — все открытые области.

    Вкладка чужой области — отказ, а не пустой список: экран не должен
    выдавать «всё отгружено», когда человеку просто не открыт этот транспорт.
    """
    if not raw:
        return None
    if raw not in TRANSPORT_PERMISSIONS:
        raise ValidationError({"transport": "Укажите truck или train"})
    if raw not in allowed_transports(user):
        raise _refuse_area(raw)
    return raw
