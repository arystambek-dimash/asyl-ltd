import json
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from io import BytesIO
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.cameras import (
    ai,
    analytics,
    continuous,
    event_sync,
    health,
    production_runs,
)
from apps.cameras.color_resolution import camera_color_key
from apps.cameras.event_protocol import EVENT_PAGE_LIMIT
from apps.cameras.models import (
    ANALYTICS_SCOPE_AI247,
    ANALYTICS_SCOPE_SHIPPING,
    AlwaysOnCounterCursor,
    AlwaysOnDailyAnalytics,
    AlwaysOnImportedEvent,
    AlwaysOnProductionRun,
    AlwaysOnStockBatch,
    CameraHealthState,
    ContinuousCameraRole,
    MonoblockCameraSettings,
    ShippingDailyAnalytics,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _default_ai247_role():
    """Most journal tests exercise the legacy/default AI247 contour."""

    ContinuousCameraRole.objects.create(
        camera="cam3",
        analytics_scope=ANALYTICS_SCOPE_AI247,
    )


def _set_role(camera: str, analytics_scope: str) -> None:
    ContinuousCameraRole.objects.update_or_create(
        camera=camera,
        defaults={"analytics_scope": analytics_scope},
    )


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
    analytics_scope: str = ANALYTICS_SCOPE_AI247,
) -> dict:
    event = {
        "id": event_id,
        "created_at": _at(second if second is not None else event_id).isoformat(),
        "cam": camera,
        "source": "sub",
        "mode": mode,
        "analytics_scope": analytics_scope,
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


def _parse(
    events: list[dict],
    *,
    camera: str = "cam3",
    after_id: int = 0,
    **page,
) -> event_sync.EventPage:
    return event_sync.parse_page(
        _page(events, after_id=after_id, **page),
        camera=camera,
        after_id=after_id,
    )


def _apply(
    page: event_sync.EventPage,
    *,
    camera: str = "cam3",
    after_id: int = 0,
    **options,
):
    return event_sync.apply_page(
        camera=camera,
        page=page,
        requested_after_id=after_id,
        **options,
    )


def test_ai_client_reads_the_bounded_camera_event_page():
    response = _page([])
    with patch.object(ai, "_call", return_value=response) as call:
        assert ai.count_events("3", 2, 500) == response

    call.assert_called_once_with(
        "GET",
        "/events?after_id=2&limit=500&cam=cam3&contract_version=2",
        max_response_bytes=ai.EVENT_PAGE_MAX_BYTES,
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
    assert result == event_sync.SyncResult(2, 0, 1, 2, True)
    cursor = AlwaysOnCounterCursor.objects.get(camera="cam3")
    assert cursor.last_event_id == 2
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
    page = _parse([_event(1, 1), _event(2, 2)])
    assert _apply(page)[:2] == (2, 0)
    assert _apply(page)[:2] == (0, 0)

    assert AlwaysOnImportedEvent.objects.count() == 2
    assert AlwaysOnDailyAnalytics.objects.get(camera="cam3").model_total == 2
    assert sum(AlwaysOnProductionRun.objects.values_list("model_bags", flat=True)) == 2


def test_ordered_event_colors_create_new_runs_and_replay_is_idempotent():
    page = _parse(
        [
            _event(1, 1, class_name="Red_50"),
            _event(2, 2, class_name="Green_50"),
            _event(3, 3, class_name="Blue_50"),
            _event(4, 4, class_name="Red_50"),
        ],
    )

    assert _apply(page)[:2] == (4, 0)
    runs = list(AlwaysOnProductionRun.objects.order_by("started_at", "id"))
    assert [row.color for row in runs] == ["red", "green", "blue", "red"]
    assert [row.model_bags for row in runs] == [1, 1, 1, 1]
    assert [row.ended_at for row in runs] == [_at(1), _at(2), _at(3), None]

    assert _apply(page)[:2] == (0, 0)
    assert AlwaysOnImportedEvent.objects.count() == 4
    assert AlwaysOnProductionRun.objects.count() == 4
    row = AlwaysOnDailyAnalytics.objects.get(camera="cam3")
    assert row.model_total == 4
    assert row.model_per_color == {"red": 2, "green": 1, "blue": 1}


def test_classified_color_brand_and_sku_are_persisted_and_drive_analytics():
    page = _parse(
        [
            _event(
                1,
                1,
                class_name="Red_50",
                color="Blue_50",
                brand="korol",
            )
        ],
    )

    assert _apply(page)[:2] == (1, 0)

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
        _parse([event])

    assert not AlwaysOnImportedEvent.objects.exists()


@pytest.mark.parametrize(
    "created_at",
    [None, 1, "2026-09-01T10:00:00", "2026-02-30T10:00:00+05:00"],
)
def test_invalid_event_created_at_is_a_sync_error(created_at):
    event = _event(1, 1)
    event["created_at"] = created_at

    with pytest.raises(event_sync.EventSyncError, match="created_at"):
        _parse([event])


def test_non_boolean_continuous_analytics_marker_is_rejected():
    event = _event(1, 1)
    event["continuous_analytics"] = 1

    with pytest.raises(
        event_sync.EventSyncError,
        match="continuous_analytics",
    ):
        _parse([event])


@pytest.mark.parametrize("analytics_scope", [None, "", "legacy", True])
def test_event_analytics_scope_is_a_required_closed_contract(analytics_scope):
    event = _event(1, 1)
    event["analytics_scope"] = analytics_scope

    with pytest.raises(
        event_sync.EventSyncError,
        match="analytics_scope",
    ):
        _parse([event])


def test_legacy_unscoped_event_blocks_deploy_without_touching_either_ledger():
    """An old CV service may ignore contract_version=2; never infer its role."""

    MonoblockCameraSettings.objects.create(always_on_camera_sources=["cam3"])
    current = {
        "camera_sources": ["cam3"],
        "source": "sub",
        "analytics_scopes": {"cam3": ANALYTICS_SCOPE_AI247},
        "processors": [],
    }
    legacy_event = _event(1, 1)
    legacy_event.pop("analytics_scope")

    with (
        patch.object(ai, "always_on_status", return_value=current),
        patch.object(ai, "count_events", return_value=_page([legacy_event])),
    ):
        continuous.reconcile()

    cursor = AlwaysOnCounterCursor.objects.get(camera="cam3")
    assert "analytics_scope" in cursor.event_sync_error
    assert cursor.event_caught_up_at is None
    assert not AlwaysOnImportedEvent.objects.exists()
    assert not AlwaysOnDailyAnalytics.objects.exists()
    assert not ShippingDailyAnalytics.objects.exists()
    assert not AlwaysOnProductionRun.objects.exists()

    now = timezone.now()
    state = CameraHealthState.objects.create(
        status=CameraHealthState.HEALTHY,
        observed_status=CameraHealthState.HEALTHY,
        expected_count=10,
        online_count=10,
        last_checked_at=now,
    )
    deploy = health.state_payload(
        state,
        now=now,
        max_age=180,
        require_events=True,
    )
    assert deploy["event_sync"]["blocking"] is True
    assert deploy["event_sync"]["cameras"][0]["status"] == "error"
    assert health.exit_code(deploy) == 2


def test_replayed_event_cannot_change_classification_enrichment():
    AlwaysOnCounterCursor.objects.create(
        camera="cam3",
        last_event_id=0,
        event_sync_supported=True,
        event_boundary_validated=True,
    )
    AlwaysOnImportedEvent.objects.create(
        camera="cam3",
        upstream_event_id=1,
        occurred_at=_at(1),
        source="sub",
        mode="always_on",
        analytics_scope=ANALYTICS_SCOPE_AI247,
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
    page = _parse(
        [
            _event(
                1,
                1,
                class_name="Red_50",
                color="Blue_50",
                brand="korol",
            )
        ],
    )

    with pytest.raises(event_sync.EventSyncError, match="changed contents"):
        _apply(page)

    cursor = AlwaysOnCounterCursor.objects.get(camera="cam3")
    assert cursor.last_event_id == 0
    assert not AlwaysOnDailyAnalytics.objects.exists()
    assert not ShippingDailyAnalytics.objects.exists()


def test_stale_concurrent_page_cannot_overwrite_the_newer_caught_up_state():
    old_page = _parse([_event(1, 1)])
    newer_page = _parse([_event(2, 2)], after_id=1, has_more=True)
    _apply(old_page)
    _apply(newer_page, after_id=1)

    stale_result = _apply(old_page)

    cursor = AlwaysOnCounterCursor.objects.get(camera="cam3")
    assert stale_result == (0, 0, 2)
    assert cursor.last_event_id == 2
    assert cursor.event_caught_up_at is None
    assert AlwaysOnDailyAnalytics.objects.get(camera="cam3").model_total == 2


def test_page_cursor_and_crm_updates_roll_back_together():
    page = _parse([_event(1, 1), _event(2, 2)])
    original = analytics.record_counted_bag
    calls = 0

    def fail_second(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("database write failed")
        return original(**kwargs)

    with patch.object(analytics, "record_counted_bag", side_effect=fail_second):
        with pytest.raises(RuntimeError, match="database write failed"):
            _apply(page)

    assert not AlwaysOnImportedEvent.objects.exists()
    assert not AlwaysOnDailyAnalytics.objects.exists()
    assert not AlwaysOnProductionRun.objects.exists()
    assert not AlwaysOnCounterCursor.objects.exists()


def test_session_event_is_durable_but_not_added_to_always_on_analytics():
    _set_role("cam3", ANALYTICS_SCOPE_SHIPPING)
    response = _page(
        [
            _event(
                1,
                1,
                mode="session",
                analytics_scope=ANALYTICS_SCOPE_SHIPPING,
            )
        ]
    )
    with patch.object(ai, "count_events", return_value=response):
        result = event_sync.sync_camera("cam3")

    assert result.processed == 0
    assert result.ignored == 1
    imported = AlwaysOnImportedEvent.objects.get()
    assert imported.mode == "session"
    assert imported.continuous_analytics is False
    assert imported.analytics_scope == ANALYTICS_SCOPE_SHIPPING
    assert imported.color is None
    assert imported.brand is None
    assert imported.sku is None
    assert not imported.applied_to_analytics
    assert AlwaysOnCounterCursor.objects.get(camera="cam3").last_event_id == 1
    assert not AlwaysOnDailyAnalytics.objects.exists()


def test_legacy_ai_scoped_session_advances_cursor_without_ai_production():
    page = _parse(
        [
            _event(
                1,
                1,
                mode="session",
                continuous_analytics=True,
                analytics_scope=ANALYTICS_SCOPE_AI247,
            )
        ],
    )

    assert _apply(page)[:2] == (0, 1)
    imported = AlwaysOnImportedEvent.objects.get()
    assert imported.applied_to_analytics is False
    assert imported.applied_to_production is False
    assert imported.applied_to_shipping_bootstrap is False
    assert AlwaysOnCounterCursor.objects.get(camera="cam3").last_event_id == 1
    assert not AlwaysOnDailyAnalytics.objects.exists()
    assert not AlwaysOnProductionRun.objects.exists()


def test_flagged_session_event_updates_continuous_analytics_exactly_once():
    _set_role("cam3", ANALYTICS_SCOPE_SHIPPING)
    page = _parse(
        [
            _event(
                1,
                1,
                mode="session",
                continuous_analytics=True,
                analytics_scope=ANALYTICS_SCOPE_SHIPPING,
                color="Green_50",
                brand="pioneer",
            )
        ],
    )

    assert _apply(page)[:2] == (1, 0)
    assert _apply(page)[:2] == (0, 0)

    imported = AlwaysOnImportedEvent.objects.get()
    assert imported.mode == "session"
    assert imported.continuous_analytics is True
    assert imported.analytics_scope == ANALYTICS_SCOPE_SHIPPING
    assert imported.applied_to_analytics is True
    analytics_row = ShippingDailyAnalytics.objects.get(camera="cam3")
    assert analytics_row.model_total == 1
    assert analytics_row.model_per_color == {"green": 1}
    assert analytics_row.model_per_brand == {"pioneer": 1}
    assert not AlwaysOnProductionRun.objects.exists()


def test_shipping_and_ai247_events_use_separate_analytics_ledgers():
    _set_role("cam2", ANALYTICS_SCOPE_SHIPPING)
    ai_page = _parse([_event(1, 1, analytics_scope=ANALYTICS_SCOPE_AI247)])
    shipping_page = _parse(
        [
            _event(
                1,
                1,
                mode="session",
                camera="cam2",
                continuous_analytics=True,
                analytics_scope=ANALYTICS_SCOPE_SHIPPING,
            )
        ],
        camera="cam2",
    )

    assert _apply(ai_page)[:2] == (1, 0)
    assert _apply(shipping_page, camera="cam2")[:2] == (1, 0)
    assert AlwaysOnDailyAnalytics.objects.get(camera="cam3").model_total == 1
    assert ShippingDailyAnalytics.objects.get(camera="cam2").model_total == 1
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
    assert result == event_sync.SyncResult(2, 0, 2, 2, True)


def test_pending_enrichment_does_not_claim_event_stream_is_caught_up():
    response = _page([], enrichment_pending=True)
    with patch.object(ai, "count_events", return_value=response):
        result = event_sync.sync_camera("cam3")

    assert result == event_sync.SyncResult(0, 0, 1, 0, False)
    cursor = AlwaysOnCounterCursor.objects.get(camera="cam3")
    assert cursor.last_event_id == 0
    assert cursor.event_boundary_validated is False
    assert cursor.event_caught_up_at is None


def test_events_404_is_a_sync_failure_not_a_legacy_mode():
    """A proxy or a wrong AI URL answering 404 must not switch off the journal."""
    with patch.object(ai, "_request", return_value=(404, {"error": "not found"})):
        assert continuous._sync_camera_events("cam3") is None

    cursor = AlwaysOnCounterCursor.objects.get(camera="cam3")
    assert cursor.has_sync_failure
    assert cursor.event_sync_supported is None
    assert cursor.last_event_id is None


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


def test_late_event_for_a_posted_stock_shift_is_counted_without_production():
    """A restart-gap backfill may deliver bags after their shift was posted.

    The posted batch must stay untouched, but freezing the journal would lose
    every later event too. The bag is kept in the day's analytics and the
    imported row records that production never received it.
    """

    event = _event(1, 1)
    occurred_at = _at(1)
    day = production_runs.business_day_for(occurred_at)
    AlwaysOnStockBatch.objects.create(
        camera="cam3",
        business_day=day,
        scheduled_for=production_runs.scheduled_for(day),
        status=AlwaysOnStockBatch.POSTED,
    )
    page = _parse([event])

    processed, ignored, cursor_id = _apply(page)

    assert (processed, ignored, cursor_id) == (1, 0, 1)
    imported = AlwaysOnImportedEvent.objects.get(camera="cam3", upstream_event_id=1)
    assert imported.applied_to_analytics is True
    assert imported.applied_to_production is False
    assert AlwaysOnCounterCursor.objects.get(camera="cam3").last_event_id == 1
    assert AlwaysOnDailyAnalytics.objects.get(camera="cam3").model_total == 1
    assert not AlwaysOnProductionRun.objects.exists()

    # A replay of the same page recomputes the same eligibility and stays idempotent.
    assert _apply(page, after_id=1) == (0, 0, 1)


def test_policy_put_failure_does_not_starve_healthy_event_import():
    _set_role("cam2", ANALYTICS_SCOPE_SHIPPING)
    MonoblockCameraSettings.objects.create(
        camera_sources=["cam2"],
        always_on_camera_sources=["cam3"],
    )
    current = {
        "camera_sources": ["cam3"],
        "source": "sub",
        "analytics_scopes": {"cam3": ANALYTICS_SCOPE_AI247},
        "processors": [],
    }
    synced = event_sync.SyncResult(0, 0, 1, 0, True)

    with (
        patch.object(ai, "always_on_status", return_value=current),
        patch.object(
            ai,
            "configure_always_on",
            side_effect=ai.AiUnavailable("policy PUT failed"),
        ),
        patch.object(event_sync, "sync_camera", return_value=synced) as sync,
    ):
        with pytest.raises(ai.AiUnavailable, match="policy PUT failed"):
            continuous.reconcile()

    assert [call.args for call in sync.call_args_list] == [
        ("cam2",),
        ("cam3",),
    ]


def test_failed_remove_then_same_scope_reactivation_reaches_fresh_synced_tail():
    _set_role("cam3", ANALYTICS_SCOPE_SHIPPING)
    row = MonoblockCameraSettings.objects.create(
        camera_sources=[],
        always_on_camera_sources=[],
    )
    cursor = AlwaysOnCounterCursor.objects.create(
        camera="cam3",
        last_total=9,
        last_event_id=9,
        event_sync_supported=True,
        event_boundary_validated=True,
        event_caught_up_at=_at(9),
    )
    still_running_shipping = {
        "camera_sources": ["cam3"],
        "source": "sub",
        "analytics_scopes": {"cam3": ANALYTICS_SCOPE_SHIPPING},
        "processors": [],
    }

    with (
        patch.object(ai, "always_on_status", return_value=still_running_shipping),
        patch.object(
            ai,
            "configure_always_on",
            side_effect=ai.AiUnavailable("remove PUT failed"),
        ),
        patch.object(
            event_sync,
            "sync_camera",
            return_value=event_sync.SyncResult(0, 0, 1, 9, True),
        ),
    ):
        with pytest.raises(ai.AiUnavailable, match="remove PUT failed"):
            continuous.reconcile()

    cursor.refresh_from_db()
    assert cursor.event_stop_drain_requested_at is not None
    assert cursor.event_stop_confirmed_at is None
    assert cursor.event_caught_up_at is None

    # The camera is reactivated in its permanent shipping contour. The fresh
    # successful reply must cancel the stale failed-stop intent.
    row.camera_sources = ["cam3"]
    row.save(update_fields=["camera_sources", "updated_at"])
    running_shipping = {
        "camera_sources": ["cam3"],
        "source": "sub",
        "analytics_scopes": {"cam3": ANALYTICS_SCOPE_SHIPPING},
        "processors": [
            {
                "cam": "cam3",
                "running": True,
                "processor_alive": True,
                "source": "sub",
                "mode": "always_on",
                "analytics_scope": ANALYTICS_SCOPE_SHIPPING,
                "last_frame_at": "2026-09-01T08:00:00Z",
            }
        ],
    }
    shipping_event = _event(
        10,
        10,
        analytics_scope=ANALYTICS_SCOPE_SHIPPING,
    )
    with (
        patch.object(ai, "always_on_status", return_value=running_shipping),
        patch.object(ai, "configure_always_on") as configure,
        patch.object(
            ai,
            "count_events",
            return_value=_page([shipping_event], after_id=9),
        ),
    ):
        continuous.reconcile()

    configure.assert_not_called()
    cursor.refresh_from_db()
    assert cursor.last_event_id == 10
    assert cursor.event_caught_up_at is not None
    assert cursor.event_drain_required_at is None
    assert cursor.event_stop_drain_requested_at is None
    assert cursor.event_stop_confirmed_at is None
    assert ShippingDailyAnalytics.objects.get(camera="cam3").model_total == 1
    assert not AlwaysOnDailyAnalytics.objects.exists()
    assert not AlwaysOnProductionRun.objects.exists()


def test_shipping_generation_reset_is_rejected():
    _set_role("cam3", ANALYTICS_SCOPE_SHIPPING)
    AlwaysOnCounterCursor.objects.create(camera="cam3", last_total=5)
    page = _parse([_event(1, 1, analytics_scope=ANALYTICS_SCOPE_SHIPPING)])

    with pytest.raises(event_sync.EventSyncError, match="reset is not authorized"):
        _apply(page)

    assert not AlwaysOnImportedEvent.objects.exists()
    assert not ShippingDailyAnalytics.objects.exists()


def test_removing_an_event_camera_performs_and_retries_a_final_drain():
    MonoblockCameraSettings.objects.create(always_on_camera_sources=[])
    AlwaysOnCounterCursor.objects.create(
        camera="cam3",
        last_event_id=9,
        event_sync_supported=True,
        event_boundary_validated=True,
        event_caught_up_at=_at(9),
    )
    before_stop = {
        "camera_sources": ["cam3"],
        "source": "sub",
        "analytics_scopes": {"cam3": ANALYTICS_SCOPE_AI247},
        "processors": [],
    }
    after_stop = {
        "camera_sources": [],
        "source": "sub",
        "analytics_scopes": {},
        "processors": [],
    }

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

    configure.assert_called_once_with([], "sub", analytics_scopes={})
    sync.assert_called_once_with("cam3")
    assert AlwaysOnCounterCursor.objects.get(camera="cam3").event_caught_up_at is None

    with (
        patch.object(ai, "always_on_status", return_value=after_stop),
        patch.object(ai, "configure_always_on") as configure,
        patch.object(
            event_sync,
            "sync_camera",
            return_value=event_sync.SyncResult(0, 0, 1, 9, True),
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
        event_sync_supported=True,
        event_boundary_validated=True,
        event_caught_up_at=_at(9),
    )
    event_sync.request_stop_drain("cam3")
    stopped = {
        "camera_sources": [],
        "source": "sub",
        "analytics_scopes": {},
        "processors": [],
    }

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


def test_initial_event_boundary_mismatch_is_rejected_without_double_counting():
    AlwaysOnCounterCursor.objects.create(camera="cam3", last_total=100)
    AlwaysOnDailyAnalytics.objects.create(
        camera="cam3",
        day=timezone.localdate(_at(1)),
        model_total=100,
    )
    page = _parse([_event(1, 50)])

    with pytest.raises(event_sync.EventSyncError, match="boundary"):
        _apply(page)

    assert not AlwaysOnImportedEvent.objects.exists()
    assert AlwaysOnDailyAnalytics.objects.get(camera="cam3").model_total == 100
    assert AlwaysOnCounterCursor.objects.get(camera="cam3").last_event_id is None


def test_event_capability_is_one_way_even_if_the_first_page_is_bad():
    malformed = {"events": [], "next_after_id": 1, "has_more": False}
    with patch.object(ai, "count_events", return_value=malformed):
        with pytest.raises(event_sync.EventSyncError):
            event_sync.sync_camera("cam3")

    assert AlwaysOnCounterCursor.objects.get(camera="cam3").event_sync_supported is True


def test_known_event_capability_is_not_rewritten_on_every_poll():
    with patch.object(ai, "count_events", return_value=_page([])):
        with patch.object(
            event_sync,
            "_mark_events_observed",
            wraps=event_sync._mark_events_observed,
        ) as observed:
            event_sync.sync_camera("cam3")
            event_sync.sync_camera("cam3")

    observed.assert_called_once_with("cam3")
    assert AlwaysOnCounterCursor.objects.get(camera="cam3").event_sync_supported is True


def test_pre_boundary_inflight_page_cannot_complete_a_required_final_drain():
    first = _parse([_event(1, 1)])
    _apply(first, synced_at=_at(1))
    AlwaysOnCounterCursor.objects.filter(camera="cam3").update(
        event_drain_required_at=_at(5), event_caught_up_at=None
    )

    stale = _parse([_event(2, 2)], after_id=1)
    _apply(stale, after_id=1, synced_at=_at(4))
    cursor = AlwaysOnCounterCursor.objects.get(camera="cam3")
    assert cursor.last_event_id == 2
    assert cursor.event_caught_up_at is None
    assert cursor.event_drain_required_at == _at(5)

    empty = _parse([], after_id=2)
    _apply(empty, after_id=2, synced_at=_at(6))
    cursor.refresh_from_db()
    assert cursor.event_caught_up_at == _at(6)
    assert cursor.event_drain_required_at is None


def test_event_source_and_mode_are_closed_contract_enums():
    event = _event(1, 1)
    event["source"] = "other"

    with pytest.raises(event_sync.EventSyncError, match="source"):
        _parse([event])

    event = _event(1, 1)
    event["mode"] = "alwayson"
    with pytest.raises(event_sync.EventSyncError, match="mode"):
        _parse([event])


def test_journal_identity_binds_once_and_detects_sqlite_recreation():
    first_payload = _page([_event(1, 1)])
    first_payload["journal_id"] = "11111111-1111-4111-8111-111111111111"
    first = event_sync.parse_page(first_payload, camera="cam3", after_id=0)
    _apply(first)

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
        _apply(recreated, after_id=1)

    cursor.refresh_from_db()
    assert cursor.last_event_id == 1


def test_late_journal_identity_requires_manual_continuity_verification():
    cursor = AlwaysOnCounterCursor.objects.create(
        camera="cam3",
        last_event_id=9,
        event_sync_supported=True,
        event_boundary_validated=True,
    )
    payload = _page([], after_id=9)
    payload["journal_id"] = "11111111-1111-4111-8111-111111111111"
    page = event_sync.parse_page(payload, camera="cam3", after_id=9)

    with pytest.raises(event_sync.EventSyncError, match="manual continuity"):
        _apply(page, after_id=9)

    cursor.refresh_from_db()
    assert cursor.event_journal_id is None
    assert cursor.last_event_id == 9


# --- multi-line verification (cv-service "Линии проверки") -------------------


def _verified(event: dict, *, color: str, brand: str, sku: str, status: str) -> dict:
    """An event settled by cross-line consensus, as the camera PC reports it."""

    return {
        **event,
        "color": color,
        "color_confidence": 0.0 if color == "unknown" else 0.93,
        "brand": brand,
        "brand_confidence": 0.0 if brand == "unknown" else 0.88,
        "sku": sku,
        "classification_status": status,
        "verification": {
            "version": 1,
            "reason": "all_lines",
            "minimum_votes": 2,
            "expected_lines": ["count", "before", "after"],
            "observed_lines": ["after", "before", "count"],
            "distinct_frames": 3,
            "samples": [
                {
                    "frame": 7001,
                    "line_ids": ["before"],
                    "bbox": [10.0, 20.0, 110.0, 220.0],
                    "crop_size": [120, 240],
                    "prediction": None,
                    "latency_ms": 41.5,
                    "error": "crop_too_small",
                }
            ],
        },
    }


def test_verified_classification_payloads_never_invent_a_colour():
    review = {"brand": "unknown", "status": "needs_review"}
    events = [
        # Consensus failed: the detector's own class must not leak in as blue.
        _verified(_event(1, 1, class_name="Blue_50"), color="unknown", sku="unknown_unknown", **review),
        _verified(
            _event(2, 2, class_name="White_50"),
            color="White_50",
            brand="unknown",
            sku="white_reverse",
            status="white_reverse",
        ),
        _verified(_event(3, 3, class_name="Green_50"), color="Green_50", sku="green_unknown", **review),
        # Weight-only detector: no colour anywhere, still one counted bag.
        _verified(_event(4, 4, class_name="Bag_50"), color="unknown", sku="unknown_unknown", **review),
    ]
    with patch.object(ai, "count_events", return_value=_page(events)):
        result = event_sync.sync_camera("cam3")

    assert result == event_sync.SyncResult(4, 0, 1, 4, True)
    daily = AlwaysOnDailyAnalytics.objects.get(camera="cam3")
    assert daily.model_total == 4
    assert daily.model_per_color == {"unknown": 2, "white": 1, "green": 1}
    assert daily.model_per_brand == {"unknown": 4}
    rows = list(AlwaysOnImportedEvent.objects.order_by("upstream_event_id"))
    assert [row.classification_status for row in rows] == [
        "needs_review",
        "white_reverse",
        "needs_review",
        "needs_review",
    ]
    assert [row.sku for row in rows] == [
        "unknown_unknown",
        "white_reverse",
        "green_unknown",
        "unknown_unknown",
    ]
    colors = ["unknown", "white", "green", "unknown"]
    assert [camera_color_key(row.color, row.class_name) for row in rows] == colors
    runs = list(AlwaysOnProductionRun.objects.order_by("started_at", "id"))
    assert [(run.color, run.model_bags) for run in runs] == [(color, 1) for color in colors]


def test_classification_still_pending_is_never_imported_as_final():
    recognized = _event(1, 1, color="Red_50", brand="korol")
    pending = {
        **_event(2, 2, class_name="Blue_50", color="Blue_50"),
        "classification_status": "pending",
    }
    later = _event(3, 3, color="Green_50", brand="korol")
    page = _parse([recognized, pending, later], has_more=True)

    assert [event.upstream_event_id for event in page.events] == [1]
    assert page.next_after_id == 1
    assert page.has_more is False
    assert page.enrichment_pending is True

    with patch.object(ai, "count_events", return_value=_page([recognized, pending, later])):
        result = event_sync.sync_camera("cam3")

    assert result == event_sync.SyncResult(1, 0, 1, 1, False)
    cursor = AlwaysOnCounterCursor.objects.get(camera="cam3")
    assert cursor.last_event_id == 1
    assert cursor.event_caught_up_at is None
    assert AlwaysOnDailyAnalytics.objects.get(camera="cam3").model_per_color == {"red": 1}


class _EventPageResponse(BytesIO):
    """A camera-PC /events reply as urllib hands it to the CRM."""

    status = 200


def _full_verification(event_id: int, extra_lines: int) -> dict:
    """Evidence exactly as cv-service 1dc2431 serialises it: one sample per line."""

    line_ids = ["count", *[f"check-{index}" for index in range(1, extra_lines + 1)]]
    samples = [
        {
            "frame": 690_000 + event_id * 10 + offset,
            "line_ids": [line_id],
            "bbox": [412.73486328125, 188.2310791015625, 530.9981689453125, 322.8759765625],
            "crop_size": [142, 158],
            "prediction": {
                "color": "Green_50",
                "color_confidence": 0.9312345669269562,
                "brand": "korol",
                "brand_confidence": 0.8812345678901234,
                "sku": "green_korol",
                "classification_status": "recognized",
            },
            "latency_ms": 41.52345678901234,
            "error": None,
        }
        for offset, line_id in enumerate(line_ids)
    ]
    return {
        "version": 1,
        "reason": "all_lines",
        "minimum_votes": 2,
        "expected_lines": line_ids,
        "observed_lines": sorted(line_ids),
        "distinct_frames": len(line_ids),
        "samples": samples,
    }


@pytest.mark.parametrize("extra_lines", [2, 8])
def test_a_full_page_of_verified_events_is_read_and_imported(extra_lines):
    events = []
    for event_id in range(1, EVENT_PAGE_LIMIT + 1):
        event = _event(event_id, event_id, color="Green_50", brand="korol", second=0)
        event["created_at"] = (_at(0) + timedelta(seconds=event_id)).isoformat()
        event["verification"] = _full_verification(event_id, extra_lines)
        events.append(event)
    # Serialised like the camera PC's handler (default separators, UTF-8).
    body = json.dumps(_page(events), ensure_ascii=False, default=str).encode()
    # Larger than an ordinary AI reply, well inside the page allowance.
    assert ai.MAX_JSON_RESPONSE_BYTES < len(body) < ai.EVENT_PAGE_MAX_BYTES / 3

    with patch("urllib.request.urlopen", side_effect=lambda *_a, **_k: _EventPageResponse(body)):
        assert len(ai.count_events("cam3", 0, EVENT_PAGE_LIMIT)["events"]) == EVENT_PAGE_LIMIT
        result = event_sync.sync_camera("cam3", max_pages=1)

    assert result == event_sync.SyncResult(EVENT_PAGE_LIMIT, 0, 1, EVENT_PAGE_LIMIT, True)
    assert AlwaysOnCounterCursor.objects.get(camera="cam3").last_event_id == EVENT_PAGE_LIMIT
    assert AlwaysOnImportedEvent.objects.count() == EVENT_PAGE_LIMIT
