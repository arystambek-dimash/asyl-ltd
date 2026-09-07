from collections import Counter
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.cameras import ai, shipping_history
from apps.cameras.models import (
    ANALYTICS_SCOPE_AI247,
    ANALYTICS_SCOPE_SHIPPING,
    AlwaysOnCounterCursor,
    AlwaysOnDailyAnalytics,
    AlwaysOnImportedEvent,
    AlwaysOnProductionRun,
    AlwaysOnStockBatch,
    ContinuousCameraRole,
    MonoblockCameraSettings,
    ShippingAnalyticsBootstrap,
    ShippingDailyAnalytics,
)

pytestmark = pytest.mark.django_db
NOW = timezone.make_aware(datetime(2026, 9, 7, 14, 0), timezone.get_default_timezone())
DAY = NOW.date()
URL = "/api/cameras/shipping-continuous-history/"


@pytest.fixture(autouse=True)
def fixed_time():
    with patch("django.utils.timezone.now", return_value=NOW):
        yield


@pytest.fixture
def camera():
    MonoblockCameraSettings.objects.create(
        camera_sources=["cam1"], always_on_camera_sources=["cam3"]
    )
    ContinuousCameraRole.objects.create(
        camera="cam1", analytics_scope=ANALYTICS_SCOPE_SHIPPING
    )
    ContinuousCameraRole.objects.create(
        camera="cam3", analytics_scope=ANALYTICS_SCOPE_AI247
    )
    AlwaysOnCounterCursor.objects.create(
        camera="cam1",
        last_event_id=0,
        event_compat_total=0,
        event_sync_supported=True,
        event_boundary_validated=True,
        event_caught_up_at=NOW,
    )
    return "cam1"


def add_events(
    colors, *, camera="cam1", at=None, scope=ANALYTICS_SCOPE_SHIPPING, **fields
):
    last = (
        AlwaysOnImportedEvent.objects.filter(camera=camera)
        .order_by("-upstream_event_id")
        .first()
    )
    first_id = last.upstream_event_id + 1 if last else 1
    start = at if at is not None else NOW - timedelta(seconds=len(colors))
    rows = AlwaysOnImportedEvent.objects.bulk_create(
        [
            AlwaysOnImportedEvent(
                camera=camera,
                upstream_event_id=first_id + index,
                occurred_at=start + timedelta(seconds=index),
                source="sub",
                mode=fields.get("mode", "always_on"),
                analytics_scope=scope,
                color=color,
                applied_to_analytics=fields.get("applied_to_analytics", True),
                applied_to_shipping_bootstrap=fields.get(
                    "applied_to_shipping_bootstrap", False
                ),
                continuous_analytics=fields.get("continuous_analytics", False),
                class_name=fields.get("class_name", ""),
            )
            for index, color in enumerate(colors)
        ]
    )
    if rows:
        AlwaysOnCounterCursor.objects.filter(camera=camera).update(
            last_event_id=rows[-1].upstream_event_id
        )
    return rows


def set_total(events, *, day=DAY, camera="cam1", legacy=False, adjustment=0):
    colors = Counter()
    for event in events:
        colors.update(shipping_history._event_color(event))
    model = AlwaysOnDailyAnalytics if legacy else ShippingDailyAnalytics
    return model.objects.create(
        camera=camera,
        day=day,
        model_total=len(events),
        model_per_color=dict(colors),
        adjustment=adjustment,
    )


def payload():
    return shipping_history.day_payload("cam1", day=DAY)


@pytest.mark.parametrize(
    "permission", ["shipping.view", "shipping.load", "sys_permissions.manage"]
)
def test_endpoint_permissions_and_no_stock_contract(
    camera, auth_client, user_with_perms, permission
):
    user = user_with_perms("shipping-history-" + permission, codes=[permission])
    with patch.object(
        ai, "_request", side_effect=AssertionError("GET must remain local")
    ):
        response = auth_client(user).get(URL, {"camera": "cam1", "day": str(DAY)})
    assert response.status_code == 200
    assert set(response.data) == {
        "camera",
        "timezone",
        "selected_day",
        "day_runs",
        "algorithm_day_runs",
        "run_smoothing",
        "history_status",
        "history_detail",
    }
    assert response.data["history_status"] == "complete"
    assert response.data["day_runs"] == []


def test_endpoint_denies_anonymous_and_unprivileged(
    camera, api_client, auth_client, user_with_perms
):
    assert api_client.get(URL, {"camera": "cam1", "day": str(DAY)}).status_code in (
        401,
        403,
    )
    user = user_with_perms("shipping-history-unprivileged", codes=["ai_247.manage"])
    assert (
        auth_client(user).get(URL, {"camera": "cam1", "day": str(DAY)}).status_code
        == 403
    )


@pytest.mark.parametrize(
    "query",
    [
        {},
        {"camera": "cam1"},
        {"day": str(DAY)},
        {"camera": "../cam1", "day": str(DAY)},
        {"camera": "cam1", "day": "2026-02-30"},
        {"camera": "cam1", "day": "oops"},
        {"camera": "cam1", "day": "9999-12-31"},
        {"camera": "cam1", "day": "0001-01-01"},
    ],
)
def test_endpoint_rejects_bad_query(camera, auth_client, user_with_perms, query):
    user = user_with_perms("shipping-history-invalid", codes=["shipping.view"])
    assert auth_client(user).get(URL, query).status_code == 400


@pytest.mark.parametrize(
    "source, configured, reserved",
    [
        ("cam3", False, ANALYTICS_SCOPE_AI247),
        ("cam1", True, ANALYTICS_SCOPE_AI247),
        ("cam1", True, None),
        ("cam1", False, ANALYTICS_SCOPE_SHIPPING),
    ],
)
def test_endpoint_requires_active_shipping_and_permanent_role(
    camera, auth_client, user_with_perms, source, configured, reserved
):
    if configured:
        MonoblockCameraSettings.objects.update(camera_sources=[source])
    else:
        MonoblockCameraSettings.objects.update(camera_sources=[])
    ContinuousCameraRole.objects.filter(camera=source).delete()
    if reserved:
        ContinuousCameraRole.objects.create(camera=source, analytics_scope=reserved)
    user = user_with_perms(
        "shipping-history-isolation", codes=["sys_permissions.manage"]
    )
    response = auth_client(user).get(URL, {"camera": source, "day": str(DAY)})
    assert response.status_code == 400
    assert response.data["code"] == "camera_not_in_shipping"


def test_raw_order_and_shared_algorithm_preserve_total_without_mutating_evidence(
    camera,
):
    events = add_events(["White_50"] * 15 + ["red"] * 4 + ["white"] * 20)
    set_total(events)
    before = list(AlwaysOnImportedEvent.objects.values())
    result = payload()
    assert result["history_status"] == "complete"
    assert [(run["color"], run["model_bags"]) for run in result["day_runs"]] == [
        ("white", 15),
        ("red", 4),
        ("white", 20),
    ]
    assert [
        (run["color"], run["model_bags"]) for run in result["algorithm_day_runs"]
    ] == [("white", 39)]
    assert [run["status"] for run in result["day_runs"]] == [
        "closed",
        "closed",
        "active",
    ]
    assert (
        result["run_smoothing"]["raw_model_total"]
        == result["run_smoothing"]["algorithm_model_total"]
        == 39
    )
    assert list(AlwaysOnImportedEvent.objects.values()) == before
    assert not AlwaysOnProductionRun.objects.exists()
    assert not AlwaysOnStockBatch.objects.exists()


def test_continuous_session_events_count_but_non_continuous_and_other_camera_do_not(
    camera,
):
    accepted = add_events(["red", "white"], mode="session", continuous_analytics=True)
    add_events(
        ["blue"], mode="session", continuous_analytics=False, applied_to_analytics=False
    )
    add_events(["blue"], camera="cam3", scope=ANALYTICS_SCOPE_AI247)
    set_total(accepted)
    result = payload()
    assert result["history_status"] == "complete"
    assert [run["color"] for run in result["day_runs"]] == ["red", "white"]


def test_local_midnight_bounds_are_half_open_and_shipping_has_no_warehouse_shift_split(
    camera,
):
    midnight = NOW.replace(hour=0, minute=0, second=0)
    yesterday = add_events(["blue"], at=midnight - timedelta(seconds=1))
    first = add_events(["white"], at=midnight)
    before_shift = add_events(
        ["red"], at=midnight + timedelta(hours=18, minutes=59, seconds=59)
    )
    after_shift = add_events(["red"], at=midnight + timedelta(hours=19))
    add_events(["green"], at=midnight + timedelta(days=1))
    set_total(yesterday, day=DAY - timedelta(days=1))
    set_total(first + before_shift + after_shift)
    with patch(
        "django.utils.timezone.now", return_value=midnight + timedelta(hours=20)
    ):
        result = payload()
        old = shipping_history.day_payload("cam1", day=DAY - timedelta(days=1))
    assert result["history_status"] == old["history_status"] == "complete"
    assert [(run["color"], run["model_bags"]) for run in result["day_runs"]] == [
        ("white", 1),
        ("red", 2),
    ]
    assert all(run["business_day"] == str(DAY) for run in result["day_runs"])
    assert old["day_runs"][0]["status"] == "closed"


def test_raw_runs_split_after_inactivity_and_old_day_near_midnight_is_closed(camera):
    events = add_events(["white"], at=NOW - timedelta(minutes=10)) + add_events(
        ["white"], at=NOW - timedelta(seconds=1)
    )
    set_total(events)
    result = payload()
    assert len(result["day_runs"]) == 2
    assert [run["status"] for run in result["day_runs"]] == ["closed", "active"]
    with patch("django.utils.timezone.now", return_value=NOW + timedelta(minutes=6)):
        assert all(run["status"] == "closed" for run in payload()["day_runs"])


@pytest.mark.parametrize(
    "cursor_changes",
    [
        {"event_caught_up_at": None},
        {"event_caught_up_at": NOW - timedelta(minutes=5)},
        {"event_sync_error": "offline"},
        {"event_sync_failed_at": NOW},
        {"event_drain_required_at": NOW},
        {"event_stop_drain_requested_at": NOW},
    ],
)
def test_saved_history_remains_readable_but_offline_or_pending_tail_is_not_live(
    camera, cursor_changes
):
    set_total(add_events(["white"]))
    AlwaysOnCounterCursor.objects.filter(camera=camera).update(**cursor_changes)
    result = payload()
    assert result["history_status"] == "complete"
    assert result["day_runs"][0]["status"] == "closed"


@pytest.mark.parametrize("missing_color", [None, "unknown", "unclassified"])
def test_missing_colors_keep_exact_counts_and_cannot_be_smoothed_into_known_color(
    camera,
    missing_color,
):
    events = add_events(["red"] * 10 + [missing_color] + ["red"] * 10)
    set_total(events)
    result = payload()
    assert result["history_status"] == "complete"
    assert [
        (run["color"], run["model_bags"]) for run in result["algorithm_day_runs"]
    ] == [("red", 10), (missing_color or "unclassified", 1), ("red", 10)]
    assert result["day_runs"][1]["is_approximate"] is False


@pytest.mark.parametrize(
    "total, colors", [(2, {"red": 2}), (1, {"blue": 1}), (1, {"red": 2})]
)
def test_partial_or_mismatched_daily_totals_are_explicitly_incomplete(
    camera, total, colors
):
    add_events(["red"])
    ShippingDailyAnalytics.objects.create(
        camera=camera, day=DAY, model_total=total, model_per_color=colors
    )
    result = payload()
    assert result["history_status"] == "incomplete"
    assert result["history_detail"]
    assert result["day_runs"] == result["algorithm_day_runs"] == []


def test_manual_adjustment_is_not_an_invented_model_event(camera):
    set_total(add_events(["red"] * 3), adjustment=-1)
    result = payload()
    assert result["history_status"] == "complete"
    assert result["run_smoothing"]["raw_model_total"] == 3


def test_pending_bootstrap_and_uninitialized_or_legacy_cursor_are_honest(camera):
    ShippingAnalyticsBootstrap.objects.create(camera=camera)
    assert payload()["history_status"] == "pending"
    ShippingAnalyticsBootstrap.objects.all().delete()
    AlwaysOnCounterCursor.objects.filter(camera=camera).update(
        event_boundary_validated=False
    )
    assert payload()["history_status"] == "pending"
    AlwaysOnCounterCursor.objects.filter(camera=camera).update(
        event_sync_supported=False
    )
    assert payload()["history_status"] == "incomplete"
    AlwaysOnCounterCursor.objects.all().delete()
    assert payload()["history_status"] == "pending"


def test_completed_bootstrap_can_prove_legacy_events_and_continuous_session_tail(
    camera,
):
    original = add_events(
        ["red"] * 4, at=NOW - timedelta(minutes=10), scope=ANALYTICS_SCOPE_AI247
    )
    tail = add_events(
        ["white"] * 2,
        at=NOW - timedelta(minutes=9),
        scope=ANALYTICS_SCOPE_AI247,
        mode="session",
        continuous_analytics=True,
        applied_to_analytics=False,
        applied_to_shipping_bootstrap=True,
    )
    current = add_events(["blue"] * 3)
    set_total(original + tail, legacy=True)
    set_total(original + tail + current)
    ShippingAnalyticsBootstrap.objects.create(
        camera=camera, scope_confirmed_at=NOW, completed_at=NOW
    )
    result = payload()
    assert result["history_status"] == "complete"
    assert result["run_smoothing"]["raw_model_per_color"] == {
        "red": 4,
        "white": 2,
        "blue": 3,
    }


@pytest.mark.parametrize(
    "problem",
    ["missing_marker", "partial_legacy", "archived_legacy", "post_bootstrap_event"],
)
def test_unproven_legacy_history_cannot_fill_shipping_log(camera, problem):
    old = add_events(
        ["red"] * 2, at=NOW - timedelta(minutes=10), scope=ANALYTICS_SCOPE_AI247
    )
    current = add_events(["white"])
    legacy = set_total(old, legacy=True)
    set_total(old + current)
    if problem != "missing_marker":
        ShippingAnalyticsBootstrap.objects.create(camera=camera, completed_at=NOW)
    if problem == "partial_legacy":
        AlwaysOnDailyAnalytics.objects.filter(pk=legacy.pk).update(model_total=3)
    elif problem == "archived_legacy":
        AlwaysOnDailyAnalytics.objects.filter(pk=legacy.pk).update(archived_at=NOW)
    elif problem == "post_bootstrap_event":
        AlwaysOnImportedEvent.objects.filter(pk=old[0].pk).update(
            imported_at=NOW + timedelta(seconds=1)
        )
    result = payload()
    assert result["history_status"] == "incomplete"
    assert result["day_runs"] == []


@pytest.mark.parametrize(
    "timestamps", [[NOW, NOW - timedelta(seconds=1)], [NOW + timedelta(seconds=1)]]
)
def test_non_monotonic_or_future_timestamps_are_not_reordered_or_shown_live(
    camera, timestamps
):
    events = [event for at in timestamps for event in add_events(["red"], at=at)]
    set_total(events)
    result = payload()
    assert result["history_status"] == "incomplete"
    assert "время" in result["history_detail"]
    assert result["day_runs"] == []


def test_query_count_is_bounded_and_get_does_not_write_or_contact_ai(camera):
    set_total(add_events(["red", "white"] * 100))
    with CaptureQueriesContext(connection) as queries:
        result = payload()
    assert result["history_status"] == "complete"
    assert len(queries) == 4
    assert all(query["sql"].startswith("SELECT") for query in queries)
    assert "occurred_at" in queries[-1]["sql"] and "LIMIT 100001" in queries[-1]["sql"]


def test_high_water_mark_mismatch_is_not_a_partial_log(camera):
    set_total(add_events(["red"] * 2))
    AlwaysOnCounterCursor.objects.filter(camera=camera).update(last_event_id=1)
    result = payload()
    assert result["history_status"] == "incomplete"
    assert result["day_runs"] == []


def test_event_and_period_limits_are_explicit_and_never_return_truncated_logs(
    camera, monkeypatch
):
    set_total(add_events(["red", "white", "red"]))
    monkeypatch.setattr(shipping_history, "MAX_DAY_EVENTS", 2)
    assert "слишком много событий" in payload()["history_detail"]
    monkeypatch.setattr(shipping_history, "MAX_DAY_EVENTS", 10)
    monkeypatch.setattr(shipping_history, "MAX_DAY_RUNS", 2)
    result = payload()
    assert result["history_status"] == "incomplete"
    assert "слишком много смен" in result["history_detail"]
    assert result["day_runs"] == []


def test_import_between_cursor_and_daily_reads_returns_pending_instead_of_false_gap(
    camera,
):
    set_total(add_events(["red"]))
    advanced = False

    def import_after_cursor_read(execute, sql, params, many, context):
        nonlocal advanced
        result = execute(sql, params, many, context)
        if (
            not advanced
            and sql.startswith("SELECT")
            and 'FROM "cameras_alwaysoncountercursor"' in sql
        ):
            advanced = True
            add_events(["red"], at=NOW)
            ShippingDailyAnalytics.objects.filter(camera=camera, day=DAY).update(
                model_total=2, model_per_color={"red": 2}
            )
        return result

    with connection.execute_wrapper(import_after_cursor_read):
        result = payload()
    assert advanced
    assert result["history_status"] == "pending"
    assert result["day_runs"] == []
    assert payload()["history_status"] == "complete"


def test_previous_calendar_day_tail_is_closed_even_seconds_after_midnight(camera):
    next_midnight = NOW.replace(hour=0, minute=0, second=0) + timedelta(days=1)
    events = add_events(["white"], at=next_midnight - timedelta(seconds=1))
    set_total(events)
    AlwaysOnCounterCursor.objects.filter(camera=camera).update(
        event_caught_up_at=next_midnight + timedelta(seconds=2)
    )
    with patch(
        "django.utils.timezone.now", return_value=next_midnight + timedelta(seconds=2)
    ):
        result = payload()
    assert result["history_status"] == "complete"
    assert result["day_runs"][0]["status"] == "closed"


def test_calendar_bounds_use_plant_timezone_not_request_override(camera):
    midnight = NOW.replace(hour=0, minute=0, second=0)
    set_total(add_events(["red"], at=midnight))
    with timezone.override("America/New_York"):
        result = payload()
    assert result["history_status"] == "complete"
    assert result["day_runs"][0]["started_at"] == midnight.isoformat()


def test_display_clock_is_sampled_after_reading_newly_committed_evidence(camera):
    event = add_events(["red"], at=NOW + timedelta(seconds=1))
    set_total(event)
    read_evidence = False

    def observe_event_read(execute, sql, params, many, context):
        nonlocal read_evidence
        result = execute(sql, params, many, context)
        if sql.startswith("SELECT") and 'FROM "cameras_alwaysonimportedevent"' in sql:
            read_evidence = True
        return result

    with (
        connection.execute_wrapper(observe_event_read),
        patch(
            "django.utils.timezone.now",
            side_effect=lambda: NOW + timedelta(seconds=2) if read_evidence else NOW,
        ),
    ):
        result = payload()
    assert result["history_status"] == "complete"
