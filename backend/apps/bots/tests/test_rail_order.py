"""«Отгрузить по отчёту» заранее внесённый вагонный заказ, отчёт о нём в формате владельца и права словарей."""
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.bots.models import BotClientProfile
from apps.bots.parsing import parse_rail_report
from apps.bots.rail import (
    can_conduct,
    can_remember_clients,
    can_remember_products,
    can_ship_by_report,
    conduct_rail_report,
    resolve_order_report,
    ship_order_by_report,
)
from apps.bots.tests.samples import OWNER_BAGS, OWNER_DAY, OWNER_REPORT, OWNER_WAGONS, report
from apps.bots.wagon_report import compose_rail_report
from apps.catalog.models import ProductAlias
from apps.clients.models import Client
from apps.eventlog.models import EventLog
from apps.orders.models import Order, OrderItem
from apps.shipments.models import ShipmentWagon
from apps.warehouse.models import StockItem

pytestmark = pytest.mark.django_db

WAGON_LOADER_CODES = ["loader.view", "loader.confirm", "loader.wagons"]


@pytest.fixture
def wagon_loader(user_with_perms):
    return user_with_perms("wagon-loader", codes=WAGON_LOADER_CODES)


def _manual_order(client, product, *, bags=OWNER_BAGS, status="confirmed", **fields):
    order = Order.objects.create(
        client=client, currency="USD", department="export", transport_type="train", status=status, **fields)
    OrderItem.objects.create(order=order, product=product, quantity=bags, unit_price="7.40")
    return order


def _codes(resolved):
    return [issue.code for issue in resolved.issues]


def _stock(product):
    return StockItem.objects.get(product=product).bags


# --- resolve_order_report / ship_order_by_report -------------------------------------------------


def test_pending_wagon_order_ships_by_report_on_the_report_day(client, product, wagon_loader):
    order = _manual_order(client, product, arrival_date=OWNER_DAY)

    shipped = ship_order_by_report(parse_rail_report(OWNER_REPORT), order, wagon_loader)

    assert shipped.pk == order.pk
    assert Order.objects.count() == 1
    assert (shipped.status, shipped.rail_station) == ("shipped", "Раустан")
    assert [wagon.number for wagon in shipped.shipment.wagons.all()] == list(OWNER_WAGONS)
    assert timezone.localtime(shipped.shipment.shipped_at).date() == OWNER_DAY
    assert _stock(product) == 20000 - OWNER_BAGS
    # Цена заказа остаётся его ценой: сверка с прайсом — только у нового заказа.
    assert shipped.items.get().unit_price == Decimal("7.40")
    assert EventLog.objects.filter(order=order, event_type="shipment").exists()


def test_order_report_takes_client_and_prices_from_the_order(client, product):
    """Незнакомое название клиента и отсутствие прайса не мешают: заказ их уже знает."""
    order = _manual_order(client, product)
    text = OWNER_REPORT.replace("ООО OSIYO NAV NIHOL", "OSIYO (Ташкент)")

    resolved = resolve_order_report(parse_rail_report(text), order)

    assert resolved.ok, _codes(resolved)
    assert (resolved.client, resolved.currency, resolved.department) == (client, "USD", "export")
    (item,) = resolved.items
    assert (item.bags, item.unit_price, item.amount) == (OWNER_BAGS, Decimal("7.40"), Decimal("120768.00"))


def test_order_is_not_its_own_manual_duplicate(client, product):
    order = _manual_order(client, product, arrival_date=OWNER_DAY)

    assert resolve_order_report(parse_rail_report(OWNER_REPORT), order).ok


def test_order_report_for_another_client_needs_review(client, product, department):
    other = Client.objects.create_with_user(
        first_name="Другой", phone="+998 90 000 00 01", company_name="ООО Другой", department=department)
    order = _manual_order(other, product)

    resolved = resolve_order_report(parse_rail_report(OWNER_REPORT), order)

    assert _codes(resolved) == ["client_mismatch"]
    assert "ООО Другой" in resolved.issues[0].message


def test_order_report_locks_the_order_before_the_client(client, product, wagon_loader):
    """Порядок блокировок как везде (lock_live_order, подтверждение, смена отдела) — без взаимоблокировки."""
    order = _manual_order(client, product, arrival_date=OWNER_DAY)

    with CaptureQueriesContext(connection) as queries:
        ship_order_by_report(parse_rail_report(OWNER_REPORT), order, wagon_loader)

    locks = [query["sql"] for query in queries.captured_queries if "FOR UPDATE" in query["sql"]]

    def first(table):
        return next(index for index, sql in enumerate(locks) if f'FROM "{table}"' in sql)

    assert first(Order._meta.db_table) < first(Client._meta.db_table)


def test_order_report_with_other_bags_needs_review_and_ships_nothing(client, product, wagon_loader):
    order = _manual_order(client, product, bags=4080)

    resolved = resolve_order_report(parse_rail_report(OWNER_REPORT), order)
    assert _codes(resolved) == ["rail_bags_mismatch"]
    assert "в заказе 4080, в отчёте 16320" in resolved.issues[0].message

    with pytest.raises(ValidationError) as caught:
        ship_order_by_report(parse_rail_report(OWNER_REPORT), order, wagon_loader)

    assert caught.value.detail["code"] == "rail_report_needs_review"
    order.refresh_from_db()
    assert order.status == "confirmed"
    assert not ShipmentWagon.objects.exists()
    assert _stock(product) == 20000


def test_bags_are_not_compared_while_a_wagon_is_unresolved(client, product):
    order = _manual_order(client, product)
    text = report(*(f"Д1с-{number}-68 тн" for number in OWNER_WAGONS[:11]), f"X9-{OWNER_WAGONS[11]}-68 тн")

    assert _codes(resolve_order_report(parse_rail_report(text), order)) == ["product_unknown"]


@pytest.mark.parametrize(("status", "transport"), [("shipped", "train"), ("pending", "train"), ("confirmed", "truck")])
def test_only_a_waiting_wagon_order_ships_by_report(client, product, status, transport):
    order = _manual_order(client, product, status=status)
    Order.objects.filter(pk=order.pk).update(transport_type=transport)
    order.refresh_from_db()

    assert "order_not_waiting" in _codes(resolve_order_report(parse_rail_report(OWNER_REPORT), order))


def test_recently_shipped_wagon_blocks_the_order_report(client, product, price, conductor, boss):
    conduct_rail_report(parse_rail_report(OWNER_REPORT), conductor)
    order = _manual_order(client, product)

    assert set(_codes(resolve_order_report(parse_rail_report(OWNER_REPORT), order))) == {"wagon_already_shipped"}


def test_shipping_by_report_needs_the_wagon_area(client, product, user_with_perms):
    trucks = user_with_perms("trucks", codes=["loader.view", "loader.confirm", "loader.trucks"])
    order = _manual_order(client, product)

    with pytest.raises(PermissionDenied):
        ship_order_by_report(parse_rail_report(OWNER_REPORT), order, trucks)

    order.refresh_from_db()
    assert order.status == "confirmed"


# --- отчёт в формате владельца («Отправить отчёт») ------------------------------------------------


def _history_rows(*orders):
    return list(
        Order.objects.filter(pk__in=[order.pk for order in orders])
        .select_related("client__user", "shipment")
        .prefetch_related("items__product", "shipment__wagons")
        .order_by("pk")
    )


def test_copied_report_is_the_owner_format_and_parses_back(client, product, price, conductor):
    order = conduct_rail_report(parse_rail_report(OWNER_REPORT), conductor)

    text = compose_rail_report(_history_rows(order)).text

    # Код товара — из словаря в написании владельца («Д1с», а не ключ «Д1C»).
    assert text.splitlines() == [
        "сб 19.09.26 Узбекистан ООО OSIYO NAV NIHOL",
        "Ст. Раустан 12 вагон",
        *(f"Д1с-{number}-68 тн" for number in OWNER_WAGONS),
    ]
    parsed = parse_rail_report(text)
    assert parsed.ok
    assert [wagon.number for wagon in parsed.wagons] == list(OWNER_WAGONS)


def test_copied_report_names_the_client_as_the_report_does(client, product, price, conductor, boss):
    order = conduct_rail_report(parse_rail_report(OWNER_REPORT), conductor)
    BotClientProfile.objects.create(name="OSIYO NAV", client=client, currency="USD", created_by=boss)
    BotClientProfile.objects.create(name="OSIYO KZT", client=client, currency="KZT", created_by=boss)
    ProductAlias.objects.create(code="D1", product=product)

    header, _, first = compose_rail_report(_history_rows(order)).text.splitlines()[:3]

    assert header == "сб 19.09.26 Узбекистан OSIYO NAV"
    assert first.startswith("D1-")


def test_copied_report_falls_back_to_the_product_name_without_a_code(client, product, price, conductor):
    order = conduct_rail_report(parse_rail_report(OWNER_REPORT), conductor)
    ProductAlias.objects.all().delete()

    lines = compose_rail_report(_history_rows(order)).text.splitlines()

    assert lines[2] == f"{product}-{OWNER_WAGONS[0]}-68 тн"


def test_only_shipped_wagon_orders_are_in_the_report(client, product, price, conductor, django_assert_num_queries):
    by_report = conduct_rail_report(parse_rail_report(OWNER_REPORT), conductor)
    manual = _manual_order(client, product)
    truck = _manual_order(client, product, status="shipped")
    Order.objects.filter(pk=truck.pk).update(transport_type="truck")
    rows = _history_rows(by_report, manual, truck)

    # Коды товаров и названия клиентов — по запросу на страницу.
    with django_assert_num_queries(2):
        composed = compose_rail_report(rows)

    assert composed.order_ids == [by_report.pk]
    assert compose_rail_report(_history_rows(manual, truck)).text == ""


# --- права ---------------------------------------------------------------------------------------


def test_rights_of_the_report_sheet(user_with_perms, conductor, wagon_loader):
    catalog = user_with_perms("catalog", codes=["catalog.edit"])
    trucks = user_with_perms(
        "trucks", codes=["orders.create", "orders.confirm", "loader.confirm", "loader.trucks"])

    assert (can_conduct(conductor), can_ship_by_report(conductor)) == (True, True)
    assert (can_conduct(wagon_loader), can_ship_by_report(wagon_loader)) == (False, True)
    assert (can_conduct(trucks), can_ship_by_report(trucks)) == (False, False)
    # Словарь читает бот, который проводит сам: грузчику без прав заказов он закрыт.
    assert (can_remember_products(conductor), can_remember_clients(conductor)) == (True, True)
    assert (can_remember_products(catalog), can_remember_clients(catalog)) == (True, False)
    assert (can_remember_products(wagon_loader), can_remember_clients(wagon_loader)) == (False, False)
    assert (can_conduct(None), can_ship_by_report(None), can_remember_products(None)) == (False, False, False)

