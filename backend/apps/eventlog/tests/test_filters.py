import pytest
from apps.eventlog.services import log_event

pytestmark = pytest.mark.django_db


def test_filter_by_event_type(auth_client, operator):
    log_event("payment", "Оплата 100", user=operator)
    log_event("stock_adjust", "Корректировка +50", user=operator)
    resp = auth_client(operator).get("/api/events/?event_type=payment")
    assert resp.status_code == 200
    assert len(resp.data) == 1
    assert resp.data[0]["event_type"] == "payment"


def test_filter_by_search(auth_client, operator):
    log_event("status", "Заказ подтверждён", user=operator)
    log_event("status", "Заказ оплачен", user=operator)
    resp = auth_client(operator).get("/api/events/?search=оплачен")
    assert resp.status_code == 200
    assert len(resp.data) == 1
    assert "оплачен" in resp.data[0]["message"]
