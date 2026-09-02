from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.utils import timezone

from apps.cameras import ai, analytics
from apps.cameras.models import (
    ANALYTICS_SCOPE_AI247,
    ANALYTICS_SCOPE_SHIPPING,
    AlwaysOnColorProductMapping,
    AlwaysOnCountArchive,
    AlwaysOnCounterCursor,
    AlwaysOnDailyAnalytics,
    AlwaysOnImportedEvent,
    ContinuousCameraRole,
    MonoblockCameraSettings,
    ShippingDailyAnalytics,
)
from apps.catalog.models import Product
from apps.eventlog.models import EventLog

pytestmark = pytest.mark.django_db


def live(total, *, mode="always_on", running=True, camera="cam3", per_color=None):
    return {
        "processors": [
            {
                "cam": camera,
                "total": total,
                "mode": mode,
                "running": running,
                "per_color": per_color or {},
            }
        ]
    }


@pytest.mark.parametrize("value", [None, {}, [], True, float("inf"), float("nan")])
def test_processor_total_rejects_non_finite_or_malformed_values(value):
    assert analytics._processor_total({"total": value}) is None


def test_snapshot_accumulates_delta_without_double_counting_and_survives_reset():
    analytics.record_snapshot(live(3, per_color={"Red_50": 2, "Blue_50": 1}))
    analytics.record_snapshot(live(7, per_color={"Red_50": 5, "Blue_50": 2}))
    analytics.record_snapshot(live(7, per_color={"Red_50": 5, "Blue_50": 2}))
    analytics.record_snapshot(live(2, per_color={"Red_50": 1, "Blue_50": 1}))

    row = AlwaysOnDailyAnalytics.objects.get(camera="cam3", day=timezone.localdate())
    assert row.model_total == 9
    assert row.model_per_color == {"red": 6, "blue": 3}
    assert row.model_per_brand == {"unclassified": 9}
    assert row.total == 9
    cursor = AlwaysOnCounterCursor.objects.get(camera="cam3")
    assert cursor.last_total == 2
    assert cursor.last_per_color == {"red": 1, "blue": 1}


def test_brand_columns_keep_database_defaults_for_old_image_rollback():
    loader = MigrationLoader(connection)
    migration_apps = loader.project_state(
        [("cameras", "0025_always_on_brand_analytics")]
    ).apps
    for runtime_model, model_name in (
        (AlwaysOnDailyAnalytics, "AlwaysOnDailyAnalytics"),
        (AlwaysOnCountArchive, "AlwaysOnCountArchive"),
    ):
        assert runtime_model._meta.get_field("model_per_brand").db_default == {}
        migration_model = migration_apps.get_model("cameras", model_name)
        assert migration_model._meta.get_field("model_per_brand").db_default == {}

    # Automatic rollback restores the previous image without reversing schema
    # migrations. Its historical ORM omits the new columns from INSERTs, so the
    # permanent PostgreSQL defaults must keep both write paths valid.
    old_apps = loader.project_state(
        [("cameras", "0023_remove_legacy_esp_schema")]
    ).apps
    old_daily = old_apps.get_model("cameras", "AlwaysOnDailyAnalytics")
    old_imported = old_apps.get_model("cameras", "AlwaysOnImportedEvent")
    old_archive = old_apps.get_model("cameras", "AlwaysOnCountArchive")
    day = timezone.localdate()
    daily = old_daily.objects.create(camera="cam31", day=day)
    imported = old_imported.objects.create(
        camera="cam31",
        upstream_event_id=1,
        occurred_at=timezone.now(),
        source="sub",
        mode="always_on",
        class_name="Red_50",
    )
    archive = old_archive.objects.create(
        camera="cam31",
        period_start=day,
        period_end=day,
    )

    assert AlwaysOnDailyAnalytics.objects.get(pk=daily.pk).model_per_brand == {}
    assert AlwaysOnImportedEvent.objects.get(pk=imported.pk).analytics_scope == "ai_247"
    assert AlwaysOnCountArchive.objects.get(pk=archive.pk).model_per_brand == {}


def test_shipping_and_ai247_analytics_are_disjoint_and_report_sync_state(
    auth_client,
    admin_user,
):
    today = timezone.localdate()
    now = timezone.now()
    MonoblockCameraSettings.objects.create(
        camera_sources=["cam2"],
        always_on_camera_sources=["cam3"],
    )
    AlwaysOnDailyAnalytics.objects.bulk_create(
        [
            AlwaysOnDailyAnalytics(
                camera="cam2",
                day=today,
                model_total=100,
            ),
            AlwaysOnDailyAnalytics(
                camera="cam3",
                day=today,
                model_total=7,
            ),
        ]
    )
    ShippingDailyAnalytics.objects.bulk_create(
        [
            ShippingDailyAnalytics(
                camera="cam2",
                day=today,
                model_total=4,
            ),
            ShippingDailyAnalytics(
                camera="cam3",
                day=today,
                model_total=200,
            ),
        ]
    )
    AlwaysOnCounterCursor.objects.bulk_create(
        [
            AlwaysOnCounterCursor(
                camera=camera,
                event_sync_supported=True,
                event_boundary_validated=True,
                event_caught_up_at=now,
            )
            for camera in ("cam2", "cam3")
        ]
    )

    shipping = auth_client(admin_user).get(
        "/api/cameras/shipping-continuous-analytics/"
    )
    ai247 = auth_client(admin_user).get("/api/cameras/always-on-analytics/")

    assert shipping.status_code == 200
    assert shipping.data["analytics_scope"] == "shipping"
    assert shipping.data["total"] == 4
    assert [row["camera"] for row in shipping.data["cameras"]] == ["cam2"]
    assert shipping.data["analytics_sync"]["available"] is True
    assert shipping.data["cameras"][0]["analytics_sync"]["status"] == "synced"
    assert ai247.status_code == 200
    assert ai247.data["analytics_scope"] == "ai_247"
    assert ai247.data["total"] == 7
    assert [row["camera"] for row in ai247.data["cameras"]] == ["cam3"]


def test_shipping_analytics_marks_failed_import_instead_of_presenting_zero_as_fresh(
    auth_client,
    admin_user,
):
    MonoblockCameraSettings.objects.create(camera_sources=["cam2"])
    ShippingDailyAnalytics.objects.create(
        camera="cam2",
        day=timezone.localdate(),
        model_total=9,
    )
    AlwaysOnCounterCursor.objects.create(
        camera="cam2",
        event_sync_supported=True,
        event_boundary_validated=True,
        event_sync_error="camera PC timeout",
        event_sync_failed_at=timezone.now(),
    )

    response = auth_client(admin_user).get(
        "/api/cameras/shipping-continuous-analytics/"
    )

    assert response.status_code == 200
    assert response.data["total"] == 9
    assert response.data["analytics_sync"]["available"] is False
    assert response.data["analytics_sync"]["status"] == "error"
    assert response.data["cameras"][0]["analytics_sync"]["error"] == (
        "camera PC timeout"
    )


def test_missing_event_cursor_marks_zero_analytics_unavailable(
    auth_client,
    admin_user,
):
    MonoblockCameraSettings.objects.create(camera_sources=["cam2"])

    response = auth_client(admin_user).get(
        "/api/cameras/shipping-continuous-analytics/"
    )

    assert response.status_code == 200
    assert response.data["total"] == 0
    assert response.data["analytics_sync"] == {
        "status": "pending",
        "available": False,
        "detail": "Журнал событий камеры ещё не проверен",
    }
    assert response.data["cameras"][0]["analytics_sync"]["available"] is False


def test_today_payload_reads_sync_cursor_before_daily_totals():
    """A concurrent import must not expose a fresh cursor with stale zero rows."""

    MonoblockCameraSettings.objects.create(camera_sources=["cam2"])
    calls = []
    cursor_filter = AlwaysOnCounterCursor.objects.filter
    daily_filter = ShippingDailyAnalytics.objects.filter

    def tracked_cursor_filter(*args, **kwargs):
        calls.append("cursor")
        return cursor_filter(*args, **kwargs)

    def tracked_daily_filter(*args, **kwargs):
        calls.append("daily")
        return daily_filter(*args, **kwargs)

    with (
        patch.object(
            AlwaysOnCounterCursor.objects,
            "filter",
            side_effect=tracked_cursor_filter,
        ),
        patch.object(
            ShippingDailyAnalytics.objects,
            "filter",
            side_effect=tracked_daily_filter,
        ),
    ):
        analytics.today_payload(ANALYTICS_SCOPE_SHIPPING)

    assert calls.index("cursor") < calls.index("daily")


def test_session_count_is_not_added_to_background_analytics():
    analytics.record_snapshot(live(20, mode="session"))
    analytics.record_snapshot(live(0))
    analytics.record_snapshot(live(4))

    assert AlwaysOnDailyAnalytics.objects.get(camera="cam3").model_total == 4


def test_superuser_can_subtract_with_reason_and_audit(auth_client, admin_user, boss):
    MonoblockCameraSettings.objects.create(always_on_camera_sources=["cam3"])
    ContinuousCameraRole.objects.create(
        camera="cam3",
        analytics_scope=ANALYTICS_SCOPE_AI247,
    )
    analytics.record_snapshot(live(12, per_color={"Red_50": 12}))

    forbidden = auth_client(boss).post(
        "/api/cameras/always-on-analytics/cam3/subtract/",
        {"amount": 2, "color": "red", "reason": "Ложное срабатывание"},
        format="json",
    )
    assert forbidden.status_code == 403

    response = auth_client(admin_user).post(
        "/api/cameras/always-on-analytics/cam3/subtract/",
        {"amount": 2, "color": "red", "reason": "Ложное срабатывание"},
        format="json",
    )
    assert response.status_code == 200
    assert response.data["model_total"] == 12
    assert response.data["adjustment"] == -2
    assert response.data["total"] == 10
    event = EventLog.objects.get(event_type="always_on_count_adjustment")
    assert event.user == admin_user
    assert event.payload["before"] == 12
    assert event.payload["after"] == 10
    assert event.payload["color"] == "red"
    assert event.payload["reason"] == "Ложное срабатывание"


def test_loader_reads_production_but_only_manager_changes_route(
    auth_client,
    admin_user,
    boss,
):
    MonoblockCameraSettings.objects.create(always_on_camera_sources=["cam3"])
    ContinuousCameraRole.objects.create(
        camera="cam3",
        analytics_scope=ANALYTICS_SCOPE_AI247,
    )
    red = Product.objects.create(
        name="Робот Кука",
        color="Red",
        weight_kg="50",
        price="100",
    )

    readable = auth_client(boss).get(
        "/api/cameras/always-on-production/?camera=cam3",
    )
    assert readable.status_code == 200

    denied = auth_client(boss).put(
        "/api/cameras/always-on-production/",
        {"camera": "cam3", "mappings": [{"color": "red", "product": red.pk}]},
        format="json",
    )
    assert denied.status_code == 403

    response = auth_client(admin_user).put(
        "/api/cameras/always-on-production/",
        {"camera": "cam3", "mappings": [{"color": "red", "product": red.pk}]},
        format="json",
    )

    assert response.status_code == 200
    assert response.data["camera"] == "cam3"
    assert response.data["timezone"] == "Asia/Almaty"
    assert response.data["close_time"] == "19:00"
    assert next(
        item for item in response.data["mappings"] if item["color"] == "red"
    ) == {
        "color": "red",
        "product": red.pk,
        "product_label": str(red),
    }
    assert (
        AlwaysOnColorProductMapping.objects.get(
            camera="cam3",
            color="red",
        ).product
        == red
    )

    mismatch = auth_client(admin_user).put(
        "/api/cameras/always-on-production/",
        {"camera": "cam3", "mappings": [{"color": "blue", "product": red.pk}]},
        format="json",
    )
    assert mismatch.status_code == 400


def test_today_endpoint_returns_real_total_and_rejects_excess_subtraction(
    auth_client, admin_user
):
    MonoblockCameraSettings.objects.create(always_on_camera_sources=["cam3", "cam5"])
    AlwaysOnDailyAnalytics.objects.create(
        camera="cam3",
        day=timezone.localdate() - timedelta(days=1),
        model_total=10,
        model_per_color={"red": 8, "blue": 2},
    )
    AlwaysOnDailyAnalytics.objects.create(
        camera="cam3",
        day=timezone.localdate(),
        model_total=8,
        model_per_color={"red": 5, "blue": 3},
    )
    AlwaysOnDailyAnalytics.objects.create(
        camera="cam5",
        day=timezone.localdate(),
        model_total=5,
        model_per_color={"blue": 5},
    )

    response = auth_client(admin_user).get("/api/cameras/always-on-analytics/")

    assert response.status_code == 200
    assert response.data["total"] == 13
    assert response.data["all_time_total"] == 23
    assert len(response.data["history"]) == 14
    assert response.data["dominant_color"] == "red"
    assert {item["camera"] for item in response.data["cameras"]} == {"cam3", "cam5"}
    cam3 = next(item for item in response.data["cameras"] if item["camera"] == "cam3")
    assert cam3["all_time_total"] == 18
    assert len(cam3["history"]) == 14
    assert cam3["dominant_color"] == "red"
    assert cam3["colors"][0] == {"color": "red", "total": 13, "percent": 72.2}

    too_much = auth_client(admin_user).post(
        "/api/cameras/always-on-analytics/cam3/subtract/",
        {"amount": 9, "color": "red", "reason": "Проверка ограничения"},
        format="json",
    )
    assert too_much.status_code == 400


def test_today_endpoint_returns_brand_breakdown_per_camera_and_day(
    auth_client,
    admin_user,
):
    today = timezone.localdate()
    yesterday = today - timedelta(days=1)
    MonoblockCameraSettings.objects.create(always_on_camera_sources=["cam3", "cam5"])
    AlwaysOnDailyAnalytics.objects.create(
        camera="cam3",
        day=yesterday,
        model_total=2,
        model_per_brand={"korol": 2},
    )
    AlwaysOnDailyAnalytics.objects.create(
        camera="cam3",
        day=today,
        model_total=4,
        model_per_brand={"korol": 2, "unknown": 1, "unclassified": 1},
    )
    AlwaysOnDailyAnalytics.objects.create(
        camera="cam5",
        day=today,
        model_total=3,
        model_per_brand={"dikhan_baba": 3},
    )

    response = auth_client(admin_user).get("/api/cameras/always-on-analytics/")

    assert response.status_code == 200
    assert response.data["model_per_brand"] == {
        "korol": 4,
        "unknown": 1,
        "unclassified": 1,
        "dikhan_baba": 3,
    }
    assert response.data["dominant_brand"] == "korol"
    assert sum(round(item["percent"] * 10) for item in response.data["brands"]) == 1000
    cam3 = next(item for item in response.data["cameras"] if item["camera"] == "cam3")
    assert cam3["dominant_brand"] == "korol"
    assert cam3["model_per_brand"] == {
        "korol": 2,
        "unknown": 1,
        "unclassified": 1,
    }
    assert {item["brand"]: item["total"] for item in cam3["brands"]} == {
        "korol": 4,
        "unknown": 1,
        "unclassified": 1,
    }
    today_point = next(
        item for item in cam3["history"] if item["day"] == today.isoformat()
    )
    assert today_point["model_per_brand"] == {
        "korol": 2,
        "unknown": 1,
        "unclassified": 1,
    }
    assert sum(item["total"] for item in today_point["brands"]) == 4


def test_legacy_daily_rows_are_reported_as_unclassified_brand(
    auth_client,
    admin_user,
):
    MonoblockCameraSettings.objects.create(always_on_camera_sources=["cam3"])
    AlwaysOnDailyAnalytics.objects.create(
        camera="cam3",
        day=timezone.localdate(),
        model_total=7,
        model_per_brand={},
    )

    response = auth_client(admin_user).get("/api/cameras/always-on-analytics/")

    assert response.status_code == 200
    assert response.data["brands"] == [
        {"brand": "unclassified", "total": 7, "percent": 100.0}
    ]
    assert response.data["dominant_brand"] is None


@pytest.mark.parametrize("malformed", [None, "cam3", {"cam3": True}, 3])
def test_reconcile_never_switches_off_on_an_unreadable_camera_list(malformed):
    """An unparsable reply means "unknown", never "no cameras configured".

    Coercing it to [] made a configured selection look like a mismatch, so the
    monitor pushed an empty set back to the camera PC and the administrator's
    24/7 cameras switched themselves off moments after being saved.
    """
    from apps.cameras import continuous

    MonoblockCameraSettings.objects.update_or_create(
        singleton=True,
        defaults={"always_on_camera_sources": ["cam3"]},
    )
    ContinuousCameraRole.objects.update_or_create(
        camera="cam3",
        defaults={"analytics_scope": ANALYTICS_SCOPE_AI247},
    )

    with (
        patch.object(
            ai,
            "always_on_status",
            return_value={"cameras": malformed, "source": "sub", "processors": []},
        ),
        patch.object(ai, "configure_always_on") as configure,
        patch.object(
            continuous.event_sync,
            "sync_camera",
            return_value=continuous.event_sync.SyncResult(False, 0, 0, 0, None, False),
        ),
    ):
        continuous.reconcile()

    configure.assert_not_called()
    assert MonoblockCameraSettings.always_on_sources() == ["cam3"]


def test_reconcile_still_pushes_the_desired_set_when_the_pc_disagrees():
    """A readable mismatch must still be corrected from PostgreSQL."""
    from apps.cameras import continuous

    MonoblockCameraSettings.objects.update_or_create(
        singleton=True,
        defaults={"always_on_camera_sources": ["cam3"]},
    )
    ContinuousCameraRole.objects.update_or_create(
        camera="cam3",
        defaults={"analytics_scope": ANALYTICS_SCOPE_AI247},
    )

    with (
        patch.object(
            ai,
            "always_on_status",
            return_value={
                "cameras": [],
                "source": "sub",
                "analytics_scopes": {},
                "processors": [],
            },
        ),
        patch.object(
            ai,
            "configure_always_on",
            return_value={
                "cameras": ["cam3"],
                "source": "sub",
                "analytics_scopes": {"cam3": "ai_247"},
                "processors": [],
            },
        ) as configure,
        patch.object(
            continuous.event_sync,
            "sync_camera",
            return_value=continuous.event_sync.SyncResult(False, 0, 0, 0, None, False),
        ),
    ):
        continuous.reconcile()

    configure.assert_called_once_with(
        ["cam3"],
        "sub",
        analytics_scopes={"cam3": "ai_247"},
    )
