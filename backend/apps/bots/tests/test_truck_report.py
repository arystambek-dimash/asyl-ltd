"""«Скопировать отчёт» в истории фур: текст отгрузки, как его пишут в чат WhatsApp."""
from datetime import date, datetime, time

import pytest
from django.utils import timezone

from apps.bots.models import BotClientProfile
from apps.bots.tests.samples import train_order, truck_order
from apps.bots.truck_report import compose_truck_report
from apps.catalog.models import Product, ProductAlias
from apps.clients.models import Client
from apps.orders.models import Order
from apps.shipments.sources import sources_prefetch
from apps.warehouse.models import Warehouse
from apps.warehouse.services import receive_stock

pytestmark = pytest.mark.django_db

DAY = date(2026, 10, 2)


def _at(day=DAY, hour=12, minute=0):
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


@pytest.fixture
def first_grade():
    """«Ат1с» — второй товар отгрузки."""
    item = Product.objects.create(name="Мука первый сорт", color="Blue", weight_kg="50")
    ProductAlias.objects.create(code="Ат1с", product=item)
    return item


@pytest.fixture
def mill():
    warehouse = Warehouse.objects.get(code="main")
    warehouse.name = "Мельница"
    warehouse.save(update_fields=["name"])
    return warehouse


@pytest.fixture
def mill_two():
    return Warehouse.objects.create(code="mill-2", name="Мельница 2")


def _truck(client, *lines, at=None, **fields):
    return truck_order(client, *lines, shipped_at=at or _at(), **fields)


def _rows(*orders):
    """Заказы, как их читает история грузчика: склад, позиции и источники — запросами на всю страницу."""
    return list(
        Order.objects.filter(pk__in=[order.pk for order in orders])
        .select_related("client__user", "shipment", "warehouse")
        .prefetch_related("items__product", "shipment__wagons", sources_prefetch())
        .order_by("-pk")
    )


def _text(*orders):
    return compose_truck_report(_rows(*orders)).text


def test_report_is_written_like_the_shipping_chat(nurzhan, product, first_grade, mill, mill_two):
    order = _truck(
        nurzhan, (first_grade, 150), (product, 450),
        sources=((first_grade, mill, 150), (product, mill, 200), (product, mill_two, 250)))

    assert _text(order) == "\n".join([
        "Отгрузка 02.10.26",
        "kz 909 ERD 13",
        "Нуржан Сарыагаш 87029368080",
        "Ат1с(50кг)- 7,5 тн",
        "Д1с(50кг)- 22,5 тн",
        "Склад: Мельница, Мельница 2",
    ])


def test_day_is_the_local_day_of_shipment(nurzhan, product):
    """Выехала в 00:30 по Алматы — это уже 2 октября, хотя в UTC ещё 1-е."""
    order = _truck(nurzhan, (product, 20), at=_at(hour=0, minute=30))

    assert _text(order).splitlines()[0] == "Отгрузка 02.10.26"


def test_plate_with_trailer_and_country_by_plate_or_by_client(client, nurzhan, product):
    kg_pair = _truck(nurzhan, (product, 20), truck="07KG695ADT", trailer="07KG837PB")
    by_client = _truck(client, (product, 20), truck="01123ABC")
    unknown = _truck(nurzhan, (product, 20), truck="CLIENT777")
    no_number = _truck(nurzhan, (product, 20), truck="", trailer="07KG837PB")

    assert _text(kg_pair).splitlines()[1] == "kg 07 KG 695 ADT / 07 KG 837 PB"
    # Номер неоднозначный (юрлицо Узбекистана или киргизский без «KG») — страна клиента.
    assert _text(by_client).splitlines()[1] == "uz 01 123 ABC"
    # Незнакомый номер у клиента из Казахстана — с его страной, как есть.
    assert _text(unknown).splitlines()[1] == "kz CLIENT777"
    assert _text(no_number).splitlines()[1] == "без номера"


def test_plate_without_any_country_has_no_prefix(nurzhan, product):
    Client.objects.filter(pk=nurzhan.pk).update(country="Таджикистан")

    assert _text(_truck(nurzhan, (product, 20), truck="CLIENT777")).splitlines()[1] == "CLIENT777"


def test_client_is_named_as_reports_name_it_with_a_chat_phone(client, nurzhan, product, boss):
    BotClientProfile.objects.create(name="Нуржан Сарыагаш опт", client=nurzhan, currency="KZT", created_by=boss)
    BotClientProfile.objects.create(name="Нуржан USD", client=nurzhan, currency="USD", created_by=boss)
    no_phone = Client.objects.create_with_user(first_name="Ерлан", phone="", company_name="ИП Ерлан")

    assert _text(_truck(nurzhan, (product, 20))).splitlines()[2] == "Нуржан Сарыагаш опт 87029368080"
    # Не Казахстан — с кодом страны; профиля нет — название из карточки.
    assert _text(_truck(client, (product, 20))).splitlines()[2] == "ООО OSIYO NAV NIHOL +998901112233"
    assert _text(_truck(no_phone, (product, 20))).splitlines()[2] == "ИП Ерлан"


def test_product_code_falls_back_to_its_name_and_numbers_have_no_trailing_zeros(nurzhan, product):
    bran = Product.objects.create(name="Отруби", color="Green", weight_kg="25")
    sample = Product.objects.create(name="Образец", color="Red", weight_kg="2")
    ProductAlias.objects.create(code="D1", spelling="Д-1с", product=product)

    order = _truck(nurzhan, (product, 20), (bran, 41), (sample, 33))

    assert _text(order).splitlines()[3:6] == [
        "Д-1с(50кг)- 1 тн",
        f"{bran}(25кг)- 1,025 тн",
        f"{sample}(2кг)- 0,066 тн",
    ]


def test_duplicate_lines_of_one_product_are_summed(nurzhan, product, first_grade):
    order = _truck(nurzhan, (product, 100), (first_grade, 10), (product, 50))

    assert _text(order).splitlines()[3:5] == ["Д1с(50кг)- 7,5 тн", "Ат1с(50кг)- 0,5 тн"]


def test_warehouse_line_follows_where_the_bags_came_from(nurzhan, product, mill, mill_two, boss):
    receive_stock(product, 20, boss, warehouse=mill_two)
    recorded = _truck(nurzhan, (product, 20), sources=((product, mill_two, 20),))
    legacy = _truck(nurzhan, (product, 20))
    fixation = _truck(nurzhan, (product, 20), stock_deducted=False, warehouse=mill_two)

    assert _text(recorded).splitlines()[-1] == "Склад: Мельница 2"
    # До складов-источников всё шло со склада заказа.
    assert _text(legacy).splitlines()[-1] == "Склад: Мельница"
    # Фиксация задним числом склад не списывала — склад заказа.
    assert _text(fixation).splitlines()[-1] == "Склад: Мельница 2"


def test_several_orders_are_blocks_in_shipment_time_order(nurzhan, product):
    late = _truck(nurzhan, (product, 20), truck="403BJN13", at=_at(hour=15))
    early = _truck(nurzhan, (product, 40), truck="909ERD13", at=_at(hour=9))
    next_day = _truck(nurzhan, (product, 60), truck="160AL17", at=_at(DAY.replace(day=3), hour=8))

    report = compose_truck_report(_rows(late, next_day, early))

    assert report.order_ids == [early.pk, late.pk, next_day.pk]
    blocks = report.text.split("\n\n")
    assert [block.splitlines()[:2] for block in blocks] == [
        ["Отгрузка 02.10.26", "kz 909 ERD 13"],
        ["Отгрузка 02.10.26", "kz 403 BJN 13"],
        ["Отгрузка 03.10.26", "kz 160 AL 17"],
    ]


def test_only_shipped_trucks_are_reported(client, nurzhan, product):
    shipped = _truck(nurzhan, (product, 20))
    pending = _truck(nurzhan, (product, 20))
    Order.objects.filter(pk=pending.pk).update(status="confirmed")
    wagon = train_order(client, product, shipped_at=_at())

    report = compose_truck_report(_rows(shipped, pending, wagon))

    assert report.order_ids == [shipped.pk]
    assert compose_truck_report(_rows(pending, wagon)).text == ""


def test_codes_and_names_are_read_once_for_all_orders(nurzhan, product, mill, mill_two, django_assert_num_queries):
    orders = [
        _truck(nurzhan, (product, 20), sources=((product, mill, 10), (product, mill_two, 10)), at=_at(hour=hour))
        for hour in range(8, 14)
    ]
    rows = _rows(*orders)

    # Коды товаров и названия клиентов — по запросу на все отгрузки, склады — из предзагрузки.
    with django_assert_num_queries(2):
        assert len(compose_truck_report(rows).order_ids) == 6
