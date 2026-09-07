from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.cameras import event_sync
from apps.cameras.models import (
    ANALYTICS_SCOPE_AI247,
    AlwaysOnCounterCursor,
    AlwaysOnDailyAnalytics,
    AlwaysOnImportedEvent,
    AlwaysOnProductionRun,
    ContinuousCameraRole,
)

pytestmark = pytest.mark.django_db


def _page(count=20):
    now = timezone.now() - timedelta(seconds=count + 60)
    return event_sync.EventPage(
        events=tuple(
            event_sync.CountEvent(
                upstream_event_id=index,
                occurred_at=now + timedelta(seconds=index),
                camera="cam3",
                source="sub",
                mode="always_on",
                continuous_analytics=True,
                analytics_scope=ANALYTICS_SCOPE_AI247,
                class_name="Red_50",
                total_after=index,
            )
            for index in range(1, count + 1)
        ),
        next_after_id=count,
        has_more=False,
        enrichment_pending=False,
        journal_id=None,
    )


@pytest.fixture(autouse=True)
def _camera():
    ContinuousCameraRole.objects.create(
        camera="cam3", analytics_scope=ANALYTICS_SCOPE_AI247
    )
    AlwaysOnCounterCursor.objects.create(
        camera="cam3",
        last_event_id=0,
        event_compat_total=0,
        event_boundary_validated=True,
    )


@pytest.mark.parametrize("count", [20, 500])
def test_page_batches_journal_io_and_checks_each_shift_once(count):
    with CaptureQueriesContext(connection) as queries:
        result = event_sync.apply_page(
            camera="cam3", page=_page(count), requested_after_id=0
        )
    sql = [q["sql"] for q in queries]
    journal = [q for q in sql if '"cameras_alwaysonimportedevent"' in q]
    shifts = [q for q in sql if '"cameras_alwaysonstockbatch"' in q]
    print(
        f"{count} events: total={len(sql)}, journal={len(journal)}, shifts={len(shifts)}"
    )
    assert result == (count, 0, count)
    assert AlwaysOnImportedEvent.objects.count() == count
    assert AlwaysOnDailyAnalytics.objects.get(camera="cam3").model_total == count
    assert AlwaysOnProductionRun.objects.get(camera="cam3").model_bags == count
    assert len(journal) <= 2
    assert len(shifts) <= 1


def test_bulk_journal_failure_rolls_back_projections_and_cursor():
    with patch.object(
        AlwaysOnImportedEvent.objects,
        "bulk_create",
        side_effect=RuntimeError("journal write failed"),
    ):
        with pytest.raises(RuntimeError, match="journal write failed"):
            event_sync.apply_page(camera="cam3", page=_page(2), requested_after_id=0)
    assert not AlwaysOnImportedEvent.objects.exists()
    assert not AlwaysOnDailyAnalytics.objects.exists()
    assert not AlwaysOnProductionRun.objects.exists()
    assert AlwaysOnCounterCursor.objects.get(camera="cam3").last_event_id == 0
