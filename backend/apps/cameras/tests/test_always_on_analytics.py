from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.cameras import ai, analytics
from apps.cameras.models import (
    AlwaysOnCounterCursor,
    AlwaysOnDailyAnalytics,
    MonoblockCameraSettings,
)
from apps.eventlog.models import EventLog

pytestmark = pytest.mark.django_db


def live(total, *, mode="always_on", running=True, camera="cam3"):
    return {"processors": [{
        "cam": camera, "total": total, "mode": mode, "running": running,
    }]}


def test_snapshot_accumulates_delta_without_double_counting_and_survives_reset():
    analytics.record_snapshot(live(3))
    analytics.record_snapshot(live(7))
    analytics.record_snapshot(live(7))
    analytics.record_snapshot(live(2))

    row = AlwaysOnDailyAnalytics.objects.get(camera="cam3", day=timezone.localdate())
    assert row.model_total == 9
    assert row.total == 9
    assert AlwaysOnCounterCursor.objects.get(camera="cam3").last_total == 2


def test_session_count_is_not_added_to_background_analytics():
    analytics.record_snapshot(live(20, mode="session"))
    analytics.record_snapshot(live(0))
    analytics.record_snapshot(live(4))

    assert AlwaysOnDailyAnalytics.objects.get(camera="cam3").model_total == 4


def test_superuser_can_subtract_with_reason_and_audit(auth_client, admin_user, boss):
    MonoblockCameraSettings.objects.create(always_on_camera_sources=["cam3"])
    analytics.record_snapshot(live(12))

    forbidden = auth_client(boss).post(
        "/api/cameras/always-on-analytics/cam3/subtract/",
        {"amount": 2, "reason": "Ложное срабатывание"}, format="json",
    )
    assert forbidden.status_code == 403

    response = auth_client(admin_user).post(
        "/api/cameras/always-on-analytics/cam3/subtract/",
        {"amount": 2, "reason": "Ложное срабатывание"}, format="json",
    )
    assert response.status_code == 200
    assert response.data["model_total"] == 12
    assert response.data["adjustment"] == -2
    assert response.data["total"] == 10
    event = EventLog.objects.get(event_type="always_on_count_adjustment")
    assert event.user == admin_user
    assert event.payload["before"] == 12
    assert event.payload["after"] == 10
    assert event.payload["reason"] == "Ложное срабатывание"


def test_today_endpoint_returns_real_total_and_rejects_excess_subtraction(
        auth_client, admin_user, monkeypatch):
    MonoblockCameraSettings.objects.create(always_on_camera_sources=["cam3", "cam5"])
    monkeypatch.setattr(ai, "AI_KEY", "key")
    with patch.object(ai, "always_on_status", return_value={
        "processors": [
            {"cam": "cam3", "total": 8, "mode": "always_on", "running": True},
            {"cam": "cam5", "total": 5, "mode": "always_on", "running": True},
        ],
    }):
        response = auth_client(admin_user).get("/api/cameras/always-on-analytics/")

    assert response.status_code == 200
    assert response.data["total"] == 13
    assert {item["camera"] for item in response.data["cameras"]} == {"cam3", "cam5"}

    too_much = auth_client(admin_user).post(
        "/api/cameras/always-on-analytics/cam3/subtract/",
        {"amount": 9, "reason": "Проверка ограничения"}, format="json",
    )
    assert too_much.status_code == 400
