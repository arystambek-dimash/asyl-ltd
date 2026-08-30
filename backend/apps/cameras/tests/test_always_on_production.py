from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from apps.cameras import analytics, production
from apps.cameras.models import (
    AlwaysOnColorProductMapping,
    AlwaysOnCounterCursor,
    AlwaysOnDailyAnalytics,
    AlwaysOnImportedEvent,
    AlwaysOnProductionCorrection,
    AlwaysOnProductionRun,
    AlwaysOnStockBatch,
    AlwaysOnStockPosting,
    MonoblockCameraSettings,
)
from apps.catalog.models import Product
from apps.warehouse.models import StockItem, StockMovement, StockReceipt

pytestmark = pytest.mark.django_db

ALMATY = ZoneInfo("Asia/Almaty")


def _at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, day, hour, minute, tzinfo=ALMATY)


def _product(color="Red"):
    return Product.objects.create(
        name=f"Продукт {color}",
        color=color,
        weight_kg="50",
        price="100",
    )


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
) -> AlwaysOnImportedEvent:
    return AlwaysOnImportedEvent.objects.create(
        camera=camera,
        upstream_event_id=event_id,
        occurred_at=occurred_at,
        source="sub",
        mode=mode,
        class_name=class_name,
        color=color,
        brand=brand,
        total_after=event_id,
        applied_to_analytics=applied,
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
    assert production.business_day_for(_at(16, 18, 59)).isoformat() == "2026-08-16"
    assert production.business_day_for(_at(16, 19, 0)).isoformat() == "2026-08-17"
    assert timezone.localtime(production.scheduled_for(_at(16, 10).date())).hour == 19


def test_color_deltas_form_periods_and_close_after_a_gap():
    production.record_color_deltas("cam3", {"red": 2}, _at(16, 10), 2)
    production.record_color_deltas("cam3", {"red": 3}, _at(16, 10, 4), 3)
    production.record_color_deltas("cam3", {"red": 1}, _at(16, 10, 10), 1)

    runs = list(AlwaysOnProductionRun.objects.order_by("started_at"))
    assert len(runs) == 2
    assert runs[0].model_bags == 5
    assert runs[0].ended_at == _at(16, 10, 4)
    assert runs[1].model_bags == 1
    assert runs[1].ended_at is None


def test_ordered_color_events_split_every_color_change_even_inside_gap():
    start = _at(16, 10)
    production.record_color_deltas(
        "cam3", {"red": 2}, start, 2, ordered_color_event=True
    )
    production.record_color_deltas(
        "cam3",
        {"red": 3},
        start + timedelta(minutes=1),
        3,
        ordered_color_event=True,
    )
    production.record_color_deltas(
        "cam3",
        {"green": 1},
        start + timedelta(minutes=2),
        1,
        ordered_color_event=True,
    )
    production.record_color_deltas(
        "cam3",
        {"blue": 1},
        start + timedelta(minutes=3),
        1,
        ordered_color_event=True,
    )
    production.record_color_deltas(
        "cam3",
        {"red": 1},
        start + timedelta(minutes=4),
        1,
        ordered_color_event=True,
    )

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


def test_legacy_multi_color_snapshot_keeps_unordered_open_runs():
    production.record_color_deltas(
        "cam3",
        {"red": 2, "blue": 1},
        _at(16, 10),
        3,
    )

    runs = AlwaysOnProductionRun.objects.filter(ended_at__isnull=True)
    assert dict(runs.values_list("color", "model_bags")) == {"red": 2, "blue": 1}


def test_first_ordered_event_does_not_revive_an_older_legacy_open_color():
    start = _at(16, 10)
    old_red = AlwaysOnProductionRun.objects.create(
        camera="cam3",
        business_day=start.date(),
        color="red",
        started_at=start,
        last_counted_at=start,
        model_bags=2,
    )
    legacy_current = AlwaysOnProductionRun.objects.create(
        camera="cam3",
        business_day=start.date(),
        color="green",
        started_at=start + timedelta(minutes=1),
        last_counted_at=start + timedelta(minutes=1),
        model_bags=1,
    )

    production.record_color_deltas(
        "cam3",
        {"red": 1},
        start + timedelta(minutes=2),
        1,
        ordered_color_event=True,
    )

    runs = list(AlwaysOnProductionRun.objects.order_by("started_at", "id"))
    assert [row.color for row in runs] == ["red", "green", "red"]
    assert [row.model_bags for row in runs] == [2, 1, 1]
    assert runs[0].pk == old_red.pk
    assert runs[0].ended_at == start
    assert runs[1].pk == legacy_current.pk
    assert runs[1].ended_at == start + timedelta(minutes=1)
    assert runs[2].ended_at is None
    assert AlwaysOnProductionRun.objects.filter(ended_at__isnull=True).count() == 1


def test_unclassified_delta_is_kept_instead_of_disappearing():
    production.record_color_deltas("cam3", {"red": 3}, _at(16, 10), 5)

    totals = dict(AlwaysOnProductionRun.objects.values_list("color", "model_bags"))
    assert totals == {"red": 3, "unclassified": 2}


def test_colour_breakdown_can_never_overstate_total_delta():
    production.record_color_deltas(
        "cam3",
        {"red": 4, "blue": 4},
        _at(16, 10),
        5,
    )

    assert sum(AlwaysOnProductionRun.objects.values_list("model_bags", flat=True)) == 5


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

    result = production.smooth_day_runs(raw)

    assert production._run_color_totals(raw) == {
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
    exact_threshold = production.smooth_day_runs(
        [
            _payload_run(1, "red", 100),
            _payload_run(2, "blue", 10),
            _payload_run(3, "red", 100),
        ]
    )
    edge = production.smooth_day_runs(
        [
            _payload_run(1, "blue", 9),
            _payload_run(2, "red", 100),
            _payload_run(3, "green", 9),
        ]
    )
    unlike_neighbors = production.smooth_day_runs(
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
    result = production.smooth_day_runs(
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

    result = production.smooth_day_runs(
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
    as_neighbor = production.smooth_day_runs(
        [
            _payload_run(1, "red", 100, **barrier_flags),
            _payload_run(2, "blue", 1),
            _payload_run(3, "red", 100),
        ]
    )
    same_color_boundary = production.smooth_day_runs(
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


def test_daily_stock_post_is_exactly_once():
    product = _product()
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


def test_event_camera_stock_waits_for_a_fresh_caught_up_page_after_cutoff():
    product = _product()
    _closed_run(bags=12)
    AlwaysOnColorProductMapping.objects.create(
        camera="cam3",
        color="red",
        product=product,
    )
    cursor = AlwaysOnCounterCursor.objects.create(
        camera="cam3",
        last_event_id=10,
        event_compat_total=0,
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


def test_stock_cannot_close_before_the_initial_event_capability_probe():
    product = _product()
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


def test_missing_mapping_blocks_whole_batch_then_retries_safely():
    red = _product("Red")
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

    blue = _product("Blue")
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


def test_color_correction_reduces_the_warehouse_receipt(boss):
    product = _product()
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
    with patch.object(production.timezone, "now", return_value=_at(16, 12)):
        production.record_correction(
            "cam3",
            "red",
            2,
            "ложное срабатывание",
            boss,
        )

    posted = production.post_due_stock(_at(16, 19))[0]

    assert posted["total_bags"] == 8
    assert posted["items"][0]["correction_bags"] == -2
    assert AlwaysOnProductionCorrection.objects.get().delta == -2
    assert StockItem.objects.get(product=product).bags == 8


def test_display_archive_does_not_duplicate_the_production_ledger(boss):
    MonoblockCameraSettings.objects.create(always_on_camera_sources=["cam3"])
    first = _at(16, 12)
    analytics.record_snapshot(
        {
            "processors": [
                {
                    "cam": "cam3",
                    "total": 100,
                    "mode": "always_on",
                    "running": True,
                    "per_color": {"Red_50": 100},
                }
            ]
        },
        observed_at=first,
    )

    with patch.object(analytics.timezone, "now", return_value=first):
        analytics.archive_camera("cam3", "закрытие экрана", boss)
    analytics.record_snapshot(
        {
            "processors": [
                {
                    "cam": "cam3",
                    "total": 140,
                    "mode": "always_on",
                    "running": True,
                    "per_color": {"Red_50": 140},
                }
            ]
        },
        observed_at=first + timedelta(minutes=1),
    )

    assert (
        sum(AlwaysOnProductionRun.objects.values_list("model_bags", flat=True)) == 140
    )
    assert analytics.today_payload()["all_time_total"] == 40


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

    result = production.production_payload("cam3", day="2026-08-14")

    assert result["selected_day"] == "2026-08-14"
    assert len(result["day_runs"]) == 101
    assert {row["camera"] for row in result["day_runs"]} == {"cam3"}
    assert {row["business_day"] for row in result["day_runs"]} == {"2026-08-14"}
    assert [row["started_at"] for row in result["day_runs"]] == sorted(
        row["started_at"] for row in result["day_runs"]
    )
    # Preserve the existing bounded journal contract for the settings screen.
    assert len(result["runs"]) == 100


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

    result = production.production_payload("cam3", day=selected_day)

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
    AlwaysOnDailyAnalytics.objects.create(
        camera="cam3",
        day=_at(14, 0).date(),
        model_total=2,
        model_per_color={"red": 2},
    )

    result = production.production_payload("cam3", day="2026-08-14")

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

    result = production.production_payload("cam3", day="2026-08-14")

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

    result = production.production_payload("cam3", day="2026-08-14")

    assert result["dominant_brand_by_color"] == {"red": None}


def test_dominant_brand_does_not_guess_for_legacy_or_missing_event_data():
    _closed_run(camera="cam3", color="red", bags=7, day=14)

    selected = production.production_payload("cam3", day="2026-08-14")
    without_day = production.production_payload("cam3")

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

    result = production.production_payload("cam3", day="2026-08-14")

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
        "changed": True,
        "raw_run_count": 3,
        "algorithm_run_count": 1,
        "raw_model_total": 202,
        "algorithm_model_total": 202,
        "raw_model_per_color": {"blue": 2, "red": 200},
        "algorithm_model_per_color": {"red": 202},
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
    assert production._day_totals("cam3", selected_day) == {
        "blue": {"detected_bags": 2, "correction_bags": 0, "net_bags": 2},
        "red": {"detected_bags": 200, "correction_bags": 0, "net_bags": 200},
    }


def test_production_api_filters_day_and_rejects_bad_iso_date(
    auth_client,
    admin_user,
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

    calendar_day = production.production_payload("cam3", day="2026-08-16")
    following_day = production.production_payload("cam3", day="2026-08-17")

    assert [row["id"] for row in calendar_day["day_runs"]] == [run.pk]
    assert following_day["day_runs"] == []


def test_continuous_run_is_split_at_local_midnight_for_daily_analytics():
    production.record_color_deltas("cam3", {"red": 2}, _at(16, 23, 59), 2)
    production.record_color_deltas("cam3", {"red": 3}, _at(17, 0, 1), 3)

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

    first_day = production.production_payload("cam3", day="2026-08-16")
    second_day = production.production_payload("cam3", day="2026-08-17")
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

    first = production.production_payload("cam3", day="2026-08-16")["day_runs"]
    second = production.production_payload("cam3", day="2026-08-17")["day_runs"]

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

    previous = production.production_payload("cam3", day="2026-08-16")
    current = production.production_payload("cam3", day="2026-08-17")

    assert previous["day_runs"] == []
    assert [row["id"] for row in current["day_runs"]] == [run.pk]
