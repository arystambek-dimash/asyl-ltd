"""Отчёт владельца (встреча 23.09) — общий пример для тестов разбора и проведения — и вагонные заказы под него."""
from datetime import date

from apps.orders.models import Order, OrderItem
from apps.sales.models import Department
from apps.shipments.models import Shipment, ShipmentWagon
from apps.warehouse.models import StockItem

OWNER_DAY = date(2026, 9, 19)
OWNER_BAGS = 16320  # 12 вагонов × 68 т мешками по 50 кг
# Провести отчёт: создать и подтвердить заказ и отгрузить вагоны.
CONDUCT_CODES = ["orders.view", "orders.create", "orders.confirm", "loader.view", "loader.confirm", "loader.wagons"]

# 12 вагонов с верной контрольной цифрой; первый — из отчёта владельца.
OWNER_WAGONS = (
    "28087658", "28087666", "28087674", "28087682", "28087690", "28087708",
    "28087716", "28087724", "28087732", "28087740", "28087757", "28087765",
)

# Строки как в WhatsApp: кириллические «ООО» и «Д1с», два пробела после страны.
OWNER_REPORT = "\n".join(
    [
        "сб 19.09.26 Узбекистан  ООО OSIYO NAV NIHOL",
        "Ст. Раустан 12 вагон",
        *(f"Д1с-{number}-68 тн" for number in OWNER_WAGONS),
    ]
)


def report(*wagon_lines: str, header="сб 19.09.26 Узбекистан  ООО OSIYO NAV NIHOL", station=None) -> str:
    """Отчёт с заданными строками вагонов; станция по умолчанию — с их числом."""
    station = f"Ст. Раустан {len(wagon_lines)} вагон" if station is None else station
    return "\n".join(line for line in (header, station, *wagon_lines) if line is not None)


def issue_codes(result, kind="issues"):
    """Коды замечаний разбора или сверки отчёта (``kind="warnings"`` — предупреждений)."""
    return [issue.code for issue in getattr(result, kind)]


def stock_bags(product):
    return StockItem.objects.get(product=product).bags


def train_order(client, product, *, bags=None, unit_price="7.50", shipped_at=None, wagons=(), **fields):
    """Вагонный заказ экспорта в USD на один товар («отгружен», если не сказано иное), без проведения.

    С ``shipped_at`` — с отгрузкой и её вагонами по 68 т (1360 мешков); мешков
    по умолчанию — по вагонам, без вагонов — как в отчёте владельца.
    """
    if bags is None:
        bags = 1360 * len(wagons) or OWNER_BAGS
    order = Order.objects.create(
        client=client, **{"currency": "USD", "department": "export", "transport_type": "train", "status": "shipped",
                          **fields})
    OrderItem.objects.create(order=order, product=product, quantity=bags, unit_price=unit_price)
    if shipped_at is not None:
        shipment = Shipment.objects.create(order=order, bags_loaded=bags, shipped_at=shipped_at)
        for position, number in enumerate(wagons, start=1):
            ShipmentWagon.objects.create(
                shipment=shipment, number=number, product=product, bags=1360, weight_kg="68000", position=position)
    return order


def manual_train_order(client, product, **fields):
    """Заранее внесённый вагонный заказ: подтверждён, ждёт отгрузки, цена 7,40 — не из прайса."""
    return train_order(client, product, unit_price="7.40", **{"status": "confirmed", **fields})


def move_to_retail(user):
    """Сотрудник отдела «Розница»: клиент отчёта (отдел «Экспорт») ему чужой."""
    department = Department.objects.create(code="retail", name="Розница")
    user.employee.sales_department = department
    user.employee.save()
    return department
