from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

import pytest
from django.db import connection, connections
from django.utils import timezone

from apps.cameras import ai, continuous, event_sync
from apps.cameras.models import (
    ANALYTICS_SCOPE_AI247,
    AlwaysOnCounterCursor,
    AlwaysOnDailyAnalytics,
    AlwaysOnImportedEvent,
    ContinuousCameraRole,
)

pytestmark = pytest.mark.django_db(transaction=True)


def test_two_importers_of_same_camera_commit_one_page_once():
    ContinuousCameraRole.objects.create(
        camera="cam3", analytics_scope=ANALYTICS_SCOPE_AI247
    )
    AlwaysOnCounterCursor.objects.create(
        camera="cam3",
        last_event_id=0,
        event_compat_total=0,
        event_boundary_validated=True,
    )
    now = timezone.now()
    page = event_sync.EventPage(
        events=(
            event_sync.CountEvent(
                upstream_event_id=1,
                occurred_at=now,
                camera="cam3",
                source="sub",
                mode="always_on",
                continuous_analytics=True,
                analytics_scope=ANALYTICS_SCOPE_AI247,
                class_name="Red_50",
                total_after=1,
            ),
        ),
        next_after_id=1,
        has_more=False,
        enrichment_pending=False,
        journal_id=None,
    )
    barrier = Barrier(2)

    def apply():
        try:
            barrier.wait(timeout=5)
            return event_sync.apply_page(camera="cam3", page=page, requested_after_id=0)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(apply) for _ in range(2)]
        results = [future.result(timeout=5) for future in futures]
    assert sorted(results) == [(0, 0, 1), (1, 0, 1)]
    assert AlwaysOnDailyAnalytics.objects.get(camera="cam3").model_total == 1
    assert AlwaysOnImportedEvent.objects.filter(camera="cam3").count() == 1
    assert AlwaysOnCounterCursor.objects.get(camera="cam3").last_event_id == 1


@pytest.mark.parametrize("failure", [None, "network", "unexpected"])
def test_parallel_cameras_preserve_counts_isolate_failures_and_close_connections(
    settings, failure
):
    settings.CAMERA_EVENT_SYNC_WORKERS = 2
    for camera in ("cam3", "cam4"):
        ContinuousCameraRole.objects.create(
            camera=camera, analytics_scope=ANALYTICS_SCOPE_AI247
        )
    barrier = Barrier(2)
    connections_used = []
    original = event_sync.sync_camera

    def run(camera):
        # Neither independent camera may wait for the other to complete.
        barrier.wait(timeout=5)
        try:
            return original(camera)
        finally:
            if connection.connection is not None:
                connections_used.append(connection.connection)

    def events(camera, after_id, limit):
        if camera == "cam3" and failure:
            if failure == "network":
                raise ai.AiUnavailable("offline")
            raise RuntimeError("unexpected importer failure")
        return {
            "events": [
                {
                    "id": 1,
                    "created_at": timezone.now().isoformat(),
                    "cam": camera,
                    "source": "sub",
                    "mode": "always_on",
                    "continuous_analytics": True,
                    "analytics_scope": ANALYTICS_SCOPE_AI247,
                    "class_name": "Red_50",
                    "total_after": 1,
                }
            ],
            "next_after_id": 1,
            "has_more": False,
        }

    with (
        patch.object(event_sync, "sync_camera", side_effect=run),
        patch.object(ai, "count_events", side_effect=events),
    ):
        if failure == "unexpected":
            with pytest.raises(RuntimeError, match="unexpected importer failure"):
                continuous._record_counts({}, ["cam3", "cam4", "cam4"], {})
        else:
            continuous._record_counts({}, ["cam3", "cam4", "cam4"], {})

    assert AlwaysOnDailyAnalytics.objects.get(camera="cam4").model_total == 1
    assert AlwaysOnImportedEvent.objects.filter(camera="cam4").count() == 1
    if failure:
        cursor = AlwaysOnCounterCursor.objects.get(camera="cam3")
        assert cursor.event_sync_error
        assert cursor.event_caught_up_at is None
        assert not AlwaysOnDailyAnalytics.objects.filter(camera="cam3").exists()
    else:
        assert AlwaysOnDailyAnalytics.objects.get(camera="cam3").model_total == 1
    assert len(connections_used) == 2
    assert all(conn.closed for conn in connections_used)
