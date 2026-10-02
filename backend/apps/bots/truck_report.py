"""«Скопировать отчёт» из истории фур у грузчика: отгрузки фур → текст для чата WhatsApp.

Бот этот отчёт не отправляет: грузчик копирует текст одной кнопкой и
вставляет в чат отгрузок. Блок на заказ, как его пишут в этот чат::

    Отгрузка 02.10.26
    kz 909 ERD 13
    Нуржан Сарыагаш 87029368080
    Ат1с(50кг)- 7,5 тн
    Д1с(50кг)- 22,5 тн
    Склад: Мельница, Мельница 2

День — местный день выезда; номер — страна номера (по номеру, иначе по
стране клиента) и номер с прицепом; клиент — как его называют отчёты, и его
телефон; строка на товар — тонны = мешки × фасовка; склады — откуда взяты
мешки (:func:`apps.shipments.sources.loaded_shipment_sources`). Коды товаров
и названия клиентов — те же, что в отчёте о вагонах, одним запросом на все
отгрузки. Несколько заказов — блоки через пустую строку по времени отгрузки.
"""
from __future__ import annotations

from decimal import Decimal

from django.utils import timezone

from apps.clients.phone import chat_phone
from apps.common.plates import client_plate_country, detect_plate_country, format_plate_pair
from apps.shipments.sources import loaded_shipment_sources

from .parsing import KG_PER_TON, format_tons
from .wagon_report import NO_NUMBER, ComposedReport, client_names, product_codes

TRUCK_TRANSPORT = "truck"


def _is_shipped_truck_order(order) -> bool:
    shipment = getattr(order, "shipment", None)
    return (
        order.transport_type == TRUCK_TRANSPORT
        and order.status == "shipped"
        and shipment is not None
        and shipment.shipped_at is not None
    )


def _plate_line(order) -> str:
    """«kz 909 ERD 13 / 07 KG 837 PB»: страна номера строчными, номер и прицеп."""
    if not order.truck_number:
        return NO_NUMBER
    plates = format_plate_pair(order.truck_number, order.trailer_number)
    country = detect_plate_country(order.truck_number) or client_plate_country(order.client.country)
    return f"{country.lower()} {plates}" if country else plates


def _client_line(order, names: dict[tuple[int, str], str]) -> str:
    name = names.get((order.client_id, order.currency)) or order.client.display_name
    return " ".join(part for part in (name, chat_phone(order.client.phone)) if part)


def _product_lines(items, codes: dict[int, str]) -> list[str]:
    """«Д1с(50кг)- 22,5 тн» на товар: строки одного товара и фасовки складываются."""
    tons: dict[tuple[str, Decimal], Decimal] = {}
    for item in sorted(items, key=lambda item: item.pk):
        pack = Decimal(item.product_weight_kg or 0)
        key = (codes.get(item.product_id) or item.product_label, pack)
        tons[key] = tons.get(key, Decimal(0)) + item.quantity * pack / KG_PER_TON
    return [f"{code}({format_tons(pack)}кг)- {format_tons(total)} тн" for (code, pack), total in tons.items()]


def _warehouse_line(order, items) -> str:
    """Склады, с которых ушли мешки; без записанных строк (до опросника, фиксация) — склад заказа."""
    basis, sources = loaded_shipment_sources(order, items)
    warehouses = [source.warehouse for source in sources] if basis == "recorded" else [order.warehouse]
    return "Склад: " + ", ".join(dict.fromkeys(warehouse.name for warehouse in warehouses))


def compose_truck_report(orders) -> ComposedReport:
    """Отчёт по отгруженным фурам (остальные заказы пропускаются).

    ``orders`` — строки истории грузчика: клиент, отгрузка и склад заказа через
    ``select_related``, позиции и строки источников
    (:func:`apps.shipments.sources.sources_prefetch`) — предзагрузкой.
    """
    shipped = sorted(
        (order for order in orders if _is_shipped_truck_order(order)),
        key=lambda order: (order.shipment.shipped_at, order.pk),
    )
    if not shipped:
        return ComposedReport(text="", order_ids=[])
    codes = product_codes(shipped)
    names = client_names(shipped)
    blocks = []
    for order in shipped:
        items = list(order.items.all())
        day = timezone.localtime(order.shipment.shipped_at).date()
        blocks.append("\n".join([
            f"Отгрузка {day:%d.%m.%y}",
            _plate_line(order),
            _client_line(order, names),
            *_product_lines(items, codes),
            _warehouse_line(order, items),
        ]))
    return ComposedReport(text="\n\n".join(blocks), order_ids=[order.pk for order in shipped])
