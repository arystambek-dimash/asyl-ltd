import pytest
from apps.eventlog.services import log_event

pytestmark = pytest.mark.django_db


def test_log_event_creates_entry(boss):
    e = log_event("arrival", "Машина прибыла", user=boss, payload={"net": 1000})
    assert e.pk is not None
    assert e.payload["net"] == 1000


def test_eventlog_is_append_only_no_update(boss):
    e = log_event("arrival", "msg", user=boss)
    e.message = "changed"
    with pytest.raises(Exception):
        e.save()


def test_eventlog_no_delete(boss):
    e = log_event("arrival", "msg", user=boss)
    with pytest.raises(Exception):
        e.delete()


def test_events_endpoint_lists_newest_first(auth_client, operator):
    log_event("a", "first", user=operator)
    log_event("b", "second", user=operator)
    resp = auth_client(operator).get("/api/events/")
    assert resp.status_code == 200
    assert resp.data["count"] == 2
    assert resp.data["results"][0]["message"] == "second"


def test_events_endpoint_paginates_without_truncating_history(auth_client, operator):
    from apps.eventlog.models import EventLog

    EventLog.objects.bulk_create(
        [
            EventLog(event_type="status", message=f"event-{index}", user=operator)
            for index in range(1005)
        ]
    )

    first = auth_client(operator).get("/api/events/?page=1&page_size=100")
    last = auth_client(operator).get("/api/events/?page=11&page_size=100")

    assert first.status_code == 200
    assert first.data["count"] == 1005
    assert len(first.data["results"]) == 100
    assert first.data["previous"] is None
    assert first.data["next"] is not None

    assert last.status_code == 200
    assert last.data["count"] == 1005
    assert len(last.data["results"]) == 5
    assert last.data["previous"] is not None
    assert last.data["next"] is None
    assert last.data["results"][-1]["message"] == "event-0"
