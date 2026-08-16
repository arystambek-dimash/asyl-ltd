from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.cameras import ai, analytics
from apps.cameras.models import (
    AlwaysOnColorProductMapping,
    AlwaysOnCounterCursor,
    AlwaysOnDailyAnalytics,
    MonoblockCameraSettings,
)
from apps.catalog.models import Product
from apps.eventlog.models import EventLog

pytestmark = pytest.mark.django_db


def live(total, *, mode="always_on", running=True, camera="cam3", per_color=None):
    return {"processors": [{
        "cam": camera, "total": total, "mode": mode, "running": running,
        "per_color": per_color or {},
    }]}


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
    assert row.total == 9
    cursor = AlwaysOnCounterCursor.objects.get(camera="cam3")
    assert cursor.last_total == 2
    assert cursor.last_per_color == {"red": 1, "blue": 1}


def test_session_count_is_not_added_to_background_analytics():
    analytics.record_snapshot(live(20, mode="session"))
    analytics.record_snapshot(live(0))
    analytics.record_snapshot(live(4))

    assert AlwaysOnDailyAnalytics.objects.get(camera="cam3").model_total == 4


def test_superuser_can_subtract_with_reason_and_audit(auth_client, admin_user, boss):
    MonoblockCameraSettings.objects.create(always_on_camera_sources=["cam3"])
    analytics.record_snapshot(live(12, per_color={"Red_50": 12}))

    forbidden = auth_client(boss).post(
        "/api/cameras/always-on-analytics/cam3/subtract/",
        {"amount": 2, "color": "red", "reason": "Ложное срабатывание"}, format="json",
    )
    assert forbidden.status_code == 403

    response = auth_client(admin_user).post(
        "/api/cameras/always-on-analytics/cam3/subtract/",
        {"amount": 2, "color": "red", "reason": "Ложное срабатывание"}, format="json",
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


def test_superuser_configures_color_to_product_route(auth_client, admin_user, boss):
    MonoblockCameraSettings.objects.create(always_on_camera_sources=["cam3"])
    red = Product.objects.create(
        name="Робот Кука", color="Red", weight_kg="50", price="100",
    )

    denied = auth_client(boss).get(
        "/api/cameras/always-on-production/?camera=cam3",
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
        "color": "red", "product": red.pk, "product_label": str(red),
    }
    assert AlwaysOnColorProductMapping.objects.get(
        camera="cam3", color="red",
    ).product == red

    mismatch = auth_client(admin_user).put(
        "/api/cameras/always-on-production/",
        {"camera": "cam3", "mappings": [{"color": "blue", "product": red.pk}]},
        format="json",
    )
    assert mismatch.status_code == 400


def test_today_endpoint_returns_real_total_and_rejects_excess_subtraction(
        auth_client, admin_user, monkeypatch):
    MonoblockCameraSettings.objects.create(always_on_camera_sources=["cam3", "cam5"])
    AlwaysOnDailyAnalytics.objects.create(
        camera="cam3",
        day=timezone.localdate() - timedelta(days=1),
        model_total=10,
        model_per_color={"red": 8, "blue": 2},
    )
    monkeypatch.setattr(ai, "AI_KEY", "key")
    with patch.object(ai, "always_on_status", return_value={
        "processors": [
            {"cam": "cam3", "total": 8, "mode": "always_on", "running": True,
             "per_color": {"Red_50": 5, "Blue_50": 3}},
            {"cam": "cam5", "total": 5, "mode": "always_on", "running": True,
             "per_color": {"Blue_25": 5}},
        ],
    }):
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
        {"amount": 9, "color": "red", "reason": "Проверка ограничения"}, format="json",
    )
    assert too_much.status_code == 400


@pytest.mark.parametrize("malformed", [None, "cam3", {"cam3": True}, 3])
def test_reconcile_never_switches_off_on_an_unreadable_camera_list(malformed):
    """An unparsable reply means "unknown", never "no cameras configured".

    Coercing it to [] made a configured selection look like a mismatch, so the
    monitor pushed an empty set back to the camera PC and the administrator's
    24/7 cameras switched themselves off moments after being saved.
    """
    from apps.cameras import continuous

    MonoblockCameraSettings.objects.update_or_create(
        singleton=True, defaults={"always_on_camera_sources": ["cam3"]},
    )

    with patch.object(
        ai, "always_on_status",
        return_value={"cameras": malformed, "source": "sub", "processors": []},
    ), patch.object(ai, "configure_always_on") as configure:
        continuous.reconcile()

    configure.assert_not_called()
    assert MonoblockCameraSettings.always_on_sources() == ["cam3"]


def test_reconcile_still_pushes_the_desired_set_when_the_pc_disagrees():
    """A readable mismatch must still be corrected from PostgreSQL."""
    from apps.cameras import continuous

    MonoblockCameraSettings.objects.update_or_create(
        singleton=True, defaults={"always_on_camera_sources": ["cam3"]},
    )

    with patch.object(
        ai, "always_on_status",
        return_value={"cameras": [], "source": "sub", "processors": []},
    ), patch.object(ai, "configure_always_on", return_value={
        "cameras": ["cam3"], "source": "sub", "processors": [],
    }) as configure:
        continuous.reconcile()

    configure.assert_called_once_with(["cam3"], "sub")
