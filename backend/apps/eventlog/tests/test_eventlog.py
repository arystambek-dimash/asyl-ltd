import pytest
from apps.eventlog.models import EventLog
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
    assert resp.data[0]["message"] == "second"
