from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from apps.cameras import analytics, production
from apps.cameras.models import (
    AlwaysOnColorProductMapping,
    AlwaysOnCounterCursor,
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
