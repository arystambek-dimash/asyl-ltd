from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from apps.cameras import analytics, production
from apps.cameras.models import (
    AlwaysOnColorProductMapping,
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
        name=f"Продукт {color}", color=color, weight_kg="50", price="100",
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

    totals = dict(
        AlwaysOnProductionRun.objects.values_list("color", "model_bags")
    )
    assert totals == {"red": 3, "unclassified": 2}


def test_colour_breakdown_can_never_overstate_total_delta():
    production.record_color_deltas(
        "cam3", {"red": 4, "blue": 4}, _at(16, 10), 5,
    )

    assert sum(
        AlwaysOnProductionRun.objects.values_list("model_bags", flat=True)
    ) == 5


def test_daily_stock_post_is_exactly_once():
    product = _product()
    _closed_run(bags=12)
    AlwaysOnColorProductMapping.objects.create(
        camera="cam3", color="red", product=product,
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


def test_missing_mapping_blocks_whole_batch_then_retries_safely():
    red = _product("Red")
    _closed_run(color="red", bags=7)
    _closed_run(color="blue", bags=4)
    AlwaysOnColorProductMapping.objects.create(
        camera="cam3", color="red", product=red,
    )

    blocked = production.post_due_stock(_at(16, 19))[0]

    assert blocked["status"] == AlwaysOnStockBatch.BLOCKED
    assert StockReceipt.objects.count() == 0
    assert not StockItem.objects.exists()

    blue = _product("Blue")
    AlwaysOnColorProductMapping.objects.create(
        camera="cam3", color="blue", product=blue,
    )
    posted = production.post_due_stock(_at(16, 19, 1))[0]

    assert posted["status"] == AlwaysOnStockBatch.POSTED
    assert StockItem.objects.get(product=red).bags == 7
    assert StockItem.objects.get(product=blue).bags == 4
    assert StockReceipt.objects.count() == 2


def test_color_correction_reduces_the_warehouse_receipt(boss):
    product = _product()
    _closed_run(bags=10)
    AlwaysOnColorProductMapping.objects.create(
        camera="cam3", color="red", product=product,
    )
    with patch.object(production.timezone, "now", return_value=_at(16, 12)):
        production.record_correction(
            "cam3", "red", 2, "ложное срабатывание", boss,
        )

    posted = production.post_due_stock(_at(16, 19))[0]

    assert posted["total_bags"] == 8
    assert posted["items"][0]["correction_bags"] == -2
    assert AlwaysOnProductionCorrection.objects.get().delta == -2
    assert StockItem.objects.get(product=product).bags == 8


def test_display_archive_does_not_duplicate_the_production_ledger(boss):
    MonoblockCameraSettings.objects.create(always_on_camera_sources=["cam3"])
    first = _at(16, 12)
    analytics.record_snapshot({"processors": [{
        "cam": "cam3", "total": 100, "mode": "always_on", "running": True,
        "per_color": {"Red_50": 100},
    }]}, observed_at=first)

    with patch.object(analytics.timezone, "now", return_value=first):
        analytics.archive_camera("cam3", "закрытие экрана", boss)
    analytics.record_snapshot({"processors": [{
        "cam": "cam3", "total": 140, "mode": "always_on", "running": True,
        "per_color": {"Red_50": 140},
    }]}, observed_at=first + timedelta(minutes=1))

    assert sum(
        AlwaysOnProductionRun.objects.values_list("model_bags", flat=True)
    ) == 140
    assert analytics.today_payload()["all_time_total"] == 40
