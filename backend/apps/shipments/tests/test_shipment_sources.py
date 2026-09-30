"""Склады-источники отгрузки: с какого склада взяты мешки каждого товара заказа.

Склады — общие фикстуры ``mill`` («Мельница», склад ``main``) и ``mill_two``
(«Мельница 2») из ``conftest.py`` этой папки.
"""

from collections import Counter
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.db import IntegrityError, connection, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.eventlog.models import EventLog
from apps.orders.models import Order, OrderItem, StatusChangeRequest
from apps.orders.services import approve_status_change, request_status_change
from apps.shipments.models import Shipment, ShipmentSource
from apps.shipments.services import RailWagon, dispatch_order, manual_complete_order, ship_rail_report
from apps.shipments.sources import (
    absorb_delta,
    bags_by_product,
    bags_mismatch,
    default_sources,
    line_sources,
    loader_must_choose,
    sources_text,
)
from apps.warehouse.models import StockItem, StockMovement, Warehouse
from apps.warehouse.services import receive_stock

pytestmark = pytest.mark.django_db


def test_source_cell_is_unique_per_shipment_product_and_warehouse(product, make_order, mill, mill_two):
    shipment = Shipment.objects.create(order=make_order(product, quantity=20))
    ShipmentSource.objects.create(shipment=shipment, product=product, warehouse=mill, bags=12)
    ShipmentSource.objects.create(shipment=shipment, product=product, warehouse=mill_two, bags=8)

    with pytest.raises(IntegrityError, match="shipment_source_unique_cell"), transaction.atomic():
        ShipmentSource.objects.create(shipment=shipment, product=product, warehouse=mill, bags=1)


def test_source_bags_must_be_positive_in_the_database(product, make_order, mill):
    shipment = Shipment.objects.create(order=make_order(product))

    with pytest.raises(IntegrityError, match="shipment_source_bags_positive"), transaction.atomic():
        ShipmentSource.objects.create(shipment=shipment, product=product, warehouse=mill, bags=0)


def test_shipment_counts_stock_as_deducted_by_default(product, make_order):
    """ORM и INSERT старого образа (он колонку не знает) — «склад списан»."""
    shipment = Shipment.objects.create(order=make_order(product))
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO shipments_shipment (order_id, bags_loaded) VALUES (%s, 0) RETURNING id",
            [make_order(product).pk],
        )
        old_image_pk = cursor.fetchone()[0]

    assert shipment.stock_deducted is True
    assert Shipment.objects.get(pk=old_image_pk).stock_deducted is True


def test_hard_delete_of_order_takes_its_sources_along(product, make_order, mill):
    """«Удалить клиента с историей» (clients/views.py::purge) удаляет заказы ORM-ом:
    Order → Shipment → ShipmentSource уходят каскадом, PROTECT склада не мешает."""
    order = make_order(product, quantity=20)
    shipment = Shipment.objects.create(order=order)
    ShipmentSource.objects.create(shipment=shipment, product=product, warehouse=mill, bags=20)

    Order.all_objects.filter(pk=order.pk).delete()

    assert not ShipmentSource.objects.filter(shipment_id=shipment.pk).exists()
    assert mill.shipment_sources.count() == 0


# ---- Чистые хелперы apps.shipments.sources (правило D5 §4, накладная, предикат опросника) ----

MILL, MILL_2 = 1, 2  # «Мельница» — «Склад отгрузки» заказа (якорь D5), «Мельница 2»


@pytest.mark.parametrize(
    ("rows", "delta", "expected"),
    [
        pytest.param({MILL: 12, MILL_2: 8}, 3, {MILL: 15, MILL_2: 8}, id="plus3-anchor-first"),
        pytest.param({MILL: 12, MILL_2: 8}, -5, {MILL: 7, MILL_2: 8}, id="minus5-from-anchor"),
        pytest.param({MILL: 12, MILL_2: 8}, -15, {MILL_2: 5}, id="minus15-empties-anchor-row"),
        pytest.param({MILL_2: 20}, 3, {MILL_2: 23}, id="all-from-mill2-then-plus3"),
        pytest.param({MILL_2: 20}, -7, {MILL_2: 13}, id="single-source-stays-single"),
        pytest.param({}, 40, {MILL: 40}, id="new-product-goes-to-anchor"),
        pytest.param({MILL: 12, MILL_2: 8}, -20, {}, id="whole-product-removed"),
        pytest.param({MILL: 12, MILL_2: 8}, 0, {MILL: 12, MILL_2: 8}, id="no-change"),
        pytest.param({2: 5, 3: 5}, 1, {2: 6, 3: 5}, id="no-anchor-tie-lower-id"),
        pytest.param({2: 5, 3: 7}, 1, {2: 5, 3: 8}, id="no-anchor-more-bags-first"),
        pytest.param({2: 5, 3: 7}, -9, {2: 3}, id="no-anchor-minus-walks-ranking"),
    ],
)
def test_absorb_delta_follows_the_d5_rule(rows, delta, expected):
    assert absorb_delta(rows, delta, anchor_id=MILL) == expected


@pytest.mark.parametrize("rows", [{MILL: 12, MILL_2: 8}, {MILL_2: 20}, {2: 5, 3: 5}, {}])
@pytest.mark.parametrize("delta", [1, 3, 20])
def test_absorb_delta_plus_then_minus_is_an_exact_undo(rows, delta):
    grown = absorb_delta(rows, delta, anchor_id=MILL)

    assert absorb_delta(grown, -delta, anchor_id=MILL) == rows


def test_absorb_delta_returns_a_new_dict_and_refuses_to_return_more_than_shipped():
    rows = {MILL: 12, MILL_2: 8}

    absorb_delta(rows, -15, anchor_id=MILL)

    assert rows == {MILL: 12, MILL_2: 8}
    with pytest.raises(ValueError):
        absorb_delta(rows, -21, anchor_id=MILL)


def test_bags_by_product_sums_duplicate_lines_and_skips_deleted_products():
    items = [
        SimpleNamespace(product_id=12, quantity=10),
        SimpleNamespace(product_id=15, quantity=40),
        SimpleNamespace(product_id=12, quantity=10),
        SimpleNamespace(product_id=None, quantity=5),
    ]

    assert bags_by_product(items) == Counter({12: 20, 15: 40})


def _source(product_id, warehouse_id, name, bags):
    return ShipmentSource(product_id=product_id, warehouse=Warehouse(id=warehouse_id, name=name), bags=bags)


def test_line_sources_pours_each_products_rows_over_its_lines_in_id_order():
    first = OrderItem(id=5, product_id=12, quantity=10)
    second = OrderItem(id=7, product_id=12, quantity=10)
    other = OrderItem(id=9, product_id=15, quantity=40)
    deleted = OrderItem(id=11, product_id=None, quantity=3)
    sources = [
        _source(12, MILL_2, "Мельница 2", 8),
        _source(15, MILL_2, "Мельница 2", 40),
        _source(12, MILL, "Мельница", 12),
    ]

    parts = line_sources(sources, [other, second, deleted, first])

    assert parts == {
        5: [("Мельница", 10)],
        7: [("Мельница", 2), ("Мельница 2", 8)],
        9: [("Мельница 2", 40)],
        11: [],
    }


def test_line_sources_orders_warehouses_by_name_then_id():
    line = OrderItem(id=1, product_id=12, quantity=20)
    sources = [_source(12, MILL, "Мельница", 12), _source(12, 9, "Амбар", 8)]

    assert line_sources(sources, [line]) == {1: [("Амбар", 8), ("Мельница", 12)]}


@pytest.mark.parametrize(
    ("parts", "expected"),
    [
        ([("Мельница", 20)], "со склада: Мельница"),
        ([("Мельница", 12), ("Мельница 2", 8)], "Мельница — 12, Мельница 2 — 8"),
        ([], ""),
    ],
)
def test_sources_text_reads_like_the_waybill(parts, expected):
    assert sources_text(parts) == expected


@pytest.mark.parametrize(
    ("transport", "second_active", "expected"),
    [
        ("truck", True, True),
        ("truck", False, False),
        ("train", True, False),
        ("train", False, False),
    ],
)
def test_loader_must_choose_only_for_trucks_with_two_active_warehouses(transport, second_active, expected):
    Warehouse.objects.create(code="mill-2", name="Мельница 2", is_active=second_active)

    assert loader_must_choose(Order(transport_type=transport)) is expected


def test_default_sources_puts_each_products_bags_on_the_order_warehouse(make_product, make_order):
    flour = make_product(name="Д1с")
    bran = make_product(name="Б", color="Blue", weight_kg="25")
    order = make_order(flour, quantity=12)
    OrderItem.objects.create(order=order, product=bran, quantity=40, unit_price="9000.00")
    OrderItem.objects.create(order=order, product=flour, quantity=8, unit_price="10000.00")
    items = list(order.items.select_related("product").order_by("product_id", "id"))

    rows = default_sources(order, items)

    assert [(row.product, row.warehouse, row.bags, row.pk) for row in rows] == [
        (flour, order.warehouse, 20, None),
        (bran, order.warehouse, 40, None),
    ]
    assert not ShipmentSource.objects.exists()


def test_bags_mismatch_counts_lines_per_product_with_the_callers_word(make_product, make_order):
    flour = make_product(name="Д1с")
    bran = make_product(name="Б", color="Blue", weight_kg="25")
    order = make_order(flour, quantity=20)
    split = [
        ShipmentSource(product=flour, warehouse=order.warehouse, bags=12),
        ShipmentSource(product=flour, warehouse=order.warehouse, bags=8),
    ]
    wrong = [
        ShipmentSource(product=flour, warehouse=order.warehouse, bags=12),
        ShipmentSource(product=bran, warehouse=order.warehouse, bags=5),
    ]

    assert bags_mismatch(order, split, counted="выбрано") == ""
    assert bags_mismatch(order, wrong, counted="выбрано") == (
        "«Б · Синий 25 кг»: в заказе 0, выбрано 5; «Д1с · Красный 50 кг»: в заказе 20, выбрано 12"
    )


# Отгрузка по ответу «С какого склада?»: plan_sources, write_off, POST dispatch (спека §2.1, §2.3, §3.2).


def _post_dispatch(api, order, **body):
    return api.post(f"/api/loader/orders/{order.pk}/dispatch/", body, format="json")


def _answer(*cells):
    """Ответ опросника из (товар, склад, мешков) — как его шлёт экран грузчика."""
    return [{"product": product_id, "warehouse": warehouse_id, "bags": bags} for product_id, warehouse_id, bags in cells]


def _stock_of(product, warehouse):
    return StockItem.objects.get(product=product, warehouse=warehouse).bags


def _source_rows(order):
    return set(
        ShipmentSource.objects.filter(shipment__order=order).values_list("product_id", "warehouse_id", "bags")
    )


def test_write_off_split_answer_deducts_each_warehouse_and_records_sources(
    auth_client, trucks_loader, boss, product, make_product, make_order, mill, mill_two,
):
    """Д1с 20 = Мельница 12 + Мельница 2 8, «Б» 40 — целиком с Мельницы 2 (спека §3.2)."""
    receive_stock(product, 50, boss, warehouse=mill_two)
    blue = make_product(name="Б", color="Blue")
    receive_stock(blue, 30, boss, warehouse=mill)
    receive_stock(blue, 60, boss, warehouse=mill_two)
    order = make_order(product, quantity=20)
    OrderItem.objects.create(order=order, product=blue, quantity=40, unit_price="10000.00")

    response = _post_dispatch(
        auth_client(trucks_loader),
        order,
        truck_number="403 bjn 13",
        sources=_answer((product.pk, mill.pk, 12), (product.pk, mill_two.pk, 8), (blue.pk, mill_two.pk, 40)),
    )

    assert response.status_code == 200, response.data
    assert response.data["status"] == "shipped"
    assert (_stock_of(product, mill), _stock_of(product, mill_two)) == (88, 42)
    assert (_stock_of(blue, mill), _stock_of(blue, mill_two)) == (30, 20)
    assert _source_rows(order) == {
        (product.pk, mill.pk, 12),
        (product.pk, mill_two.pk, 8),
        (blue.pk, mill_two.pk, 40),
    }
    note = f"Отгрузка заказа #{order.pk}"
    assert set(
        StockMovement.objects.filter(reason="shipment").values_list("product_id", "warehouse_id", "delta", "note")
    ) == {
        (product.pk, mill.pk, -12, note),
        (product.pk, mill_two.pk, -8, note),
        (blue.pk, mill_two.pk, -40, note),
    }
    assert EventLog.objects.get(order=order, event_type="shipment").payload["sources"] == [
        {"product": product.pk, "warehouse": mill.pk, "warehouse_name": "Мельница", "bags": 12},
        {"product": product.pk, "warehouse": mill_two.pk, "warehouse_name": "Мельница 2", "bags": 8},
        {"product": blue.pk, "warehouse": mill_two.pk, "warehouse_name": "Мельница 2", "bags": 40},
    ]


def test_write_off_requires_an_answer_for_a_truck_with_two_active_warehouses(
    auth_client, trucks_loader, product, make_order, mill, mill_two, dispatch_closed_orders,
):
    """Старая вкладка без опросника: отказ «обновите страницу», AI-подсчёт не тронут (D1)."""
    order = make_order(product, status="loading", quantity=5)

    response = _post_dispatch(auth_client(trucks_loader), order)

    assert response.status_code == 400
    assert response.data["code"] == "sources_required"
    assert response.data["detail"] == (
        "Выберите, с какого склада отгрузка (если окна выбора нет — обновите страницу)"
    )
    assert dispatch_closed_orders == []
    order.refresh_from_db()
    assert order.status == "loading"
    assert _stock_of(product, mill) == 100
    assert not ShipmentSource.objects.exists()


@pytest.mark.parametrize(("transport_type", "second_active"), [("truck", False), ("train", True)])
def test_write_off_without_choice_ships_everything_from_the_order_warehouse(
    auth_client, boss, product, make_order, mill, mill_two, transport_type, second_active,
):
    """Вагон или один активный склад: опросника нет, всё со «Склада отгрузки», строки пишутся."""
    Warehouse.objects.filter(pk=mill_two.pk).update(is_active=second_active)
    order = make_order(product, quantity=7, transport_type=transport_type)

    response = _post_dispatch(auth_client(boss), order)

    assert response.status_code == 200, response.data
    assert _source_rows(order) == {(product.pk, mill.pk, 7)}
    assert _stock_of(product, mill) == 93
    assert not StockItem.objects.filter(product=product, warehouse=mill_two).exists()


@pytest.mark.parametrize(
    ("case", "code"),
    [
        ("short", "sources_mismatch"),
        ("missing_product", "sources_mismatch"),
        ("extra_product", "sources_mismatch"),
        ("unknown_product", "sources_mismatch"),
        ("duplicate", "sources_duplicate"),
        ("inactive_warehouse", "warehouse_inactive"),
        ("unknown_warehouse", "warehouse_not_found"),
        ("deleted_product", "product_deleted"),
    ],
)
def test_write_off_refuses_a_bad_answer_before_closing_the_ai_count(
    auth_client, trucks_loader, product, make_product, make_order, mill, mill_two,
    dispatch_closed_orders, case, code,
):
    """Кривой или устаревший ответ опросника — 400 до закрытия AI-подсчёта: сессию камер он не останавливает."""
    blue = make_product(name="Б", color="Blue")
    stray = make_product(name="Лишний", color="Green")
    retired = Warehouse.objects.create(code="retired", name="Старый склад", is_active=False)
    order = make_order(product, status="loading", quantity=5)
    blue_line = OrderItem.objects.create(order=order, product=blue, quantity=3, unit_price="10000.00")
    full = [(product.pk, mill.pk, 5), (blue.pk, mill_two.pk, 3)]
    answers = {
        "short": [(product.pk, mill.pk, 3), (blue.pk, mill_two.pk, 3)],
        "missing_product": [(product.pk, mill.pk, 5)],
        "extra_product": [*full, (stray.pk, mill.pk, 1)],
        "unknown_product": [*full, (stray.pk + 1000, mill.pk, 1)],
        "duplicate": [(product.pk, mill.pk, 2), (product.pk, mill.pk, 3), (blue.pk, mill_two.pk, 3)],
        "inactive_warehouse": [(product.pk, retired.pk, 5), (blue.pk, mill_two.pk, 3)],
        "unknown_warehouse": [(product.pk, retired.pk + 1000, 5), (blue.pk, mill_two.pk, 3)],
        "deleted_product": full,
    }
    if case == "deleted_product":
        OrderItem.objects.filter(pk=blue_line.pk).update(product=None)

    response = _post_dispatch(auth_client(trucks_loader), order, sources=_answer(*answers[case]))

    assert response.status_code == 400
    assert response.data["code"] == code
    assert dispatch_closed_orders == []
    order.refresh_from_db()
    assert order.status == "loading"
    assert not ShipmentSource.objects.exists()
    assert not StockMovement.objects.filter(reason="shipment").exists()


@pytest.mark.parametrize("answer", ["empty", "zero_bags", "too_many"])
def test_write_off_checks_the_answer_shape_first(
    auth_client, trucks_loader, product, make_order, mill, mill_two, dispatch_closed_orders, answer,
):
    """Форму ответа проверяет сериализатор: 400 по полю ``sources`` раньше сервиса и AI-подсчёта."""
    order = make_order(product, status="loading", quantity=5)
    sources = {
        "empty": [],
        "zero_bags": _answer((product.pk, mill.pk, 0)),
        "too_many": _answer(*[(product.pk, mill.pk, 1)] * 101),
    }[answer]

    response = _post_dispatch(auth_client(trucks_loader), order, sources=sources)

    assert response.status_code == 400
    # config/exceptions.py: ошибки полей сериализатора — под "detail", код — "invalid".
    assert response.data["code"] == "invalid"
    assert "sources" in response.data["detail"]
    assert dispatch_closed_orders == []


def test_write_off_rechecks_the_answer_under_the_order_lock(trucks_loader, product, make_order, mill, mill_two):
    """Позиции поправили, пока был открыт опросник: под блокировкой ответ не сходится — ничего не списано."""
    order = make_order(product, quantity=20)
    answer = _answer((product.pk, mill.pk, 12), (product.pk, mill_two.pk, 8))
    OrderItem.objects.filter(order=order).update(quantity=25)

    with pytest.raises(ValidationError) as caught:
        dispatch_order(order, trucks_loader, sources=answer)

    assert caught.value.detail["code"] == "sources_mismatch"
    assert caught.value.detail["detail"] == (
        "Состав заказа изменился — ответьте заново: «Д1с · Красный 50 кг»: в заказе 25, выбрано 20"
    )
    order.refresh_from_db()
    assert order.status == "confirmed"
    assert not Shipment.objects.filter(order=order).exists()
    assert _stock_of(product, mill) == 100


def test_write_off_from_a_warehouse_without_a_card_goes_negative(
    auth_client, trucks_loader, product, make_product, make_order, mill, mill_two,
):
    """«Мельница 2» без карточки: карточка создаётся с 0 и уходит в минус, отгрузка проходит (D2)."""
    blue = make_product(name="Б", color="Blue")  # ни одной карточки ни на одном складе
    order = make_order(product, quantity=20)
    OrderItem.objects.create(order=order, product=blue, quantity=40, unit_price="10000.00")

    response = _post_dispatch(
        auth_client(trucks_loader),
        order,
        sources=_answer((product.pk, mill.pk, 12), (product.pk, mill_two.pk, 8), (blue.pk, mill_two.pk, 40)),
    )

    assert response.status_code == 200, response.data
    assert (_stock_of(product, mill_two), _stock_of(blue, mill_two)) == (-8, -40)
    # Карточка на «Складе отгрузки» гарантирована: на ней держится триггер orders/0047 при правке позиций.
    assert _stock_of(blue, mill) == 0
    negatives = EventLog.objects.filter(event_type="stock_negative").order_by("id")
    assert [(e.order_id, e.payload["product"], e.payload["warehouse"], e.payload["deduct"]) for e in negatives] == [
        (order.pk, product.pk, mill_two.pk, 8),
        (order.pk, blue.pk, mill_two.pk, 40),
    ]


def test_write_off_sums_duplicate_lines_of_one_product(
    auth_client, trucks_loader, product, make_order, mill, mill_two,
):
    """Две позиции одного товара — один вопрос на их сумму."""
    order = make_order(product, quantity=5)
    OrderItem.objects.create(order=order, product=product, quantity=7, unit_price="10000.00")
    api = auth_client(trucks_loader)

    partial = _post_dispatch(api, order, sources=_answer((product.pk, mill.pk, 7)))

    assert partial.status_code == 400
    assert partial.data["code"] == "sources_mismatch"

    response = _post_dispatch(api, order, sources=_answer((product.pk, mill.pk, 4), (product.pk, mill_two.pk, 8)))

    assert response.status_code == 200, response.data
    assert _source_rows(order) == {(product.pk, mill.pk, 4), (product.pk, mill_two.pk, 8)}
    assert (_stock_of(product, mill), _stock_of(product, mill_two)) == (96, -8)


def test_write_off_manual_completion_uses_the_order_warehouse(boss, product, make_order, mill, mill_two):
    """D4: ручное «Отгружен» не спрашивает склад даже при двух активных — всё со «Склада отгрузки»."""
    order = make_order(product, quantity=6)

    manual_complete_order(order, None, boss)

    assert _source_rows(order) == {(product.pk, mill.pk, 6)}
    assert _stock_of(product, mill) == 94
    assert EventLog.objects.get(order=order, event_type="shipment").payload["sources"] == [
        {"product": product.pk, "warehouse": mill.pk, "warehouse_name": "Мельница", "bags": 6},
    ]


def test_write_off_approved_status_request_uses_the_order_warehouse(
    operator, boss, product, make_order, mill, mill_two,
):
    """D4: одобренный запрос «Отгружен» (approve_status_change → manual_complete_order) — без опросника."""
    order = make_order(product, quantity=4)
    assert request_status_change(order, "shipped", operator)["applied"] is False

    approve_status_change(StatusChangeRequest.objects.get(order=order), boss)

    order.refresh_from_db()
    assert order.status == "shipped"
    assert _source_rows(order) == {(product.pk, mill.pk, 4)}
    assert _stock_of(product, mill) == 96
    assert not StockItem.objects.filter(product=product, warehouse=mill_two).exists()


def test_write_off_rail_report_uses_the_order_warehouse(wagons_loader, product, make_order, mill, mill_two):
    """D4: вагоны по отчёту — без опросника, источник записан на «Склад отгрузки»."""
    order = make_order(product, quantity=40, transport_type="train")

    ship_rail_report(
        order,
        [RailWagon(number="28087658", product=product, bags=40, weight_kg=Decimal("2000"))],
        wagons_loader,
        station="Раустан",
        shipped_day=timezone.localdate(),
    )

    assert _source_rows(order) == {(product.pk, mill.pk, 40)}
    assert _stock_of(product, mill) == 60
