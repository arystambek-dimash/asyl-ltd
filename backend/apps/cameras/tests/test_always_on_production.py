from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Event
from zoneinfo import ZoneInfo

import pytest
from django.db import close_old_connections, connections
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.cameras import production, production_queries, production_runs
from apps.cameras.models import (
    ANALYTICS_SCOPE_AI247,
    ANALYTICS_SCOPE_SHIPPING,
    AlwaysOnColorProductMapping,
    AlwaysOnCounterCursor,
    AlwaysOnDailyAnalytics,
    AlwaysOnImportedEvent,
    AlwaysOnProductionCorrection,
    AlwaysOnProductionRun,
    AlwaysOnStockBatch,
    AlwaysOnStockPosting,
    AlwaysOnWarehouseRoute,
    ContinuousCameraRole,
    MonoblockCameraSettings,
)
from apps.catalog.models import Product
from apps.warehouse import services as warehouse_services
from apps.warehouse.models import StockItem, StockMovement, StockReceipt, Warehouse
from apps.warehouse.services import receive_stock

pytestmark = pytest.mark.django_db

ALMATY = ZoneInfo("Asia/Almaty")


def _at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, day, hour, minute, tzinfo=ALMATY)


def _closed_run(*, camera="cam3", color="red", bags=10, day=16):
    started = _at(day, 10)
    return AlwaysOnProductionRun.objects.create(
        camera=camera,
        business_day=started.date(),
        color=color,
        started_at=started,
        last_counted_at=started + timedelta(minutes=10),
        ended_at=started + timedelta(minutes=10),
        model_bags=bags,
    )


def _reserve_ai247(camera="cam3"):
    ContinuousCameraRole.objects.update_or_create(
        camera=camera,
        defaults={"analytics_scope": ANALYTICS_SCOPE_AI247},
    )


def _imported_event(
    event_id: int,
    occurred_at: datetime,
    *,
    camera: str = "cam3",
    mode: str = "always_on",
    applied: bool = True,
    class_name: str = "Red_50",
    color: str | None = None,
    brand: str | None = "korol",
    analytics_scope: str = ANALYTICS_SCOPE_AI247,
) -> AlwaysOnImportedEvent:
    return AlwaysOnImportedEvent.objects.create(
        camera=camera,
        upstream_event_id=event_id,
        occurred_at=occurred_at,
        source="sub",
        mode=mode,
        analytics_scope=analytics_scope,
        class_name=class_name,
        color=color,
        brand=brand,
        total_after=event_id,
        applied_to_analytics=applied,
        applied_to_production=(
            applied
            and analytics_scope == ANALYTICS_SCOPE_AI247
            and mode == "always_on"
        ),
    )


def _payload_run(
    run_id: int,
    color: str,
    bags: int,
    *,
    partial: bool = False,
    approximate: bool = False,
) -> dict:
    minute = run_id % 60
    timestamp = f"2026-08-16T10:{minute:02d}:00+05:00"
    return {
        "id": run_id,
        "camera": "cam3",
        "business_day": "2026-08-16",
        "color": color,
        "started_at": timestamp,
        "last_counted_at": timestamp,
        "ended_at": timestamp,
        "model_bags": bags,
        "is_approximate": approximate,
        "status": "closed",
        "starts_before_day": partial,
        "ends_after_day": False,
        "is_partial_for_day": partial,
    }


def test_business_day_switches_exactly_at_nineteen():
    assert production_runs.business_day_for(_at(16, 18, 59)).isoformat() == "2026-08-16"
    assert production_runs.business_day_for(_at(16, 19, 0)).isoformat() == "2026-08-17"
    assert timezone.localtime(production_runs.scheduled_for(_at(16, 10).date())).hour == 19


def _count(color, observed_at, bags=1):
    for _ in range(bags):
        production_runs.record_color_event("cam3", color, observed_at)


def test_color_events_form_periods_and_close_after_a_gap():
    _count("red", _at(16, 10), 2)
    _count("red", _at(16, 10, 4), 3)
    _count("red", _at(16, 10, 10))

    runs = list(AlwaysOnProductionRun.objects.order_by("started_at"))
    assert len(runs) == 2
    assert runs[0].model_bags == 5
    assert runs[0].ended_at == _at(16, 10, 4)
    assert runs[1].model_bags == 1
    assert runs[1].ended_at is None


def test_color_events_split_every_color_change_even_inside_gap():
    start = _at(16, 10)
    _count("red", start, 2)
    _count("red", start + timedelta(minutes=1), 3)
    _count("green", start + timedelta(minutes=2))
    _count("blue", start + timedelta(minutes=3))
    _count("red", start + timedelta(minutes=4))

    runs = list(AlwaysOnProductionRun.objects.order_by("started_at", "id"))
    assert [row.color for row in runs] == ["red", "green", "blue", "red"]
    assert [row.model_bags for row in runs] == [5, 1, 1, 1]
    assert [row.ended_at for row in runs] == [
        start + timedelta(minutes=1),
        start + timedelta(minutes=2),
        start + timedelta(minutes=3),
        None,
    ]
    assert AlwaysOnProductionRun.objects.filter(ended_at__isnull=True).count() == 1


def test_color_event_does_not_revive_an_older_open_color():
    start = _at(16, 10)
    old_red = AlwaysOnProductionRun.objects.create(
        camera="cam3",
        business_day=start.date(),
        color="red",
        started_at=start,
        last_counted_at=start,
        model_bags=2,
    )
    current = AlwaysOnProductionRun.objects.create(
        camera="cam3",
        business_day=start.date(),
        color="green",
        started_at=start + timedelta(minutes=1),
        last_counted_at=start + timedelta(minutes=1),
        model_bags=1,
    )

    _count("red", start + timedelta(minutes=2))

    runs = list(AlwaysOnProductionRun.objects.order_by("started_at", "id"))
    assert [row.color for row in runs] == ["red", "green", "red"]
    assert [row.model_bags for row in runs] == [2, 1, 1]
    assert runs[0].pk == old_red.pk
    assert runs[0].ended_at == start
    assert runs[1].pk == current.pk
    assert runs[1].ended_at == start + timedelta(minutes=1)
    assert runs[2].ended_at is None
    assert AlwaysOnProductionRun.objects.filter(ended_at__isnull=True).count() == 1


@pytest.mark.parametrize("color", ["", None, "x" * 33])
def test_bag_without_a_usable_colour_is_counted_as_unclassified(color):
    _count(color, _at(16, 10))

    run = AlwaysOnProductionRun.objects.get()
    assert (run.color, run.model_bags, run.is_approximate) == ("unclassified", 1, True)


def test_smooth_day_runs_matches_supplied_operator_sample_without_mutating_raw():
    data = [
        ("red", 82),
        ("red", 391),
        ("red", 1852),
        ("red", 179),
        ("red", 18),
        ("red", 24),
        ("red", 740),
        ("red", 20),
        ("blue", 23),
        ("blue", 474),
        ("green", 1),
        ("green", 1),
        ("blue", 269),
        ("blue", 74),
        ("green", 1),
        ("green", 2),
        ("green", 136),
    ]
    raw = [
        _payload_run(index, color, bags)
        for index, (color, bags) in enumerate(data, start=1)
    ]
    original = [dict(run) for run in raw]

    result = production_queries.smooth_day_runs(raw)

    assert production_queries._run_color_totals(raw) == {
        "blue": 840,
        "green": 141,
        "red": 3306,
    }
    assert [(run["color"], run["model_bags"]) for run in result] == [
        ("red", 3306),
        ("blue", 842),
        ("green", 139),
    ]
    assert sum(run["model_bags"] for run in result) == sum(
        run["model_bags"] for run in raw
    )
    assert raw == original


def test_smooth_day_runs_uses_strict_threshold_and_keeps_edges_and_boundaries():
    exact_threshold = production_queries.smooth_day_runs(
        [
            _payload_run(1, "red", 100),
            _payload_run(2, "blue", 10),
            _payload_run(3, "red", 100),
        ]
    )
    edge = production_queries.smooth_day_runs(
        [
            _payload_run(1, "blue", 9),
            _payload_run(2, "red", 100),
            _payload_run(3, "green", 9),
        ]
    )
    unlike_neighbors = production_queries.smooth_day_runs(
        [
            _payload_run(1, "red", 100),
            _payload_run(2, "blue", 9),
            _payload_run(3, "green", 100),
        ]
    )

    assert [(run["color"], run["model_bags"]) for run in exact_threshold] == [
        ("red", 100),
        ("blue", 10),
        ("red", 100),
    ]
    assert [(run["color"], run["model_bags"]) for run in edge] == [
        ("blue", 9),
        ("red", 100),
        ("green", 9),
    ]
    assert [(run["color"], run["model_bags"]) for run in unlike_neighbors] == [
        ("red", 100),
        ("blue", 9),
        ("green", 100),
    ]


def test_smooth_day_runs_repeats_after_smallest_sandwich_collapses():
    result = production_queries.smooth_day_runs(
        [
            _payload_run(1, "red", 100),
            _payload_run(2, "blue", 4),
            _payload_run(3, "green", 1),
            _payload_run(4, "blue", 4),
            _payload_run(5, "red", 100),
        ]
    )

    assert [(run["color"], run["model_bags"]) for run in result] == [("red", 209)]


@pytest.mark.parametrize("barrier", ["partial", "approximate"])
def test_smooth_day_runs_never_uses_unreliable_rows_as_sandwich_neighbors(barrier):
    middle = _payload_run(
        2,
        "blue",
        1,
        partial=barrier == "partial",
        approximate=barrier == "approximate",
    )

    result = production_queries.smooth_day_runs(
        [
            _payload_run(1, "red", 100),
            middle,
            _payload_run(3, "red", 100),
        ]
    )

    assert [(run["color"], run["model_bags"]) for run in result] == [
        ("red", 100),
        ("blue", 1),
        ("red", 100),
    ]


@pytest.mark.parametrize("barrier", ["partial", "approximate"])
def test_smooth_day_runs_never_merges_with_or_across_unreliable_neighbor(barrier):
    barrier_flags = {
        "partial": barrier == "partial",
        "approximate": barrier == "approximate",
    }
    as_neighbor = production_queries.smooth_day_runs(
        [
            _payload_run(1, "red", 100, **barrier_flags),
            _payload_run(2, "blue", 1),
            _payload_run(3, "red", 100),
        ]
    )
    same_color_boundary = production_queries.smooth_day_runs(
        [
            _payload_run(1, "red", 100),
            _payload_run(2, "red", 1, **barrier_flags),
            _payload_run(3, "red", 100),
        ]
    )

    assert [(run["color"], run["model_bags"]) for run in as_neighbor] == [
        ("red", 100),
        ("blue", 1),
        ("red", 100),
    ]
    assert [run["model_bags"] for run in same_color_boundary] == [100, 1, 100]


def test_daily_stock_post_is_exactly_once(make_product):
    _reserve_ai247()
    product = make_product()
    _closed_run(bags=12)
    AlwaysOnColorProductMapping.objects.create(
        camera="cam3",
        color="red",
        product=product,
    )

    first = production.post_due_stock(_at(16, 19))
    second = production.post_due_stock(_at(16, 19, 1))

    assert first[0]["status"] == AlwaysOnStockBatch.POSTED
    assert first[0]["total_bags"] == 12
    assert second == []
    assert StockItem.objects.get(product=product).bags == 12
    assert StockReceipt.objects.filter(product=product, bags=12).count() == 1
    assert AlwaysOnStockPosting.objects.count() == 1
    movement = StockMovement.objects.get(product=product)
    assert movement.delta == 12
    assert "AI 24/7" in movement.note


def test_daily_stock_post_uses_the_camera_warehouse_route(make_product):
    _reserve_ai247()
    secondary = Warehouse.objects.create(
        code="finished-goods-2",
        name="Склад готовой продукции №2",
    )
    product = make_product()
    production.save_mappings(
        "cam3",
        [{"color": "red", "product": product.pk}],
        warehouse_id=secondary.pk,
    )
    AlwaysOnCounterCursor.objects.filter(camera="cam3").update(
        event_sync_supported=False,
    )
    _closed_run(bags=12)

    posted = production.post_due_stock(_at(16, 19))[0]

    route = AlwaysOnWarehouseRoute.objects.get(camera="cam3")
    batch = AlwaysOnStockBatch.objects.get(camera="cam3")
    stock = StockItem.objects.get(product=product)
    receipt = StockReceipt.objects.get(product=product)
    movement = StockMovement.objects.get(product=product)
    assert route.warehouse == secondary
    assert batch.warehouse == secondary
    assert stock.warehouse == secondary
    assert receipt.warehouse == secondary
    assert movement.warehouse == secondary
    assert posted["warehouse"] == secondary.pk
    assert posted["warehouse_name"] == secondary.name


def test_mapping_materializes_zero_stock_ownership_before_posting(make_product):
    secondary = Warehouse.objects.create(code="mapped-stock", name="Склад маршрута")
    product = make_product()

    production.save_mappings(
        "cam3",
        [{"color": "red", "product": product.pk}],
        warehouse_id=secondary.pk,
    )

    stock = StockItem.objects.get(product=product)
    assert stock.warehouse == secondary
    assert stock.bags == 0
    assert AlwaysOnCounterCursor.objects.filter(camera="cam3").exists()


def test_unrouted_legacy_camera_stays_on_main_after_default_changes(make_product):
    main = Warehouse.objects.get(code="main")
    secondary = Warehouse.objects.create(code="new-default", name="Новый основной")
    product = make_product()
    receive_stock(product, 3, user=None, warehouse=main)
    AlwaysOnColorProductMapping.objects.create(
        camera="cam3",
        color="red",
        product=product,
    )
    Warehouse.objects.filter(pk=main.pk).update(is_default=False)
    Warehouse.objects.filter(pk=secondary.pk).update(is_default=True)

    result = production_queries.production_payload("cam3")

    assert result["warehouse"] == main.pk
    red_mapping = next(
        row for row in result["mappings"] if row["color"] == "red"
    )
    assert red_mapping["product"] == product.pk


def test_daily_stock_post_finishes_for_a_route_disabled_after_production(make_product):
    _reserve_ai247()
    secondary = Warehouse.objects.create(
        code="finished-goods-disabled",
        name="Отключённый после смены склад",
    )
    product = make_product()
    production.save_mappings(
        "cam3",
        [{"color": "red", "product": product.pk}],
        warehouse_id=secondary.pk,
    )
    AlwaysOnCounterCursor.objects.filter(camera="cam3").update(
        event_sync_supported=False,
    )
    _closed_run(bags=9)
    Warehouse.objects.filter(pk=secondary.pk).update(is_active=False)

    posted = production.post_due_stock(_at(16, 19))[0]

    assert posted["status"] == AlwaysOnStockBatch.POSTED
    assert posted["warehouse"] == secondary.pk
    assert StockItem.objects.get(product=product).warehouse_id == secondary.pk
    assert StockReceipt.objects.get(product=product).warehouse_id == secondary.pk


def test_mapping_can_be_fixed_on_the_same_inactive_route_with_unposted_work(make_product):
    secondary = Warehouse.objects.create(code="inactive-route", name="Склад смены")
    red = make_product(color="Red")
    blue = make_product(color="Blue")
    production.save_mappings(
        "cam3",
        [{"color": "red", "product": red.pk}],
        warehouse_id=secondary.pk,
    )
    _closed_run(color="blue", bags=4)
    Warehouse.objects.filter(pk=secondary.pk).update(is_active=False)

    result = production.save_mappings(
        "cam3",
        [
            {"color": "red", "product": red.pk},
            {"color": "blue", "product": blue.pk},
        ],
        warehouse_id=secondary.pk,
    )

    assert result["warehouse"] == secondary.pk
    assert StockItem.objects.get(product=blue).warehouse_id == secondary.pk
    assert AlwaysOnColorProductMapping.objects.get(
        camera="cam3",
        color="blue",
    ).product == blue


def test_mapping_creates_a_stock_card_for_same_product_in_another_warehouse(make_product):
    main = Warehouse.objects.get(is_default=True)
    secondary = Warehouse.objects.create(code="secondary", name="Второй склад")
    product = make_product()
    receive_stock(product, 3, user=None, warehouse=main)

    result = production.save_mappings(
        "cam3",
        [{"color": "red", "product": product.pk}],
        warehouse_id=secondary.pk,
    )

    assert result["warehouse"] == secondary.pk
    assert StockItem.objects.filter(
        product=product,
        warehouse=main,
        bags=3,
    ).exists()
    assert StockItem.objects.filter(
        product=product,
        warehouse=secondary,
        bags=0,
    ).exists()
    assert AlwaysOnWarehouseRoute.objects.get(camera="cam3").warehouse == secondary
    assert AlwaysOnColorProductMapping.objects.get(
        camera="cam3", color="red"
    ).product == product


def test_payload_marks_mapping_unconfigured_without_stock_card_in_camera_warehouse(make_product):
    main = Warehouse.objects.get(is_default=True)
    secondary = Warehouse.objects.create(code="secondary", name="Второй склад")
    product = make_product()
    receive_stock(product, 3, user=None, warehouse=main)
    AlwaysOnWarehouseRoute.objects.create(camera="cam3", warehouse=secondary)
    AlwaysOnColorProductMapping.objects.create(
        camera="cam3",
        color="red",
        product=product,
    )

    result = production_queries.production_payload("cam3")

    product_option = next(row for row in result["products"] if row["id"] == product.pk)
    assert result["fully_configured"] is False
    assert product_option["warehouse_ids"] == [main.pk]


def test_production_payload_exposes_every_stock_card_for_product(make_product):
    main = Warehouse.objects.get(is_default=True)
    secondary = Warehouse.objects.create(
        code="second",
        name="Мельница 2",
        is_active=False,
    )
    product = make_product(name="Товар двух складов")
    StockItem.objects.create(product=product, warehouse=main, bags=12)
    StockItem.objects.create(product=product, warehouse=secondary, bags=0)

    payload = production_queries.production_payload("cam3")

    product_row = next(row for row in payload["products"] if row["id"] == product.pk)
    assert product_row["warehouse_ids"] == [main.pk, secondary.pk]


@pytest.mark.django_db(transaction=True)
def test_mapping_route_change_locks_main_before_secondary_warehouse(monkeypatch, make_product):
    main = Warehouse.objects.get(is_default=True)
    secondary = Warehouse.objects.create(code="secondary", name="Второй склад")
    product = make_product()
    receive_stock(product, 10, user=None, warehouse=main)
    AlwaysOnWarehouseRoute.objects.create(camera="cam3", warehouse=secondary)

    main_locked = Event()
    concurrent_transfer_started = Event()
    original_main_warehouse = production.get_main_warehouse

    def pause_after_main_lock(*, lock=False):
        warehouse = original_main_warehouse(lock=lock)
        if lock:
            main_locked.set()
            if not concurrent_transfer_started.wait(timeout=5):
                raise RuntimeError("concurrent transfer did not start")
        return warehouse

    monkeypatch.setattr(
        production,
        "get_main_warehouse",
        pause_after_main_lock,
    )

    def change_route():
        close_old_connections()
        try:
            return production.save_mappings(
                "cam3",
                [{"color": "red", "product": product.pk}],
                warehouse_id=main.pk,
            )
        finally:
            connections.close_all()

    def transfer_concurrently():
        close_old_connections()
        try:
            if not main_locked.wait(timeout=5):
                raise RuntimeError("mapping save did not lock main")
            concurrent_transfer_started.set()
            return warehouse_services.transfer_stock(
                Product.objects.get(pk=product.pk),
                1,
                user=None,
                from_warehouse=main.pk,
                to_warehouse=secondary.pk,
            )
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        route_future = pool.submit(change_route)
        transfer_future = pool.submit(transfer_concurrently)
        route_future.result(timeout=10)
        transfer_future.result(timeout=10)

    assert AlwaysOnWarehouseRoute.objects.get(camera="cam3").warehouse == main
    assert StockItem.objects.get(product=product, warehouse=main).bags == 9
    assert StockItem.objects.get(product=product, warehouse=secondary).bags == 1


def test_camera_warehouse_cannot_change_with_unposted_production(make_product):
    main = Warehouse.objects.get(is_default=True)
    secondary = Warehouse.objects.create(code="secondary", name="Второй склад")
    product = make_product()
    production.save_mappings(
        "cam3",
        [{"color": "red", "product": product.pk}],
        warehouse_id=main.pk,
    )
    _closed_run(bags=1)

    with pytest.raises(ValidationError) as exc:
        production.save_mappings(
            "cam3",
            [{"color": "red", "product": product.pk}],
            warehouse_id=secondary.pk,
        )

    assert exc.value.detail["code"] == "warehouse_has_unposted_production"
    assert AlwaysOnWarehouseRoute.objects.get(camera="cam3").warehouse == main


def test_camera_warehouse_cannot_change_with_a_nonterminal_batch_before_runs(make_product):
    main = Warehouse.objects.get(is_default=True)
    secondary = Warehouse.objects.create(code="batch-route", name="Второй склад")
    product = make_product()
    production.save_mappings(
        "cam3",
        [{"color": "red", "product": product.pk}],
        warehouse_id=main.pk,
    )
    AlwaysOnStockBatch.objects.create(
        camera="cam3",
        warehouse=main,
        business_day=_at(28, 12).date(),
        scheduled_for=_at(28, 19),
        status=AlwaysOnStockBatch.BLOCKED,
        last_error="Ожидается журнал событий",
    )

    with pytest.raises(ValidationError) as exc:
        production.save_mappings(
            "cam3",
            [],
            warehouse_id=secondary.pk,
        )

    assert exc.value.detail["code"] == "warehouse_has_unposted_production"
    assert AlwaysOnWarehouseRoute.objects.get(camera="cam3").warehouse == main


def test_event_camera_stock_waits_for_a_fresh_caught_up_page_after_cutoff(make_product):
    _reserve_ai247()
    product = make_product()
    _closed_run(bags=12)
    AlwaysOnColorProductMapping.objects.create(
        camera="cam3",
        color="red",
        product=product,
    )
    cursor = AlwaysOnCounterCursor.objects.create(
        camera="cam3",
        last_event_id=10,
        event_sync_supported=True,
        event_boundary_validated=True,
        event_caught_up_at=_at(16, 18, 59),
    )

    blocked = production.post_due_stock(_at(16, 19))[0]

    assert blocked["status"] == AlwaysOnStockBatch.BLOCKED
    assert "синхронизация событий AI" in blocked["last_error"]
    assert not StockReceipt.objects.exists()

    cursor.event_caught_up_at = _at(16, 19, 1)
    cursor.save(update_fields=["event_caught_up_at", "updated_at"])
    posted = production.post_due_stock(_at(16, 19, 1))[0]

    assert posted["status"] == AlwaysOnStockBatch.POSTED
    assert posted["total_bags"] == 12
    assert StockItem.objects.get(product=product).bags == 12


def test_stock_cannot_close_before_the_initial_event_capability_probe(make_product):
    _reserve_ai247()
    product = make_product()
    _closed_run(bags=4)
    AlwaysOnColorProductMapping.objects.create(
        camera="cam3",
        color="red",
        product=product,
    )
    AlwaysOnCounterCursor.objects.create(camera="cam3", last_total=4)

    blocked = production.post_due_stock(_at(16, 19))[0]

    assert blocked["status"] == AlwaysOnStockBatch.BLOCKED
    assert "проверка журнала" in blocked["last_error"]
    assert not StockReceipt.objects.exists()


def test_missing_mapping_blocks_whole_batch_then_retries_safely(make_product):
    _reserve_ai247()
    red = make_product(color="Red")
    _closed_run(color="red", bags=7)
    _closed_run(color="blue", bags=4)
    AlwaysOnColorProductMapping.objects.create(
        camera="cam3",
        color="red",
        product=red,
    )

    blocked = production.post_due_stock(_at(16, 19))[0]

    assert blocked["status"] == AlwaysOnStockBatch.BLOCKED
    assert StockReceipt.objects.count() == 0
    assert not StockItem.objects.exists()

    blue = make_product(color="Blue")
    AlwaysOnColorProductMapping.objects.create(
        camera="cam3",
        color="blue",
        product=blue,
    )
    posted = production.post_due_stock(_at(16, 19, 1))[0]

    assert posted["status"] == AlwaysOnStockBatch.POSTED
    assert StockItem.objects.get(product=red).bags == 7
    assert StockItem.objects.get(product=blue).bags == 4
    assert StockReceipt.objects.count() == 2


def test_color_correction_reduces_the_warehouse_receipt(boss, make_product):
    _reserve_ai247()
    product = make_product()
    _closed_run(bags=10)
    AlwaysOnCounterCursor.objects.create(
        camera="cam3",
        event_sync_supported=False,
    )
    AlwaysOnColorProductMapping.objects.create(
        camera="cam3",
        color="red",
        product=product,
    )
    AlwaysOnProductionCorrection.objects.create(
        camera="cam3",
        business_day=_at(16, 12).date(),
        color="red",
        delta=-2,
        reason="ложное срабатывание",
        created_by=boss,
    )

    posted = production.post_due_stock(_at(16, 19))[0]

    assert posted["total_bags"] == 8
    assert posted["items"][0]["correction_bags"] == -2
    assert AlwaysOnProductionCorrection.objects.get().delta == -2
    assert StockItem.objects.get(product=product).bags == 8


def test_shipping_reserved_legacy_batches_are_never_posted_or_retried():
    for camera in ("cam2", "cam4"):
        ContinuousCameraRole.objects.create(
            camera=camera,
            analytics_scope=ANALYTICS_SCOPE_SHIPPING,
        )
    blocked_day = _at(15, 10).date()
    failed_day = _at(16, 10).date()
    _closed_run(camera="cam2", bags=5, day=15)
    _closed_run(camera="cam4", bags=7, day=16)
    blocked = AlwaysOnStockBatch.objects.create(
        camera="cam2",
        business_day=blocked_day,
        scheduled_for=production_runs.scheduled_for(blocked_day),
        status=AlwaysOnStockBatch.BLOCKED,
        last_error="legacy blocked",
    )
    failed = AlwaysOnStockBatch.objects.create(
        camera="cam4",
        business_day=failed_day,
        scheduled_for=production_runs.scheduled_for(failed_day),
        status=AlwaysOnStockBatch.FAILED,
        last_error="legacy failed",
    )

    assert production._due_pairs(_at(17, 19)) == []
    assert production.post_due_stock(_at(17, 19)) == []
    for batch in (blocked, failed):
        with pytest.raises(ValidationError):
            production.retry_batch(batch.pk)
        batch.refresh_from_db()
    assert blocked.status == AlwaysOnStockBatch.BLOCKED
    assert blocked.last_error == "legacy blocked"
    assert failed.status == AlwaysOnStockBatch.FAILED
    assert failed.last_error == "legacy failed"
    assert not StockMovement.objects.exists()


def test_production_payload_returns_every_run_for_selected_day():
    selected_day = _at(14, 10).date()
    rows = []
    for index in range(101):
        started = _at(14, 8) + timedelta(seconds=index)
        rows.append(
            AlwaysOnProductionRun(
                camera="cam3",
                business_day=selected_day,
                color="red" if index % 2 == 0 else "blue",
                started_at=started,
                last_counted_at=started,
                ended_at=started,
                model_bags=1,
            )
        )
    AlwaysOnProductionRun.objects.bulk_create(rows)
    _closed_run(camera="cam3", color="green", bags=3, day=15)
    _closed_run(camera="cam4", color="red", bags=4, day=14)

    result = production_queries.production_payload("cam3", day="2026-08-14")

    assert result["selected_day"] == "2026-08-14"
    assert len(result["day_runs"]) == 101
    assert {row["camera"] for row in result["day_runs"]} == {"cam3"}
    assert {row["business_day"] for row in result["day_runs"]} == {"2026-08-14"}
    assert [row["started_at"] for row in result["day_runs"]] == sorted(
        row["started_at"] for row in result["day_runs"]
    )


def test_viewing_ai_production_does_not_close_a_shipping_legacy_run():
    _reserve_ai247("cam3")
    ContinuousCameraRole.objects.create(
        camera="cam2",
        analytics_scope=ANALYTICS_SCOPE_SHIPPING,
    )
    shipping_run = AlwaysOnProductionRun.objects.create(
        camera="cam2",
        business_day=_at(14, 10).date(),
        color="red",
        started_at=_at(14, 10),
        last_counted_at=_at(14, 10),
        model_bags=3,
    )

    production_queries.production_payload("cam3", day="2026-08-14")

    shipping_run.refresh_from_db()
    assert shipping_run.ended_at is None


def test_selected_day_dominant_brand_is_joined_to_normalized_event_color():
    selected_day = "2026-08-14"
    for event_id, brand in enumerate(
        [" Korol ", "korol", "Dikhan   Baba", "dikhan baba"],
        start=1,
    ):
        _imported_event(
            event_id,
            _at(14, 10) + timedelta(minutes=event_id),
            class_name="Red_50",
            color=" Blue_50 ",
            brand=brand,
        )
    _imported_event(
        5,
        _at(14, 11),
        class_name="Green_50",
        brand=None,
    )
    _imported_event(
        6,
        _at(14, 12),
        class_name="Green_50",
        brand="unknown",
    )
    AlwaysOnDailyAnalytics.objects.create(
        camera="cam3",
        day=_at(14, 0).date(),
        model_total=6,
        model_per_color={"blue": 4, "green": 2},
    )

    result = production_queries.production_payload("cam3", day=selected_day)

    # Both real brands have two events; the lexical tie-break is deterministic.
    assert result["dominant_brand_by_color"] == {
        "blue": "dikhan baba",
        "green": None,
    }


def test_dominant_brand_uses_local_day_and_only_applied_always_on_camera_events():
    start = _at(14, 0)
    end = _at(15, 0)
    _imported_event(1, start, class_name="Red_50", brand="korol")
    _imported_event(
        2,
        end - timedelta(microseconds=1),
        class_name="Red_50",
        brand="korol",
    )
    _imported_event(
        3,
        start - timedelta(microseconds=1),
        class_name="Green_50",
        brand="outside before",
    )
    _imported_event(
        4,
        end,
        class_name="Blue_50",
        brand="outside after",
    )
    _imported_event(
        1,
        _at(14, 12),
        camera="cam4",
        class_name="Yellow_50",
        brand="other camera",
    )
    _imported_event(
        5,
        _at(14, 13),
        mode="session",
        class_name="Purple_50",
        brand="wrong mode",
    )
    _imported_event(
        6,
        _at(14, 14),
        applied=False,
        class_name="Orange_50",
        brand="not applied",
    )
    # A camera may change contour later on the same day. Shipping enrichment
    # must never label AI-production totals, even though it is durably applied
    # to its own analytics ledger.
    for event_id in (7, 8):
        _imported_event(
            event_id,
            _at(14, 15) + timedelta(seconds=event_id),
            class_name="Red_50",
            brand="shipping-only",
            analytics_scope=ANALYTICS_SCOPE_SHIPPING,
        )
    AlwaysOnDailyAnalytics.objects.create(
        camera="cam3",
        day=_at(14, 0).date(),
        model_total=2,
        model_per_color={"red": 2},
    )

    result = production_queries.production_payload("cam3", day="2026-08-14")

    assert result["dominant_brand_by_color"] == {"red": "korol"}


def test_dominant_brand_uses_only_the_active_tail_after_same_day_archive():
    _imported_event(1, _at(14, 9), class_name="Red_50", brand="dikhan_baba")
    _imported_event(2, _at(14, 9, 1), class_name="Green_50", brand="korol")
    _imported_event(3, _at(14, 10), class_name="Red_50", brand="dikhan_baba")
    _imported_event(4, _at(14, 11), class_name="Red_50", brand="korol")
    _imported_event(5, _at(14, 11, 1), class_name="Green_50", brand="unknown")
    _imported_event(6, _at(14, 12), class_name="Red_50", brand="korol")
    # The active analytics row was reset by an archive after event 3.  Its
    # colour counts describe only events 4–6, so archived brands cannot leak
    # into the cards.
    AlwaysOnDailyAnalytics.objects.create(
        camera="cam3",
        day=_at(14, 0).date(),
        model_total=3,
        model_per_color={"red": 2, "green": 1},
    )

    result = production_queries.production_payload("cam3", day="2026-08-14")

    assert result["dominant_brand_by_color"] == {
        "green": None,
        "red": "korol",
    }


def test_dominant_brand_is_unknown_when_event_journal_does_not_cover_active_count():
    _imported_event(1, _at(14, 11), class_name="Red_50", brand="dikhan_baba")
    _imported_event(2, _at(14, 12), class_name="Red_50", brand="dikhan_baba")
    # A snapshot baseline predates the durable event journal.  Two classified
    # events cannot safely label all 2,293 active red bags.
    AlwaysOnDailyAnalytics.objects.create(
        camera="cam3",
        day=_at(14, 0).date(),
        model_total=2293,
        model_per_color={"red": 2293},
    )

    result = production_queries.production_payload("cam3", day="2026-08-14")

    assert result["dominant_brand_by_color"] == {"red": None}


def test_dominant_brand_does_not_guess_for_legacy_or_missing_event_data():
    _closed_run(camera="cam3", color="red", bags=7, day=14)

    selected = production_queries.production_payload("cam3", day="2026-08-14")
    without_day = production_queries.production_payload("cam3")

    assert selected["dominant_brand_by_color"] == {}
    assert without_day["dominant_brand_by_color"] == {}


def test_production_payload_keeps_raw_runs_and_adds_algorithm_analytics():
    selected_day = _at(14, 10).date()
    started = _at(14, 10)
    rows = [
        AlwaysOnProductionRun(
            camera="cam3",
            business_day=selected_day,
            color=color,
            started_at=started + timedelta(minutes=index),
            last_counted_at=started + timedelta(minutes=index),
            ended_at=started + timedelta(minutes=index),
            model_bags=bags,
        )
        for index, (color, bags) in enumerate([("red", 100), ("blue", 2), ("red", 100)])
    ]
    AlwaysOnProductionRun.objects.bulk_create(rows)

    result = production_queries.production_payload("cam3", day="2026-08-14")

    assert [(run["color"], run["model_bags"]) for run in result["day_runs"]] == [
        ("red", 100),
        ("blue", 2),
        ("red", 100),
    ]
    assert [
        (run["color"], run["model_bags"]) for run in result["algorithm_day_runs"]
    ] == [("red", 202)]
    assert result["run_smoothing"] == {
        "n_min": 10,
        "raw_model_total": 202,
        "algorithm_model_total": 202,
        "raw_colors": [
            {"color": "red", "total": 200, "percent": 99.0},
            {"color": "blue", "total": 2, "percent": 1.0},
        ],
        "algorithm_colors": [{"color": "red", "total": 202, "percent": 100.0}],
    }
    # The API algorithm is display-only: the durable stock ledger stays raw.
    assert list(
        AlwaysOnProductionRun.objects.order_by("started_at", "id").values_list(
            "color", "model_bags"
        )
    ) == [("red", 100), ("blue", 2), ("red", 100)]
    assert production_runs._day_totals("cam3", selected_day) == {
        "blue": {
            "detected_bags": 2,
            "resolved_bags": 0,
            "correction_bags": 0,
            "net_bags": 2,
            "inferred": {},
        },
        "red": {
            "detected_bags": 200,
            "resolved_bags": 0,
            "correction_bags": 0,
            "net_bags": 200,
            "inferred": {},
        },
    }


def test_production_api_filters_day_and_rejects_bad_iso_date(
    auth_client,
    admin_user,
    ai247_camera,
):
    selected = _closed_run(camera="cam3", color="red", bags=7, day=14)
    _closed_run(camera="cam3", color="blue", bags=8, day=15)

    response = auth_client(admin_user).get(
        "/api/cameras/always-on-production/?camera=cam3&day=2026-08-14",
    )

    assert response.status_code == 200
    assert response.data["selected_day"] == "2026-08-14"
    assert [row["id"] for row in response.data["day_runs"]] == [selected.pk]

    invalid = auth_client(admin_user).get(
        "/api/cameras/always-on-production/?camera=cam3&day=14.08.2026",
    )
    assert invalid.status_code == 400
    assert "day" in invalid.data["detail"]


def test_production_api_rejects_shipping_contour_camera(auth_client, admin_user):
    MonoblockCameraSettings.objects.create(
        camera_sources=["cam2"],
        always_on_camera_sources=["cam3"],
    )

    response = auth_client(admin_user).get(
        "/api/cameras/always-on-production/?camera=cam2"
    )

    assert response.status_code == 400
    assert response.data["code"] == "camera_not_in_ai247"


def test_production_api_fails_closed_when_active_ai_setting_has_shipping_role(
    auth_client,
    admin_user,
):
    MonoblockCameraSettings.objects.create(always_on_camera_sources=["cam2"])
    ContinuousCameraRole.objects.create(
        camera="cam2",
        analytics_scope=ANALYTICS_SCOPE_SHIPPING,
    )

    read = auth_client(admin_user).get(
        "/api/cameras/always-on-production/?camera=cam2"
    )
    write = auth_client(admin_user).put(
        "/api/cameras/always-on-production/",
        {"camera": "cam2", "mappings": []},
        format="json",
    )

    assert read.status_code == 400
    assert read.data["code"] == "camera_not_in_ai247"
    assert write.status_code == 400
    assert write.data["code"] == "camera_not_in_ai247"
    assert not AlwaysOnColorProductMapping.objects.filter(camera="cam2").exists()


def test_selected_analytics_day_uses_local_calendar_not_stock_business_day():
    started = _at(16, 20)
    run = AlwaysOnProductionRun.objects.create(
        camera="cam3",
        # After the 19:00 warehouse cutoff this is the next production day,
        # while the analytics bar is still the calendar date 16 August.
        business_day=_at(17, 10).date(),
        color="red",
        started_at=started,
        last_counted_at=started + timedelta(minutes=2),
        ended_at=started + timedelta(minutes=2),
        model_bags=5,
    )

    calendar_day = production_queries.production_payload("cam3", day="2026-08-16")
    following_day = production_queries.production_payload("cam3", day="2026-08-17")

    assert [row["id"] for row in calendar_day["day_runs"]] == [run.pk]
    assert following_day["day_runs"] == []


def test_continuous_run_is_split_at_local_midnight_for_daily_analytics():
    _count("red", _at(16, 23, 59), 2)
    _count("red", _at(17, 0, 1), 3)

    rows = list(AlwaysOnProductionRun.objects.order_by("started_at", "id"))
    assert len(rows) == 2
    assert [row.business_day.isoformat() for row in rows] == [
        "2026-08-17",
        "2026-08-17",
    ]
    assert rows[0].model_bags == 2
    assert rows[0].ended_at == _at(16, 23, 59)
    assert rows[1].model_bags == 3
    assert rows[1].started_at == _at(17, 0, 1)

    first_day = production_queries.production_payload("cam3", day="2026-08-16")
    second_day = production_queries.production_payload("cam3", day="2026-08-17")
    assert [row["id"] for row in first_day["day_runs"]] == [rows[0].pk]
    assert [row["id"] for row in second_day["day_runs"]] == [rows[1].pk]
    assert first_day["day_runs"][0]["is_partial_for_day"] is False
    assert second_day["day_runs"][0]["is_partial_for_day"] is False


def test_legacy_cross_midnight_run_overlaps_both_calendar_days_with_flags():
    started = _at(16, 23, 58)
    legacy = AlwaysOnProductionRun.objects.create(
        camera="cam3",
        business_day=_at(17, 10).date(),
        color="blue",
        started_at=started,
        last_counted_at=_at(17, 0, 2),
        ended_at=_at(17, 0, 2),
        model_bags=9,
    )

    first = production_queries.production_payload("cam3", day="2026-08-16")["day_runs"]
    second = production_queries.production_payload("cam3", day="2026-08-17")["day_runs"]

    assert [row["id"] for row in first] == [legacy.pk]
    assert first[0]["starts_before_day"] is False
    assert first[0]["ends_after_day"] is True
    assert first[0]["is_partial_for_day"] is True
    assert [row["id"] for row in second] == [legacy.pk]
    assert second[0]["starts_before_day"] is True
    assert second[0]["ends_after_day"] is False
    assert second[0]["is_partial_for_day"] is True


def test_run_counted_exactly_at_midnight_belongs_to_new_calendar_day():
    midnight = _at(17, 0)
    run = AlwaysOnProductionRun.objects.create(
        camera="cam3",
        business_day=midnight.date(),
        color="green",
        started_at=midnight,
        last_counted_at=midnight,
        ended_at=midnight,
        model_bags=1,
    )

    previous = production_queries.production_payload("cam3", day="2026-08-16")
    current = production_queries.production_payload("cam3", day="2026-08-17")

    assert previous["day_runs"] == []
    assert [row["id"] for row in current["day_runs"]] == [run.pk]
