from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.utils import timezone

from apps.cameras import ai, analytics
from apps.cameras.models import (
    ANALYTICS_SCOPE_SHIPPING,
    AlwaysOnColorProductMapping,
    AlwaysOnCountArchive,
    AlwaysOnCounterCursor,
    AlwaysOnDailyAnalytics,
    MonoblockCameraSettings,
    ShippingDailyAnalytics,
)
from apps.catalog.models import Product

pytestmark = pytest.mark.django_db


def test_brand_columns_keep_database_defaults():
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


def test_superuser_maps_production_colors_to_products(
    auth_client,
    admin_user,
    ai247_camera,
):
    red = Product.objects.create(
        name="Робот Кука",
        color="Red",
        weight_kg="50",
    )

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


def test_today_endpoint_returns_real_total(
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
    assert {item["camera"] for item in response.data["cameras"]} == {"cam3", "cam5"}
    cam3 = next(item for item in response.data["cameras"] if item["camera"] == "cam3")
    assert cam3["all_time_total"] == 18
    assert len(cam3["history"]) == 14
    assert cam3["colors"][0] == {"color": "red", "total": 13, "percent": 72.2}


@pytest.mark.parametrize("malformed", [None, "cam3", {"cam3": True}, 3])
def test_reconcile_never_switches_off_on_an_unreadable_camera_list(malformed, ai247_camera):
    """An unparsable reply means "unknown", never "no cameras configured".

    Coercing it to [] made a configured selection look like a mismatch, so the
    monitor pushed an empty set back to the camera PC and the administrator's
    24/7 cameras switched themselves off moments after being saved.
    """
    from apps.cameras import continuous

    with (
        patch.object(
            ai,
            "always_on_status",
            return_value={"camera_sources": malformed, "source": "sub", "processors": []},
        ),
        patch.object(ai, "configure_always_on") as configure,
        patch.object(
            continuous.event_sync,
            "sync_camera",
            return_value=continuous.event_sync.SyncResult(0, 0, 0, None, False),
        ),
    ):
        continuous.reconcile()

    configure.assert_not_called()
    assert MonoblockCameraSettings.continuous_sources() == ["cam3"]


def test_reconcile_still_pushes_the_desired_set_when_the_pc_disagrees(ai247_camera):
    """A readable mismatch must still be corrected from PostgreSQL."""
    from apps.cameras import continuous

    with (
        patch.object(
            ai,
            "always_on_status",
            return_value={
                "camera_sources": [],
                "source": "sub",
                "analytics_scopes": {},
                "processors": [],
            },
        ),
        patch.object(
            ai,
            "configure_always_on",
            return_value={
                "camera_sources": ["cam3"],
                "source": "sub",
                "analytics_scopes": {"cam3": "ai_247"},
                "processors": [],
            },
        ) as configure,
        patch.object(
            continuous.event_sync,
            "sync_camera",
            return_value=continuous.event_sync.SyncResult(0, 0, 0, None, False),
        ),
    ):
        continuous.reconcile()

    configure.assert_called_once_with(
        ["cam3"],
        "sub",
        analytics_scopes={"cam3": "ai_247"},
    )
