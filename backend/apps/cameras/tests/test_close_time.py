from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from apps.cameras import production_runs
from apps.cameras.models import AlwaysOnCloseTime
from apps.eventlog.models import EventLog

pytestmark = pytest.mark.django_db

ALMATY = ZoneInfo("Asia/Almaty")
NINE_PM = time(21)


def _at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=ALMATY)


def _day(day: int) -> date:
    return date(2026, 10, day)


def _close_hour(day: int) -> str:
    return timezone.localtime(production_runs.scheduled_for(_day(day))).strftime("%H:%M")


def test_new_hour_applies_to_the_open_shift_and_keeps_earlier_shifts(admin_user):
    effective = production_runs.set_close_time(NINE_PM, admin_user, now=_at(14, 12))

    assert effective == _day(14)
    assert _close_hour(14) == "21:00"
    assert _close_hour(13) == "19:00"
    # Bags after yesterday's 19:00 stay in today's shift; 19:00–21:00 is still today.
    assert production_runs.business_day_for(_at(13, 19, 30)) == _day(14)
    assert production_runs.business_day_for(_at(14, 20, 59)) == _day(14)
    assert production_runs.business_day_for(_at(14, 21)) == _day(15)
    assert production_runs.planned_close_time(_day(14)) is None
    event = EventLog.objects.get(event_type="always_on_close_time")
    assert event.user == admin_user
    assert event.payload == {"old": "19:00", "new": "21:00", "effective_day": "2026-10-14"}


@pytest.mark.parametrize(
    ("now", "new_hour"),
    [
        (_at(14, 18, 55), NINE_PM),  # less than the margin before the old close
        (_at(14, 18, 30), time(17)),  # the new close is already in the past
        (_at(14, 12), time(12, 5)),  # the new close is within the margin
    ],
    ids=["near-old-close", "earlier-than-now", "near-new-close"],
)
def test_too_late_for_the_open_shift_applies_from_the_next_one(admin_user, now, new_hour):
    effective = production_runs.set_close_time(new_hour, admin_user, now=now)

    assert effective == _day(15)
    assert _close_hour(14) == "19:00"
    assert _close_hour(15) == new_hour.strftime("%H:%M")
    assert production_runs.business_day_for(_at(14, 18, 59)) == _day(14)
    assert production_runs.business_day_for(_at(14, 19)) == _day(15)
    assert production_runs.planned_close_time(_day(14)) == (_day(15), new_hour)


def test_setting_the_hour_again_replaces_a_planned_change(admin_user):
    production_runs.set_close_time(NINE_PM, admin_user, now=_at(14, 18, 55))
    production_runs.set_close_time(time(19), admin_user, now=_at(14, 18, 56))

    assert production_runs.planned_close_time(_day(14)) is None
    assert _close_hour(15) == "19:00"
    assert _close_hour(16) == "19:00"


def test_same_hour_without_a_planned_change_writes_nothing(admin_user):
    assert production_runs.set_close_time(time(19), admin_user, now=_at(14, 12)) == _day(14)

    assert not AlwaysOnCloseTime.objects.exists()
    assert not EventLog.objects.filter(event_type="always_on_close_time").exists()


def test_close_time_api_is_superuser_only_and_returns_the_tab(
    auth_client, admin_user, boss, ai247_camera, monkeypatch
):
    monkeypatch.setattr(timezone, "now", lambda: _at(14, 12))
    body = {"camera": "cam3", "close_time": "21:00"}

    denied = auth_client(boss).put("/api/cameras/always-on-production/close-time/", body, format="json")
    assert denied.status_code == 403

    response = auth_client(admin_user).put(
        "/api/cameras/always-on-production/close-time/", body, format="json"
    )
    assert response.status_code == 200, response.data
    assert response.data["camera"] == "cam3"
    assert response.data["close_time"] == "21:00"
    assert response.data["next_run_at"].startswith("2026-10-14T21:00")
    assert response.data["planned_close_time"] is None

    monkeypatch.setattr(timezone, "now", lambda: _at(14, 20))
    later = auth_client(admin_user).put(
        "/api/cameras/always-on-production/close-time/",
        body | {"close_time": "20:00"},
        format="json",
    )
    assert later.status_code == 200, later.data
    # 20:00 already passed: today's shift still closes at 21:00, the new hour starts tomorrow.
    assert later.data["close_time"] == "21:00"
    assert later.data["planned_close_time"] == {"close_time": "20:00", "from_day": "2026-10-15"}

    invalid = auth_client(admin_user).put(
        "/api/cameras/always-on-production/close-time/",
        body | {"close_time": "25:00"},
        format="json",
    )
    assert invalid.status_code == 400
