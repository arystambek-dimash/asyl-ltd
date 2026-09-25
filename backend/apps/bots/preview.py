"""Предпросмотр отчёта о вагонах для «Вставить отчёт» у грузчика — ничего не пишет.

Экран показывает то же, что проведёт :func:`apps.bots.rail.conduct_rail_report`
(или :func:`apps.bots.rail.ship_order_by_report` для заранее внесённого
заказа): вагоны с контрольной цифрой, товар, тонны, мешки, цену и сумму в
валюте клиента, причины разбора и что можно разрешить прямо здесь.
"""
from decimal import Decimal

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.common.money import money_string
from apps.orders.models import Order
from apps.orders.statuses import AWAITING_SHIPMENT_STATUSES
from apps.sales.access import scope_by_client_department

from .parsing import RailReport, decimal_string, wagon_number_status
from .rail import (
    CLIENT_OTHER_DEPARTMENT,
    MANUAL_ORDER_DUPLICATE,
    RAIL_TRANSPORT,
    ResolvedReport,
    can_conduct,
    can_remember_clients,
    can_remember_products,
    can_ship_by_report,
    resolve_order_report,
    resolve_report,
)

# Причины, которые снимает выбор клиента и валюты или товара для кода.
CLIENT_ISSUES = ("client_unknown", "client_ambiguous")
PRODUCT_ISSUES = ("product_unknown", "product_archived")


def _money(value: Decimal | None) -> str | None:
    return None if value is None else money_string(value)


def _shippable_duplicates(resolved: ResolvedReport) -> list[int]:
    """Похожие ручные заказы, которые ещё ждут отгрузки: их можно отгрузить по этому отчёту.

    Уже отгруженный вручную заказ — только предупреждение: «Отгрузить по
    отчёту» ему нечего.
    """
    ids = [issue.order_id for issue in resolved.issues if issue.code == MANUAL_ORDER_DUPLICATE and issue.order_id]
    if not ids:
        return []
    waiting = set(
        Order.objects.filter(
            pk__in=ids, transport_type=RAIL_TRANSPORT, status__in=AWAITING_SHIPMENT_STATUSES,
        ).values_list("pk", flat=True)
    )
    return [pk for pk in ids if pk in waiting]


def report_preview(resolved: ResolvedReport, user, *, order=None) -> dict:
    """Ответ предпросмотра: вагоны, позиции, итог, причины и права экрана.

    ``order`` — «Отгрузить по отчёту» заранее внесённый заказ: клиент и цены
    уже заданы им, поэтому клиента здесь не выбирают.
    """
    report = resolved.report
    shipped = {wagon.number: wagon for wagon in resolved.wagons}
    prices = {item.product.pk: item.unit_price for item in resolved.items}
    wagons = []
    for line in report.wagons:
        product = resolved.products.get(line.code_key)
        price = prices.get(product.pk) if product is not None else None
        rail = shipped.get(line.number)
        bags = rail.bags if rail is not None else None
        wagons.append({
            "position": line.position,
            "line": line.line,
            "number": line.number,
            "number_status": wagon_number_status(line.number),
            "code": line.code,
            "product_id": product.pk if product is not None else None,
            "product_label": str(product) if product is not None else "",
            "tons": decimal_string(line.tons),
            "bags": bags,
            "unit_price": _money(price),
            "amount": _money(price * bags if price is not None and bags is not None else None),
        })
    items = [
        {
            "product_id": item.product.pk,
            "product_label": str(item.product),
            "code": item.code,
            "wagons": item.wagons,
            "bags": item.bags,
            "unit_price": _money(item.unit_price),
            "amount": _money(item.amount),
            "reference_price": _money(item.reference_price),
            "reference_order_id": item.reference_order_id,
        }
        for item in resolved.items
    ]
    amounts = [item.amount for item in resolved.items]
    # Сумма — только когда распознан каждый вагон и у каждого товара есть цена:
    # частичная сумма выглядела бы итогом отчёта. Валюта одна на отчёт.
    priced = bool(amounts) and None not in amounts and len(resolved.wagons) == len(report.wagons)
    codes = {issue.code for issue in resolved.issues}
    client = resolved.client
    can_apply = resolved.ok and (can_ship_by_report(user) if order is not None else can_conduct(user))
    return {
        "order_id": order.pk if order is not None else None,
        "day": report.day.isoformat() if report.day else None,
        "country": report.country,
        "client_name": report.client_name,
        "station": report.station,
        "declared_wagons": report.declared_wagons,
        "client": (
            {"id": client.pk, "name": client.display_name, "profile": resolved.profile is not None}
            if client is not None
            else None
        ),
        "currency": resolved.currency,
        "wagons": wagons,
        "items": items,
        "totals": {
            "wagons": len(report.wagons),
            "tons": decimal_string(report.total_tons),
            "bags": resolved.total_bags,
            "amount": _money(sum(amounts, Decimal("0"))) if priced else None,
            "currency": resolved.currency,
        },
        "issues": [issue.as_dict() for issue in resolved.issues],
        "warnings": [issue.as_dict() for issue in resolved.warnings],
        "unresolved": {
            "client": report.client_name if codes & set(CLIENT_ISSUES) else "",
            "products": [issue.subject for issue in resolved.issues if issue.code in PRODUCT_ISSUES],
        },
        # «Отгрузить заказ №N по этому отчёту» — только у ждущих отгрузки.
        "shippable_orders": _shippable_duplicates(resolved) if order is None else [],
        "ok": resolved.ok,
        "can_apply": can_apply,
        "can_remember_products": can_remember_products(user),
        # У заранее внесённого заказа клиент уже выбран; название клиента
        # другого отдела перенаправляет только его отдел.
        "can_remember_clients": (
            order is None and CLIENT_OTHER_DEPARTMENT not in codes and can_remember_clients(user)
        ),
    }


def preview_report(report: RailReport, user, *, order=None) -> dict:
    """Предпросмотр для ``user``: новый отчёт или «Отгрузить по отчёту» заказ ``order``.

    Окно дублей и допуск цены — из настроек бота, как при проведении.
    """
    if order is not None:
        resolved = resolve_order_report(report, order)
    else:
        resolved = resolve_report(report, user=user)
    return report_preview(resolved, user, order=order)


def resolution_options(user) -> dict:
    """Из чего выбирать на экране: товары для кода и клиенты для названия.

    Только то, что человеку можно запомнить; клиенты — своего отдела.
    """
    products = (
        [
            {"id": product.pk, "label": str(product), "weight_kg": money_string(product.weight_kg)}
            for product in Product.objects.filter(is_active=True).order_by("name", "weight_kg", "id")
        ]
        if can_remember_products(user)
        else []
    )
    clients = (
        [
            {
                "id": client.pk,
                "name": client.display_name,
                "currency": client.currency,
                "department_name": client.department.name if client.department else "",
            }
            for client in scope_by_client_department(
                Client.objects.select_related("user", "department"), user,
            ).order_by("company_name", "user__first_name", "id")
        ]
        if can_remember_clients(user)
        else []
    )
    return {"products": products, "clients": clients}
