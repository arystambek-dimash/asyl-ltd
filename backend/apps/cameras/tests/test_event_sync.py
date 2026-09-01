from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from unittest.mock import patch

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.cameras import ai, analytics, continuous, event_sync, production
from apps.cameras.models import (
    AlwaysOnCounterCursor,
    AlwaysOnDailyAnalytics,
    AlwaysOnImportedEvent,
    AlwaysOnProductionRun,
    AlwaysOnStockBatch,
    MonoblockCameraSettings,
)

pytestmark = pytest.mark.django_db


def _at(second: int) -> datetime:
    return datetime(2026, 8, 25, 8, 36, second, tzinfo=dt_timezone.utc)


def _event(
    event_id: int,
    total_after: int,
    *,
    second: int | None = None,
    mode: str = "always_on",
    camera: str = "cam3",
    class_name: str = "Red_50",
    color: str | None = None,
    brand: str | None = None,
    continuous_analytics: bool | None = None,
) -> dict:
    event = {
        "id": event_id,
        "created_at": _at(second if second is not None else event_id).isoformat(),
        "cam": camera,
        "source": "sub",
        "mode": mode,
        "generation": 1,
        "frame": 6900 + event_id,
        "track_id": event_id,
        "class_id": 0,
        "class_name": class_name,
        "confidence": 0.8,
        "direction": "negative",
        "point_x": 354.25,
        "point_y": 199.125,
        "weight_kg": 50.0,
        "total_after": total_after,
        "total_weight_after": float(event_id * 50),
    }
    if continuous_analytics is not None:
        event["continuous_analytics"] = continuous_analytics
    if color is not None:
        event.update(
            {
                "color": color,
                "color_confidence": 0.997,
                "brand": brand or "unknown",
                "brand_confidence": 0.91,
                "sku": f"{color.split('_', 1)[0].lower()}_{brand or 'unknown'}",
                "classification_status": "recognized",
            }
        )
    return event


def _page(
    events: list[dict],
    *,
    after_id: int = 0,
    has_more: bool = False,
    enrichment_pending: bool = False,
) -> dict:
    return {
        "events": events,
        "next_after_id": events[-1]["id"] if events else after_id,
        "has_more": has_more,
        "enrichment_pending": enrichment_pending,
    }


def test_ai_client_reads_the_bounded_camera_event_page():
    response = _page([])
    with patch.object(ai, "_call", return_value=response) as call:
        assert ai.count_events("3", 2, 500) == response

    call.assert_called_once_with(
        "GET",
        "/events?after_id=2&limit=500&cam=cam3",
        none_on_404=True,
    )


@pytest.mark.parametrize(
    ("after_id", "limit"),
    [(-1, 500), (True, 500), (0, 0), (0, 501), (0, True)],
)
def test_ai_client_rejects_invalid_event_page_bounds(after_id, limit):
    with pytest.raises(ValueError):
        ai.count_events("cam3", after_id, limit)


def test_sync_backfills_from_event_zero_not_the_stale_snapshot_total():
    AlwaysOnCounterCursor.objects.create(
        camera="cam3",
        last_total=48578,
        last_per_color={"red": 35808, "blue": 10327, "green": 2443},
        last_mode="always_on",
    )
    AlwaysOnDailyAnalytics.objects.create(
        camera="cam3",
        day=timezone.localdate(_at(1)),
        model_total=2291,
        model_per_color={"red": 2291},
    )
    AlwaysOnDailyAnalytics.objects.create(
        camera="cam3",
        day=timezone.localdate(_at(1)) - timedelta(days=1),
        model_total=4337,
        model_per_color={"red": 4337},
    )
    response = _page([_event(1, 2292), _event(2, 2293)])

    with patch.object(ai, "count_events", return_value=response) as request:
        result = event_sync.sync_camera("cam3")

    request.assert_called_once_with("cam3", 0, 500)
    assert result == event_sync.SyncResult(True, 2, 0, 1, 2, True)
    cursor = AlwaysOnCounterCursor.objects.get(camera="cam3")
    assert cursor.last_event_id == 2
    assert cursor.last_total == 48580
    assert cursor.event_compat_total == 48580
    assert cursor.last_per_color == {
        "red": 35810,
        "blue": 10327,
        "green": 2443,
    }
    assert cursor.event_sync_supported is True
    assert cursor.event_boundary_validated is True
    assert cursor.event_caught_up_at is not None
    row = AlwaysOnDailyAnalytics.objects.get(
        camera="cam3",
        day=timezone.localdate(_at(1)),
    )
    assert row.model_total == 2293
    assert row.model_per_color == {"red": 2293}
    assert AlwaysOnImportedEvent.objects.count() == 2
    assert sum(AlwaysOnProductionRun.objects.values_list("model_bags", flat=True)) == 2


def test_replaying_an_applied_page_does_not_count_events_twice():
    page = event_sync.parse_page(
        _page([_event(1, 1), _event(2, 2)]),
        camera="cam3",
        after_id=0,
    )
    assert event_sync.apply_page(
        camera="cam3",
        page=page,
        requested_after_id=0,
    )[:2] == (2, 0)
    assert event_sync.apply_page(
        camera="cam3",
        page=page,
        requested_after_id=0,
    )[:2] == (0, 0)

    assert AlwaysOnImportedEvent.objects.count() == 2
    assert AlwaysOnDailyAnalytics.objects.get(camera="cam3").model_total == 2
    assert sum(AlwaysOnProductionRun.objects.values_list("model_bags", flat=True)) == 2


def test_ordered_event_colors_create_new_runs_and_replay_is_idempotent():
    page = event_sync.parse_page(
        _page(
            [
                _event(1, 1, class_name="Red_50"),
                _event(2, 2, class_name="Green_50"),
                _event(3, 3, class_name="Blue_50"),
                _event(4, 4, class_name="Red_50"),
            ]
        ),
        camera="cam3",
        after_id=0,
    )

    assert event_sync.apply_page(
        camera="cam3",
        page=page,
        requested_after_id=0,
    )[:2] == (4, 0)
    runs = list(AlwaysOnProductionRun.objects.order_by("started_at", "id"))
    assert [row.color for row in runs] == ["red", "green", "blue", "red"]
    assert [row.model_bags for row in runs] == [1, 1, 1, 1]
    assert [row.ended_at for row in runs] == [_at(1), _at(2), _at(3), None]

    assert event_sync.apply_page(
        camera="cam3",
        page=page,
        requested_after_id=0,
    )[:2] == (0, 0)
    assert AlwaysOnImportedEvent.objects.count() == 4
    assert AlwaysOnProductionRun.objects.count() == 4
    row = AlwaysOnDailyAnalytics.objects.get(camera="cam3")
    assert row.model_total == 4
    assert row.model_per_color == {"red": 2, "green": 1, "blue": 1}


def test_classified_color_brand_and_sku_are_persisted_and_drive_analytics():
    page = event_sync.parse_page(
        _page(
            [
                _event(
                    1,
                    1,
                    class_name="Red_50",
                    color="Blue_50",
                    brand="korol",
                )
            ]
        ),
        camera="cam3",
        after_id=0,
    )

    assert event_sync.apply_page(
        camera="cam3",
        page=page,
        requested_after_id=0,
    )[:2] == (1, 0)

    imported = AlwaysOnImportedEvent.objects.get()
    assert imported.class_name == "Red_50"
    assert imported.color == "Blue_50"
    assert imported.color_confidence == pytest.approx(0.997)
    assert imported.brand == "korol"
    assert imported.brand_confidence == pytest.approx(0.91)
    assert imported.sku == "blue_korol"
    assert imported.classification_status == "recognized"
    analytics_row = AlwaysOnDailyAnalytics.objects.get(camera="cam3")
    assert analytics_row.model_per_color == {"blue": 1}
    assert analytics_row.model_per_brand == {"korol": 1}
    assert AlwaysOnProductionRun.objects.get(camera="cam3").color == "blue"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("color", ""),
        ("brand", 123),
        ("sku", "x" * 256),
        ("classification_status", "x" * 33),
        ("color_confidence", True),
        ("color_confidence", float("nan")),
        ("brand_confidence", 1.01),
    ],
)
def test_invalid_classification_enrichment_is_rejected(field, value):
    event = _event(1, 1)
    event[field] = value

    with pytest.raises(event_sync.EventSyncError, match=field):
        event_sync.parse_page(_page([event]), camera="cam3", after_id=0)

    assert not AlwaysOnImportedEvent.objects.exists()


def test_non_boolean_continuous_analytics_marker_is_rejected():
    event = _event(1, 1)
    event["continuous_analytics"] = 1

    with pytest.raises(
        event_sync.EventSyncError,
        match="continuous_analytics",
    ):
        event_sync.parse_page(_page([event]), camera="cam3", after_id=0)


def test_replayed_event_cannot_change_classification_enrichment():
    AlwaysOnCounterCursor.objects.create(
        camera="cam3",
        last_event_id=0,
        event_compat_total=0,
        event_sync_supported=True,
        event_boundary_validated=True,
    )
    AlwaysOnImportedEvent.objects.create(
        camera="cam3",
        upstream_event_id=1,
        occurred_at=_at(1),
        source="sub",
        mode="always_on",
        class_name="Red_50",
        color="Red_50",
        color_confidence=0.9,
        brand="korol",
        brand_confidence=0.91,
        sku="red_korol",
        classification_status="recognized",
        total_after=1,
        applied_to_analytics=True,
    )
    page = event_sync.parse_page(
        _page(
            [
                _event(
                    1,
                    1,
                    class_name="Red_50",
                    color="Blue_50",
                    brand="korol",
                )
            ]
        ),
        camera="cam3",
        after_id=0,
    )

    with pytest.raises(event_sync.EventSyncError, match="changed contents"):
        event_sync.apply_page(
            camera="cam3",
            page=page,
            requested_after_id=0,
        )

    cursor = AlwaysOnCounterCursor.objects.get(camera="cam3")
    assert cursor.last_event_id == 0
    assert not AlwaysOnDailyAnalytics.objects.exists()


def test_stale_concurrent_page_cannot_overwrite_the_newer_caught_up_state():
    old_page = event_sync.parse_page(
        _page([_event(1, 1)]),
        camera="cam3",
        after_id=0,
    )
    newer_page = event_sync.parse_page(
        _page([_event(2, 2)], after_id=1, has_more=True),
        camera="cam3",
        after_id=1,
    )
    event_sync.apply_page(
        camera="cam3",
        page=old_page,
        requested_after_id=0,
    )
    event_sync.apply_page(
        camera="cam3",
        page=newer_page,
        requested_after_id=1,
    )

    stale_result = event_sync.apply_page(
        camera="cam3",
        page=old_page,
        requested_after_id=0,
    )

    cursor = AlwaysOnCounterCursor.objects.get(camera="cam3")
    assert stale_result == (0, 0, 2)
    assert cursor.last_event_id == 2
    assert cursor.event_caught_up_at is None
    assert AlwaysOnDailyAnalytics.objects.get(camera="cam3").model_total == 2


def test_page_cursor_and_crm_updates_roll_back_together():
    page = event_sync.parse_page(
        _page([_event(1, 1), _event(2, 2)]),
        camera="cam3",
        after_id=0,
    )
    original = analytics.record_model_delta
    calls = 0

    def fail_second(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("database write failed")
        return original(**kwargs)

    with patch.object(analytics, "record_model_delta", side_effect=fail_second):
        with pytest.raises(RuntimeError, match="database write failed"):
            event_sync.apply_page(
                camera="cam3",
                page=page,
                requested_after_id=0,
            )

    assert not AlwaysOnImportedEvent.objects.exists()
    assert not AlwaysOnDailyAnalytics.objects.exists()
    assert not AlwaysOnProductionRun.objects.exists()
    assert not AlwaysOnCounterCursor.objects.exists()


def test_session_event_is_durable_but_not_added_to_always_on_analytics():
    response = _page([_event(1, 1, mode="session")])
    with patch.object(ai, "count_events", return_value=response):
        result = event_sync.sync_camera("cam3")

    assert result.processed == 0
    assert result.ignored == 1
    imported = AlwaysOnImportedEvent.objects.get()
    assert imported.mode == "session"
    assert imported.continuous_analytics is False
    assert imported.color is None
    assert imported.brand is None
    assert imported.sku is None
    assert not imported.applied_to_analytics
    assert AlwaysOnCounterCursor.objects.get(camera="cam3").last_event_id == 1
    assert not AlwaysOnDailyAnalytics.objects.exists()


def test_flagged_session_event_updates_continuous_analytics_exactly_once():
    page = event_sync.parse_page(
        _page(
            [
                _event(
                    1,
                    1,
                    mode="session",
                    continuous_analytics=True,
                    color="Green_50",
                    brand="pioneer",
                )
            ]
        ),
        camera="cam3",
        after_id=0,
    )

    assert event_sync.apply_page(
        camera="cam3",
        page=page,
        requested_after_id=0,
    )[:2] == (1, 0)
    assert event_sync.apply_page(
        camera="cam3",
        page=page,
        requested_after_id=0,
    )[:2] == (0, 0)

    imported = AlwaysOnImportedEvent.objects.get()
    assert imported.mode == "session"
    assert imported.continuous_analytics is True
    assert imported.applied_to_analytics is True
    analytics_row = AlwaysOnDailyAnalytics.objects.get(camera="cam3")
    assert analytics_row.model_total == 1
    assert analytics_row.model_per_color == {"green": 1}
    assert analytics_row.model_per_brand == {"pioneer": 1}
    assert sum(
        AlwaysOnProductionRun.objects.values_list("model_bags", flat=True)
    ) == 1


def test_sync_follows_pages_until_the_upstream_is_caught_up():
    responses = [
        _page([_event(1, 1)], has_more=True),
        _page([_event(2, 2)], after_id=1),
    ]
    with patch.object(ai, "count_events", side_effect=responses) as request:
        result = event_sync.sync_camera("cam3")

    assert [call.args for call in request.call_args_list] == [
        ("cam3", 0, 500),
        ("cam3", 1, 500),
    ]
    assert result == event_sync.SyncResult(True, 2, 0, 2, 2, True)


def test_pending_enrichment_does_not_claim_event_stream_is_caught_up():
    response = _page([], enrichment_pending=True)
    with patch.object(ai, "count_events", return_value=response):
        result = event_sync.sync_camera("cam3")

    assert result == event_sync.SyncResult(True, 0, 0, 1, 0, False)
    cursor = AlwaysOnCounterCursor.objects.get(camera="cam3")
    assert cursor.last_event_id == 0
    assert cursor.event_boundary_validated is False
    assert cursor.event_caught_up_at is None


def test_explicit_404_keeps_legacy_snapshot_mode_only_before_cutover():
    AlwaysOnCounterCursor.objects.create(camera="cam3", last_total=10)
    with patch.object(ai, "count_events", return_value=None):
        result = event_sync.sync_camera("cam3")
    assert not result.supported
    assert AlwaysOnCounterCursor.objects.get(camera="cam3").last_event_id is None

    AlwaysOnCounterCursor.objects.filter(camera="cam3").update(
        last_event_id=0,
        event_compat_total=10,
        event_sync_supported=True,
    )
    with patch.object(ai, "count_events", return_value=None):
        with pytest.raises(event_sync.EventSyncError, match="disappeared"):
            event_sync.sync_camera("cam3")


def test_snapshot_is_ignored_after_event_mode_cutover():
    AlwaysOnCounterCursor.objects.create(
        camera="cam3",
        last_total=10,
        last_event_id=0,
        event_compat_total=10,
        event_sync_supported=True,
    )
    analytics.record_snapshot(
        {
            "processors": [
                {
                    "cam": "cam3",
                    "total": 20,
                    "mode": "always_on",
                    "running": True,
                    "per_color": {"Red_50": 20},
                }
            ]
        }
    )

    assert AlwaysOnCounterCursor.objects.get(camera="cam3").last_total == 10
    assert not AlwaysOnDailyAnalytics.objects.exists()


@pytest.mark.parametrize(
    "payload",
    [
        {"events": [], "next_after_id": 1, "has_more": False},
        {"events": [], "next_after_id": 0, "has_more": True},
        _page([_event(1, 2292, camera="cam2")]),
        {
            "events": [_event(2, 2293), _event(1, 2292)],
            "next_after_id": 1,
            "has_more": False,
        },
    ],
)
def test_malformed_page_never_advances_the_cursor(payload):
    with pytest.raises(event_sync.EventSyncError):
        event_sync.parse_page(payload, camera="cam3", after_id=0)
    assert not AlwaysOnCounterCursor.objects.exists()


def test_late_event_for_a_terminal_stock_shift_is_not_skipped():
    event = _event(1, 1)
    occurred_at = _at(1)
    day = production.business_day_for(occurred_at)
    AlwaysOnStockBatch.objects.create(
        camera="cam3",
        business_day=day,
        scheduled_for=production.scheduled_for(day),
        status=AlwaysOnStockBatch.POSTED,
    )
    page = event_sync.parse_page(
        _page([event]),
        camera="cam3",
        after_id=0,
    )

    with pytest.raises(event_sync.EventSyncError, match="already posted"):
        event_sync.apply_page(
            camera="cam3",
            page=page,
            requested_after_id=0,
        )
    assert not AlwaysOnImportedEvent.objects.exists()
    assert not AlwaysOnCounterCursor.objects.exists()


def test_reconcile_uses_snapshots_only_for_an_explicit_legacy_404():
    MonoblockCameraSettings.objects.create(always_on_camera_sources=["cam3"])
    current = {
        "cameras": ["cam3"],
        "source": "sub",
        "processors": [
            {
                "cam": "cam3",
                "total": 4,
                "mode": "always_on",
                "running": True,
            }
        ],
    }
    with (
        patch.object(ai, "always_on_status", return_value=current),
        patch.object(
            event_sync,
            "sync_camera",
            return_value=event_sync.SyncResult(False, 0, 0, 0, None, False),
        ),
        patch.object(analytics, "record_snapshot") as snapshot,
    ):
        continuous.reconcile()
    snapshot.assert_called_once_with(current, cameras={"cam3"})

    with (
        patch.object(ai, "always_on_status", return_value=current),
        patch.object(
            event_sync,
            "sync_camera",
            side_effect=ai.AiUnavailable("timeout"),
        ),
        patch.object(analytics, "record_snapshot") as snapshot,
    ):
        continuous.reconcile()
    snapshot.assert_not_called()


def test_removing_an_event_camera_performs_and_retries_a_final_drain():
    MonoblockCameraSettings.objects.create(always_on_camera_sources=[])
    AlwaysOnCounterCursor.objects.create(
        camera="cam3",
        last_event_id=9,
        event_compat_total=0,
        event_sync_supported=True,
        event_boundary_validated=True,
        event_caught_up_at=_at(9),
    )
    before_stop = {"cameras": ["cam3"], "source": "sub", "processors": []}
    after_stop = {"cameras": [], "source": "sub", "processors": []}

    with (
        patch.object(ai, "always_on_status", return_value=before_stop),
        patch.object(ai, "configure_always_on", return_value=after_stop) as configure,
        patch.object(
            event_sync,
            "sync_camera",
            side_effect=ai.AiUnavailable("timeout"),
        ) as sync,
    ):
        continuous.reconcile()

    configure.assert_called_once_with([], "sub")
    sync.assert_called_once_with("cam3")
    assert AlwaysOnCounterCursor.objects.get(camera="cam3").event_caught_up_at is None

    with (
        patch.object(ai, "always_on_status", return_value=after_stop),
        patch.object(ai, "configure_always_on") as configure,
        patch.object(
            event_sync,
            "sync_camera",
            return_value=event_sync.SyncResult(True, 0, 0, 1, 9, True),
        ) as sync,
    ):
        continuous.reconcile()

    configure.assert_not_called()
    sync.assert_called_once_with("cam3")


def test_reconcile_recovers_a_crash_after_remote_stop_before_second_barrier():
    MonoblockCameraSettings.objects.create(always_on_camera_sources=[])
    AlwaysOnCounterCursor.objects.create(
        camera="cam3",
        last_event_id=9,
        event_compat_total=0,
        event_sync_supported=True,
        event_boundary_validated=True,
        event_caught_up_at=_at(9),
    )
    event_sync.request_stop_drain("cam3")
    stopped = {"cameras": [], "source": "sub", "processors": []}

    with (
        patch.object(ai, "always_on_status", return_value=stopped),
        patch.object(ai, "configure_always_on") as configure,
        patch.object(ai, "count_events", return_value=_page([], after_id=9)),
    ):
        continuous.reconcile()

    configure.assert_not_called()
    cursor = AlwaysOnCounterCursor.objects.get(camera="cam3")
    assert cursor.last_event_id == 9
    assert cursor.event_stop_drain_requested_at is None
    assert cursor.event_stop_confirmed_at is None
    assert cursor.event_drain_required_at is None
    assert cursor.event_caught_up_at is not None


def test_database_fences_snapshot_updates_during_an_old_image_rollback():
    page = event_sync.parse_page(
        _page([_event(1, 1)]),
        camera="cam3",
        after_id=0,
    )
    event_sync.apply_page(camera="cam3", page=page, requested_after_id=0)

    with pytest.raises(IntegrityError), transaction.atomic():
        AlwaysOnCounterCursor.objects.filter(camera="cam3").update(last_total=2)

    cursor = AlwaysOnCounterCursor.objects.get(camera="cam3")
    assert cursor.last_total == cursor.event_compat_total == 1
    assert AlwaysOnDailyAnalytics.objects.get(camera="cam3").model_total == 1


def test_initial_event_boundary_mismatch_is_rejected_without_double_counting():
    AlwaysOnCounterCursor.objects.create(camera="cam3", last_total=100)
    AlwaysOnDailyAnalytics.objects.create(
        camera="cam3",
        day=timezone.localdate(_at(1)),
        model_total=100,
    )
    page = event_sync.parse_page(
        _page([_event(1, 50)]),
        camera="cam3",
        after_id=0,
    )

    with pytest.raises(event_sync.EventSyncError, match="boundary"):
        event_sync.apply_page(camera="cam3", page=page, requested_after_id=0)

    assert not AlwaysOnImportedEvent.objects.exists()
    assert AlwaysOnDailyAnalytics.objects.get(camera="cam3").model_total == 100
    assert AlwaysOnCounterCursor.objects.get(camera="cam3").last_event_id is None


def test_non_404_event_capability_is_one_way_even_if_the_first_page_is_bad():
    malformed = {"events": [], "next_after_id": 1, "has_more": False}
    with patch.object(ai, "count_events", return_value=malformed):
        with pytest.raises(event_sync.EventSyncError):
            event_sync.sync_camera("cam3")

    assert AlwaysOnCounterCursor.objects.get(camera="cam3").event_sync_supported is True
    with patch.object(ai, "count_events", return_value=None):
        with pytest.raises(event_sync.EventSyncError, match="disappeared"):
            event_sync.sync_camera("cam3")


def test_pre_boundary_inflight_page_cannot_complete_a_required_final_drain():
    first = event_sync.parse_page(
        _page([_event(1, 1)]),
        camera="cam3",
        after_id=0,
    )
    event_sync.apply_page(
        camera="cam3",
        page=first,
        requested_after_id=0,
        synced_at=_at(1),
    )
    event_sync.require_fresh_drain("cam3", required_at=_at(5))

    stale = event_sync.parse_page(
        _page([_event(2, 2)], after_id=1),
        camera="cam3",
        after_id=1,
    )
    event_sync.apply_page(
        camera="cam3",
        page=stale,
        requested_after_id=1,
        synced_at=_at(4),
    )
    cursor = AlwaysOnCounterCursor.objects.get(camera="cam3")
    assert cursor.last_event_id == 2
    assert cursor.event_caught_up_at is None
    assert cursor.event_drain_required_at == _at(5)

    empty = event_sync.parse_page(
        _page([], after_id=2),
        camera="cam3",
        after_id=2,
    )
    event_sync.apply_page(
        camera="cam3",
        page=empty,
        requested_after_id=2,
        synced_at=_at(6),
    )
    cursor.refresh_from_db()
    assert cursor.event_caught_up_at == _at(6)
    assert cursor.event_drain_required_at is None


def test_event_source_and_mode_are_closed_contract_enums():
    event = _event(1, 1)
    event["source"] = "other"

    with pytest.raises(event_sync.EventSyncError, match="source"):
        event_sync.parse_page(_page([event]), camera="cam3", after_id=0)

    event = _event(1, 1)
    event["mode"] = "alwayson"
    with pytest.raises(event_sync.EventSyncError, match="mode"):
        event_sync.parse_page(_page([event]), camera="cam3", after_id=0)


def test_journal_identity_binds_once_and_detects_sqlite_recreation():
    first_payload = _page([_event(1, 1)])
    first_payload["journal_id"] = "11111111-1111-4111-8111-111111111111"
    first = event_sync.parse_page(first_payload, camera="cam3", after_id=0)
    event_sync.apply_page(camera="cam3", page=first, requested_after_id=0)

    cursor = AlwaysOnCounterCursor.objects.get(camera="cam3")
    assert cursor.event_journal_id == "11111111-1111-4111-8111-111111111111"

    recreated_payload = _page([], after_id=1)
    recreated_payload["journal_id"] = "22222222-2222-4222-8222-222222222222"
    recreated = event_sync.parse_page(
        recreated_payload,
        camera="cam3",
        after_id=1,
    )
    with pytest.raises(event_sync.EventSyncError, match="identity changed"):
        event_sync.apply_page(
            camera="cam3",
            page=recreated,
            requested_after_id=1,
        )

    cursor.refresh_from_db()
    assert cursor.last_event_id == 1


def test_late_journal_identity_requires_manual_continuity_verification():
    cursor = AlwaysOnCounterCursor.objects.create(
        camera="cam3",
        last_event_id=9,
        event_compat_total=0,
        event_sync_supported=True,
        event_boundary_validated=True,
    )
    payload = _page([], after_id=9)
    payload["journal_id"] = "11111111-1111-4111-8111-111111111111"
    page = event_sync.parse_page(payload, camera="cam3", after_id=9)

    with pytest.raises(event_sync.EventSyncError, match="manual continuity"):
        event_sync.apply_page(
            camera="cam3",
            page=page,
            requested_after_id=9,
        )

    cursor.refresh_from_db()
    assert cursor.event_journal_id is None
    assert cursor.last_event_id == 9
