"""Отгрузка вагонного заказа по отчёту: вагоны, склад, долг и дата отчёта."""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.eventlog.models import EventLog
from apps.orders.models import Order, OrderItem
from apps.shipments.models import Shipment, ShipmentWagon
from apps.shipments.services import RailWagon, loader_rollback_blocker, rollback_shipment, ship_rail_report
from apps.warehouse.models import StockItem
from apps.warehouse.services import receive_stock

pytestmark = pytest.mark.django_db

BAGS_PER_WAGON = 1360  # 68 т мешками по 50 кг


@pytest.fixture
def wagons_loader(user_with_perms):
    return user_with_perms("wagons-loader", codes=["loader.view", "loader.confirm", "loader.wagons"])


@pytest.fixture
def product(boss):
    item = Product.objects.create(name="Д1с", color="Red", weight_kg="50")
    receive_stock(item, 10000, boss)
    return item


def _order(product, *, bags=2 * BAGS_PER_WAGON, transport_type="train", status="confirmed"):
    client = Client.objects.create_with_user(first_name="Osiyo", phone="+998 90 000", company_name="ООО OSIYO NAV NIHOL")
    order = Order.objects.create(client=client, status=status, transport_type=transport_type, currency="USD")
    OrderItem.objects.create(order=order, product=product, quantity=bags, unit_price="7.50")
    return order


def _wagons(product, *numbers, bags=BAGS_PER_WAGON):
    return [RailWagon(number=number, product=product, bags=bags, weight_kg=Decimal("68000")) for number in numbers]


def _stock(product):
    return StockItem.objects.get(product=product).bags


def _local_date(value):
    return timezone.localtime(value).date()


def test_report_ships_the_order_with_its_wagons(product, wagons_loader):
    order = _order(product)

    shipment = ship_rail_report(
        order, _wagons(product, "28087658", "28087666"), wagons_loader,
        station="Раустан", shipped_day=timezone.localdate(),
    )

    order.refresh_from_db()
    assert order.status == "shipped"
    assert order.rail_station == "Раустан"
    assert order.truck_number == ""
    assert order.payment_status == "unpaid"
    assert _stock(product) == 10000 - 2 * BAGS_PER_WAGON
    assert shipment.bags_loaded == 2 * BAGS_PER_WAGON
    assert _local_date(shipment.shipped_at) == timezone.localdate()
    wagons = list(ShipmentWagon.objects.filter(shipment=shipment))
    assert [(w.position, w.number, w.bags, w.weight_kg) for w in wagons] == [
        (1, "28087658", BAGS_PER_WAGON, Decimal("68000.00")),
        (2, "28087666", BAGS_PER_WAGON, Decimal("68000.00")),
    ]
    assert wagons[0].product == product
    assert wagons[0].product_label == str(product)
    assert wagons[0].product_weight_kg_snapshot == Decimal("50.00")
    # Долг — остаток отгруженного заказа, событие — на его сумму.
    debt = EventLog.objects.get(order=order, event_type="debt")
    assert debt.payload["amount"] == "20400.00"
    shipped = EventLog.objects.get(order=order, event_type="shipment")
    assert "2 ваг." in shipped.message and "ст. Раустан" in shipped.message
    assert shipped.payload["bags_loaded"] == 2 * BAGS_PER_WAGON


def test_yesterdays_report_backdates_shipment_and_its_events(product, wagons_loader):
    order = _order(product)
    day = timezone.localdate() - timedelta(days=4)

    shipment = ship_rail_report(
        order, _wagons(product, "28087658", "28087666"), wagons_loader, station="Раустан", shipped_day=day,
    )

    assert _local_date(shipment.shipped_at) == day
    assert timezone.localtime(shipment.shipped_at).hour == 12
    for event_type in ("shipment", "debt"):
        assert _local_date(EventLog.objects.get(order=order, event_type=event_type).created_at) == day
    # Склад списан по-настоящему: вагон уехал, это не историческая фиксация.
    assert _stock(product) == 10000 - 2 * BAGS_PER_WAGON


@pytest.mark.parametrize(
    ("days_ago", "blocker"), [(0, ""), (1, "Прошло больше часа — отмену оформляет старший в «Заказах»")])
def test_loader_cancels_own_report_shipment_only_when_it_is_dated_today(product, wagons_loader, days_ago, blocker):
    """Окно грузчика — от даты отгрузки: вчерашний отчёт отменяет старший, как и фиксацию."""
    order = _order(product)
    ship_rail_report(
        order, _wagons(product, "28087658", "28087666"), wagons_loader,
        station="Раустан", shipped_day=timezone.localdate() - timedelta(days=days_ago),
    )

    assert loader_rollback_blocker(Order.objects.get(pk=order.pk), wagons_loader) == blocker


def test_report_bags_must_match_a_prepared_order(product, wagons_loader):
    order = _order(product, bags=3 * BAGS_PER_WAGON)

    with pytest.raises(ValidationError) as caught:
        ship_rail_report(
            order, _wagons(product, "28087658", "28087666"), wagons_loader,
            station="Раустан", shipped_day=timezone.localdate(),
        )

    assert caught.value.detail["code"] == "rail_bags_mismatch"
    assert "в заказе 4080, в отчёте 2720" in str(caught.value.detail["detail"])
    order.refresh_from_db()
    assert order.status == "confirmed"
    assert _stock(product) == 10000
    assert not ShipmentWagon.objects.exists()


def test_report_product_missing_from_order_is_a_mismatch(product, boss, wagons_loader):
    other = Product.objects.create(name="Д2", color="Blue", weight_kg="50")
    receive_stock(other, 5000, boss)
    order = _order(product, bags=BAGS_PER_WAGON)

    with pytest.raises(ValidationError) as caught:
        ship_rail_report(
            order, _wagons(product, "28087658") + _wagons(other, "28087666"), wagons_loader,
            station="Раустан", shipped_day=timezone.localdate(),
        )

    assert caught.value.detail["code"] == "rail_bags_mismatch"
    assert "в заказе 0, в отчёте 1360" in str(caught.value.detail["detail"])


def test_prepared_order_keeps_its_station_when_the_report_has_none(product, wagons_loader):
    order = _order(product)
    order.rail_station = "Раустан"
    order.save()

    ship_rail_report(
        order, _wagons(product, "28087658", "28087666"), wagons_loader,
        station=" ", shipped_day=timezone.localdate(),
    )

    order.refresh_from_db()
    assert order.rail_station == "Раустан"


def test_trucks_loader_cannot_ship_wagons(product, user_with_perms):
    trucks_loader = user_with_perms("trucks-loader", codes=["loader.view", "loader.confirm", "loader.trucks"])
    order = _order(product)

    with pytest.raises(PermissionDenied):
        ship_rail_report(
            order, _wagons(product, "28087658", "28087666"), trucks_loader,
            station="Раустан", shipped_day=timezone.localdate(),
        )

    order.refresh_from_db()
    assert order.status == "confirmed"
    assert _stock(product) == 10000


@pytest.mark.parametrize(
    ("fields", "code"),
    [
        ({"transport_type": "truck"}, "wrong_transport"),
        ({"status": "pending"}, "invalid_status"),
        ({"status": "shipped"}, "invalid_status"),
    ],
)
def test_only_an_awaiting_wagon_order_ships_by_report(product, operator, fields, code):
    # Обе области: фуру отчёт не отгружает не из-за прав, а по виду транспорта.
    order = _order(product, **fields)

    with pytest.raises(ValidationError) as caught:
        ship_rail_report(
            order, _wagons(product, "28087658", "28087666"), operator,
            station="Раустан", shipped_day=timezone.localdate(),
        )

    assert caught.value.detail["code"] == code


@pytest.mark.parametrize(
    ("numbers", "day_shift", "code"),
    [
        (("28087658", "28087658"), 0, "rail_duplicate_wagon"),
        (("28087658", "28087666"), 1, "rail_future_day"),
        ((), 0, "rail_no_wagons"),
    ],
)
def test_report_wagons_and_day_are_validated(product, wagons_loader, numbers, day_shift, code):
    order = _order(product)

    with pytest.raises(ValidationError) as caught:
        ship_rail_report(
            order, _wagons(product, *numbers), wagons_loader,
            station="Раустан", shipped_day=timezone.localdate() + timedelta(days=day_shift),
        )

    assert caught.value.detail["code"] == code
    assert not Shipment.objects.filter(order=order).exists()


def test_rollback_removes_the_wagons(product, wagons_loader, boss):
    order = _order(product)
    ship_rail_report(
        order, _wagons(product, "28087658", "28087666"), wagons_loader,
        station="Раустан", shipped_day=timezone.localdate(),
    )

    rollback_shipment(order, boss, target_status="confirmed", reason="Не тот клиент")

    assert not ShipmentWagon.objects.exists()
    assert _stock(product) == 10000


def test_wagon_number_is_eight_digits_in_the_database(product):
    shipment = Shipment.objects.create(order=_order(product))

    with pytest.raises(IntegrityError), transaction.atomic():
        ShipmentWagon.objects.create(
            shipment=shipment, number="2808765", product=product, bags=1, weight_kg="50", position=1)
