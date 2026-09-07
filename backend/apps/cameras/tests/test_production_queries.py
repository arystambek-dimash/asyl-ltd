from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from django.db import connection, connections, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.cameras import production
from apps.cameras.models import (
    ANALYTICS_SCOPE_AI247,
    AlwaysOnProductionRun,
    AlwaysOnStockBatch,
    AlwaysOnStockPosting,
    ContinuousCameraRole,
)
from apps.catalog.models import Product
from apps.warehouse.models import Warehouse
from apps.warehouse.services import receive_stock

pytestmark = pytest.mark.django_db


@pytest.mark.django_db(transaction=True)
def test_stale_cleanup_skips_busy_run_and_preserves_concurrent_refresh():
    now = timezone.now()
    old = now - timedelta(minutes=10)
    runs = [
        AlwaysOnProductionRun.objects.create(
            camera=camera,
            color="red",
            business_day=production.business_day_for(now),
            started_at=old,
            last_counted_at=old,
            model_bags=1,
        )
        for camera in ("cam3", "cam4")
    ]

    def cleanup():
        try:
            return production.close_stale_runs(now)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            busy = AlwaysOnProductionRun.objects.select_for_update().get(pk=runs[0].pk)
            # The worker must close the other camera without waiting for this
            # transaction to finish; a plain global UPDATE would block here.
            assert pool.submit(cleanup).result(timeout=5) == 1
            busy.last_counted_at = now
            busy.save(update_fields=["last_counted_at"])
    runs[0].refresh_from_db()
    runs[1].refresh_from_db()
    assert runs[0].ended_at is None
    assert runs[0].last_counted_at == now
    assert runs[1].ended_at == old


def test_reading_production_projects_stale_status_without_mutating_any_camera():
    now = timezone.now()
    rows = []
    for camera in ("cam3", "cam4"):
        ContinuousCameraRole.objects.create(
            camera=camera, analytics_scope=ANALYTICS_SCOPE_AI247
        )
        rows.append(
            AlwaysOnProductionRun.objects.create(
                camera=camera,
                color="red",
                business_day=production.business_day_for(now),
                started_at=now - timedelta(minutes=10),
                last_counted_at=now - timedelta(minutes=6),
                model_bags=5,
            )
        )
    with CaptureQueriesContext(connection) as queries:
        result = production.production_payload("cam3")
    for row in rows:
        row.refresh_from_db()
        assert row.ended_at is None
    assert result["runs"][0]["status"] == "closed"
    assert result["runs"][0]["ended_at"] is not None
    assert all(
        "FOR UPDATE" not in q["sql"] and not q["sql"].startswith("UPDATE")
        for q in queries
    )


def test_production_batch_query_count_does_not_grow_with_history():
    warehouse = Warehouse.objects.get(code="main")
    product = Product.objects.create(name="Flour", color="Red", weight_kg=50, price=100)
    day = timezone.localdate()

    def add_batch(offset):
        batch = AlwaysOnStockBatch.objects.create(
            camera="cam3",
            warehouse=warehouse,
            business_day=day - timedelta(days=offset),
            scheduled_for=production.scheduled_for(day - timedelta(days=offset)),
            status=AlwaysOnStockBatch.POSTED,
            total_bags=2,
        )
        receipt = receive_stock(product, 2, user=None, warehouse=warehouse)
        AlwaysOnStockPosting.objects.create(
            batch=batch,
            color="red",
            product=product,
            detected_bags=2,
            posted_bags=2,
            receipt=receipt,
        )

    add_batch(1)
    with CaptureQueriesContext(connection) as first:
        production.production_payload("cam3")
    for offset in range(2, 32):
        add_batch(offset)
    with CaptureQueriesContext(connection) as full:
        result = production.production_payload("cam3")
    print(f"production queries: 1 batch={len(first)}, 31 batches={len(full)}")
    assert len(result["batches"]) == 31
    assert all(
        batch["items"][0]["product_label"] == str(product)
        for batch in result["batches"]
    )
    assert len(full) == len(first)
