"""Транспорт заказа: тягач и полуприцеп фуры либо номер вагона.

Номер машины хранится в ``Order.truck_number`` (у вагона — 8 цифр),
полуприцеп — в ``Order.trailer_number``. Все записи пары идут через
:func:`set_order_transport`: форма заказа, грузчик и
портал клиента сравнивают номера одинаково и не расходятся в правилах.
"""

from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.common.plates import (
    clean_plate,
    format_plate,
    format_plate_pair,
    normalize_plate,
    plate_match_key,
)
from apps.common.wagon_numbers import is_wagon_number
from apps.eventlog.services import log_event
from apps.notifications.services import notify
from apps.shipments.services import has_open_ai_session

from .querysets import recent_transport_pairs
from .services import can_set_truck_number, lock_live_order
from .statuses import ENTERED_POST_STATUSES

# Сколько прошлых пар клиента подсказывать чипами «как в прошлый раз».
TRANSPORT_SUGGESTIONS = 3


def clean_transport_number(value, transport_type: str, *, field: str = "truck_number") -> str:
    """Нормализовать номер и применить жёсткое правило вида транспорта (400)."""
    if transport_type != "train":
        return clean_plate(value, field=field)
    compact = normalize_plate(value)
    if compact and field == "trailer_number":
        raise ValidationError({"trailer_number": "У вагона нет прицепа."})
    if compact and not is_wagon_number(compact):
        raise ValidationError({"truck_number": "Номер вагона должен содержать 8 цифр."})
    return compact


def _changed(current: str, new: str) -> bool:
    return plate_match_key(current) != plate_match_key(new)


def clean_transport_pair(
    transport_type: str, truck, trailer, *, current: tuple[str, str, str] | None = None
) -> tuple[str, str]:
    """Номера пары к записи: изменённые нормализованы и проверены.

    ``current`` — (вид транспорта, тягач, прицеп) сохранённого заказа. Номер,
    который не менялся при том же виде транспорта, остаётся как записан:
    исторические значения старше правил не блокируют несвязанные правки.
    """
    current_type, current_truck, current_trailer = current or (None, "", "")
    same_type = current_type == transport_type
    if same_type and not _changed(current_truck, truck):
        truck = current_truck
    else:
        truck = clean_transport_number(truck, transport_type)
    if same_type and not _changed(current_trailer, trailer):
        trailer = current_trailer
    else:
        trailer = clean_transport_number(trailer, transport_type, field="trailer_number")
    return truck, trailer


def _number_label(order) -> str:
    return "Номер вагона" if order.transport_type == "train" else "Номер машины"


def _owner_refusal(order, *, trailer_only: bool) -> str:
    """Отказ по правилу владельца: кто указал номер и что именно ему не менять."""
    owner = "клиент" if order.truck_number_set_by.is_client else "менеджер"
    if trailer_only:
        return f"Номер транспорта указал {owner} — прицеп к нему тоже указывает {owner}"
    return f"Номер транспорта указал {owner} — изменить его может только {owner}"


def check_transport_change(
    order, user, *, truck=None, trailer=None, ignore_ai_session=False
) -> tuple[str, str] | None:
    """Проверить смену пары, ничего не записывая.

    ``None`` вместо номера — этот номер не трогать. Возвращает ``None``, если
    пара не меняется (совпала с сохранённой с точностью до записи), иначе —
    пару к записи. Правила — те же, что у :func:`set_order_transport`:
    владелец пары (``forbidden``), замок после въезда
    (``truck_number_locked``) и формат номера.

    Грузчик проверяет номер до закрытия AI-подсчёта, чтобы опечатка или чужой
    номер не останавливали сессию камер. Открытую сессию его отгрузка
    закрывает сама — ему ``ignore_ai_session=True``.
    """
    previous = (order.truck_number, order.trailer_number)
    truck = previous[0] if truck is None else truck
    trailer = previous[1] if trailer is None else trailer
    truck_changed = _changed(previous[0], truck)
    if not truck_changed and not _changed(previous[1], trailer):
        return None
    if not can_set_truck_number(order, user):
        raise ValidationError({
            "detail": _owner_refusal(order, trailer_only=not truck_changed),
            "code": "forbidden",
        })
    # Заехавшей машине номер не заменить, а пустой — дописать можно
    # (грузчик вводит номер при отгрузке).
    replaced = any(old and _changed(old, new) for old, new in zip(previous, (truck, trailer)))
    if replaced and (
        order.status in ENTERED_POST_STATUSES
        or (not ignore_ai_session and has_open_ai_session(order))
    ):
        raise ValidationError({
            "detail": f"{_number_label(order)} нельзя изменить после прибытия или начала погрузки",
            "code": "truck_number_locked",
        })
    return clean_transport_pair(
        order.transport_type, truck, trailer, current=(order.transport_type, *previous))


def rail_phrase(wagons: int, station: str = "") -> str:
    """«12 ваг., ст. Раустан» — вагоны отгрузки по отчёту."""
    return f"{wagons} ваг." + (f", ст. {station}" if station else "")


def transport_phrase(order, *, joiner: str = ", ", numbers: bool = True) -> str:
    """«машина 07 KG 695 ADT, прицеп 07 KG 837 PB», «вагон 00123456» или
    «12 ваг., ст. Раустан» у отгрузки по отчёту о вагонах.

    ``numbers=False`` — без номеров машины и вагона: остаётся только фраза
    отчёта о вагонах (номеров в ней нет), иначе пустая строка.
    """
    if order.transport_type == "train":
        from apps.shipments.models import ShipmentWagon

        wagons = ShipmentWagon.objects.filter(shipment__order_id=order.pk).count()
        if wagons:
            return rail_phrase(wagons, order.rail_station)
        if not numbers:
            return ""
        return f"вагон {order.truck_number}" if order.truck_number else "вагон"
    if not numbers:
        return ""
    parts = []
    if order.truck_number:
        parts.append(f"машина {format_plate(order.truck_number)}")
    if order.trailer_number:
        parts.append(f"прицеп {format_plate(order.trailer_number)}")
    return joiner.join(parts)


def transport_number_text(order) -> str:
    """Номер для документов: «07 KG 695 ADT / прицеп 07 KG 837 PB»."""
    return format_plate_pair(order.truck_number, order.trailer_number, joiner=" / прицеп ")


def order_wagons(order) -> list:
    """Вагоны отгрузки по отчёту о вагонах, по порядку отчёта.

    Списки предзагружают ``shipment__wagons`` (orders/querysets.py) — тогда
    это не запрос на строку. У фуры вагонов нет, и база не спрашивается.
    """
    if order.transport_type != "train":
        return []
    shipment = getattr(order, "shipment", None)
    return list(shipment.wagons.all()) if shipment is not None else []


def transport_cell_text(order) -> str:
    """Номер транспорта одной ячейкой таблицы: пара номеров фуры, номер вагона
    или все вагоны отгрузки по отчёту через запятую."""
    wagons = order_wagons(order)
    if wagons:
        return ", ".join(wagon.number for wagon in wagons)
    return format_plate_pair(order.truck_number, order.trailer_number)


def _wants_suggestions(order) -> bool:
    return order.transport_type == "truck" and order.status != "shipped"


def suggestion_pairs(orders) -> dict[int, list[dict]]:
    """Подсказки номеров для страницы заказов — один запрос на страницу.

    Результат передаётся сериализатору через context (``transport_pairs``).
    """
    client_ids = {order.client_id for order in orders if _wants_suggestions(order)}
    return recent_transport_pairs(client_ids, limit=TRANSPORT_SUGGESTIONS + 1)


def transport_suggestions(order, pairs_by_client) -> list[dict]:
    """Прошлые пары клиента для заказа, кроме той, что уже записана в нём."""
    if not _wants_suggestions(order):
        return []
    own = (plate_match_key(order.truck_number), plate_match_key(order.trailer_number))
    return [
        pair for pair in pairs_by_client.get(order.client_id, ())
        if (plate_match_key(pair["truck_number"]), plate_match_key(pair["trailer_number"])) != own
    ][:TRANSPORT_SUGGESTIONS]


def transport_on_site(order) -> bool:
    """Машина уже на территории (заехала, грузится, отгружена): номер закреплён."""
    return order.status in ENTERED_POST_STATUSES


def client_transport_phrase(order, *, joiner: str = ", ") -> str:
    """Транспорт в уведомлении клиенту: машина уже на территории — без номеров.

    Решение владельца: после заезда клиент номера не видит нигде — ни в
    портале (``PortalOrderSerializer``), ни в уведомлениях.
    """
    return transport_phrase(order, joiner=joiner, numbers=not transport_on_site(order))


def transport_locked(order, user) -> bool:
    """Номер виден только для чтения: его задал другой владелец или машина уже заехала."""
    return transport_on_site(order) or not can_set_truck_number(order, user)


@transaction.atomic
def set_order_transport(
    order, user, *, truck=None, trailer=None, notify_client=True, any_department=False
):
    """Записать номер тягача и прицепа. Возвращает ``(order, changed)``.

    ``None`` вместо номера — этот номер не трогать. Пара, совпавшая с
    сохранённой с точностью до записи (пробелы, регистр, «KG»), — не смена:
    ничего не пишется, владелец номера не меняется. Номер меняет только тот,
    кто его ввёл (клиент или сотрудник), см. ``can_set_truck_number``.
    """
    # Serialize the number with loading. A stale form or a second portal tab
    # must not retag a truck after its physical workflow has started. Match AI
    # start: lock the parent first, then observe a reserved session.
    caller_order = order
    order = lock_live_order(order, user, any_department=any_department)
    previous = (order.truck_number, order.trailer_number)
    pair = check_transport_change(order, user, truck=truck, trailer=trailer)
    if pair is None:
        return order, False
    truck, trailer = pair
    order.truck_number = truck
    order.trailer_number = trailer
    order.truck_number_set_by = user
    order.save(update_fields=["truck_number", "trailer_number", "truck_number_set_by"])
    # Preserve the existing service contract: callers historically observed
    # their passed instance updated without having to use the return value.
    caller_order.truck_number = truck
    caller_order.trailer_number = trailer
    caller_order.truck_number_set_by = user
    log_event(
        "status",
        f"{_number_label(order)}: {transport_number_text(order) or '—'}",
        user=user,
        order=order,
        payload={
            "truck": truck,
            "trailer": trailer,
            "previous": {"truck": previous[0], "trailer": previous[1]},
        },
    )
    # Машина уже на территории — номер клиенту не показывается (решение
    # владельца), значит и о дописанном номере ему не пишем.
    if notify_client and user is not None and not user.is_client and not transport_on_site(order):
        notify(order.client, f"Заказ №{order.pk}: {client_transport_phrase(order) or 'номер транспорта снят'}")
    return order, True
