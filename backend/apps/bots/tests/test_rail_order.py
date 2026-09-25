"""«Отгрузить по отчёту» заранее внесённый вагонный заказ, отчёт о нём в формате владельца и права словарей."""
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

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
from apps.bots.tests.samples import (
    OWNER_BAGS,
    OWNER_DAY,
    OWNER_REPORT,
    OWNER_WAGONS,
    issue_codes,
    manual_train_order,
    report,
    stock_bags,
)
from apps.bots.wagon_report import compose_rail_report
from apps.clients.models import Client
from apps.eventlog.models import EventLog
from apps.orders.models import Order
from apps.shipments.models import ShipmentWagon

pytestmark = pytest.mark.django_db


# --- resolve_order_report / ship_order_by_report -------------------------------------------------


def test_pending_wagon_order_ships_by_report_on_the_report_day(client, product, wagon_loader):
    order = manual_train_order(client, product, arrival_date=OWNER_DAY)

    shipped = ship_order_by_report(parse_rail_report(OWNER_REPORT), order, wagon_loader)

    assert shipped.pk == order.pk
    assert Order.objects.count() == 1
    assert (shipped.status, shipped.rail_station) == ("shipped", "Раустан")
    assert [wagon.number for wagon in shipped.shipment.wagons.all()] == list(OWNER_WAGONS)
    assert timezone.localtime(shipped.shipment.shipped_at).date() == OWNER_DAY
    assert stock_bags(product) == 20000 - OWNER_BAGS
    # Цена заказа остаётся его ценой: сверка с прайсом — только у нового заказа.
    assert shipped.items.get().unit_price == Decimal("7.40")
    assert EventLog.objects.filter(order=order, event_type="shipment").exists()


def test_order_report_takes_client_and_prices_from_the_order(client, product):
    """Незнакомое название клиента и отсутствие прайса не мешают: заказ их уже знает."""
    order = manual_train_order(client, product)
    text = OWNER_REPORT.replace("ООО OSIYO NAV NIHOL", "OSIYO (Ташкент)")

    resolved = resolve_order_report(parse_rail_report(text), order)

    assert resolved.ok, issue_codes(resolved)
    assert (resolved.client, resolved.currency, resolved.department) == (client, "USD", "export")
    (item,) = resolved.items
    assert (item.bags, item.unit_price, item.amount) == (OWNER_BAGS, Decimal("7.40"), Decimal("120768.00"))


def test_order_is_not_its_own_manual_duplicate(client, product):
    order = manual_train_order(client, product, arrival_date=OWNER_DAY)

    assert resolve_order_report(parse_rail_report(OWNER_REPORT), order).ok


def test_order_report_for_another_client_needs_review(client, product, department):
    other = Client.objects.create_with_user(
        first_name="Другой", phone="+998 90 000 00 01", company_name="ООО Другой", department=department)
    order = manual_train_order(other, product)

    resolved = resolve_order_report(parse_rail_report(OWNER_REPORT), order)

    assert issue_codes(resolved) == ["client_mismatch"]
    assert "ООО Другой" in resolved.issues[0].message


def test_order_report_locks_the_order_before_the_client(client, product, wagon_loader):
    """Порядок блокировок как везде (lock_live_order, подтверждение, смена отдела) — без взаимоблокировки."""
    order = manual_train_order(client, product, arrival_date=OWNER_DAY)

    with CaptureQueriesContext(connection) as queries:
        ship_order_by_report(parse_rail_report(OWNER_REPORT), order, wagon_loader)

    locks = [query["sql"] for query in queries.captured_queries if "FOR UPDATE" in query["sql"]]

    def first(table):
        return next(index for index, sql in enumerate(locks) if f'FROM "{table}"' in sql)

    assert first(Order._meta.db_table) < first(Client._meta.db_table)


def test_order_report_with_other_bags_needs_review_and_ships_nothing(client, product, wagon_loader):
    order = manual_train_order(client, product, bags=4080)

    resolved = resolve_order_report(parse_rail_report(OWNER_REPORT), order)
    assert issue_codes(resolved) == ["rail_bags_mismatch"]
    assert "в заказе 4080, в отчёте 16320" in resolved.issues[0].message

    with pytest.raises(ValidationError) as caught:
        ship_order_by_report(parse_rail_report(OWNER_REPORT), order, wagon_loader)

    assert caught.value.detail["code"] == "rail_report_needs_review"
    order.refresh_from_db()
    assert order.status == "confirmed"
    assert not ShipmentWagon.objects.exists()
    assert stock_bags(product) == 20000


def test_bags_are_not_compared_while_a_wagon_is_unresolved(client, product):
    order = manual_train_order(client, product)
    text = report(*(f"Д1с-{number}-68 тн" for number in OWNER_WAGONS[:11]), f"X9-{OWNER_WAGONS[11]}-68 тн")

    assert issue_codes(resolve_order_report(parse_rail_report(text), order)) == ["product_unknown"]


@pytest.mark.parametrize(("status", "transport"), [("shipped", "train"), ("pending", "train"), ("confirmed", "truck")])
def test_only_a_waiting_wagon_order_ships_by_report(client, product, status, transport):
    order = manual_train_order(client, product, status=status)
    Order.objects.filter(pk=order.pk).update(transport_type=transport)
    order.refresh_from_db()

    assert "order_not_waiting" in issue_codes(resolve_order_report(parse_rail_report(OWNER_REPORT), order))


def test_recently_shipped_wagon_blocks_the_order_report(client, product, price, conductor, boss):
    conduct_rail_report(parse_rail_report(OWNER_REPORT), conductor)
    order = manual_train_order(client, product)

    assert set(issue_codes(resolve_order_report(parse_rail_report(OWNER_REPORT), order))) == {"wagon_already_shipped"}


def test_shipping_by_report_needs_the_wagon_area(client, product, user_with_perms):
    trucks = user_with_perms("trucks", codes=["loader.view", "loader.confirm", "loader.trucks"])
    order = manual_train_order(client, product)

    with pytest.raises(PermissionDenied):
        ship_order_by_report(parse_rail_report(OWNER_REPORT), order, trucks)

    order.refresh_from_db()
    assert order.status == "confirmed"


# --- отчёт в формате владельца («Отправить отчёт») ------------------------------------------------


def test_copied_report_is_the_owner_format_and_parses_back(client, product, price, conductor):
    """Сквозной путь: проведённый отчёт владельца «Отправить отчёт» возвращает в том же виде."""
    order = conduct_rail_report(parse_rail_report(OWNER_REPORT), conductor)

    text = compose_rail_report([order]).text

    # Код товара — из словаря в написании владельца («Д1с», а не ключ «Д1C»).
    assert text.splitlines() == [
        "сб 19.09.26 Узбекистан ООО OSIYO NAV NIHOL",
        "Ст. Раустан 12 вагон",
        *(f"Д1с-{number}-68 тн" for number in OWNER_WAGONS),
    ]
    parsed = parse_rail_report(text)
    assert parsed.ok
    assert [wagon.number for wagon in parsed.wagons] == list(OWNER_WAGONS)


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

