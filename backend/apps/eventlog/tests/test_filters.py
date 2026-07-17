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


@pytest.mark.parametrize("params, code", [
    ({"date_from": "not-a-date"}, "bad_date"),
    ({"date_to": "2026-02-31"}, "bad_date"),
    ({"date_from": "2026-07-16", "date_to": "2026-07-01"}, "bad_range"),
])
def test_invalid_date_filters_return_normalized_400(auth_client, operator, params, code):
    resp = auth_client(operator).get("/api/events/", params)
    assert resp.status_code == 400
    assert resp.data["code"] == code


def test_event_payload_exposes_related_order(auth_client, operator):
    from apps.clients.models import Client
    from apps.orders.models import Order
    from apps.eventlog.models import EventLog

    client = Client.objects.create(first_name="A", last_name="B", phone="1")
    order = Order.objects.create(client=client)
    event = EventLog.objects.create(event_type="shipment", message="done", order=order)

    response = auth_client(operator).get("/api/events/")

    assert response.status_code == 200
    row = next(item for item in response.data if item["id"] == event.id)
    assert row["order"] == order.id


def test_order_events_include_all_departments(auth_client, operator):
    from apps.clients.models import Client
    from apps.orders.models import Order
    from apps.eventlog.models import EventLog

    main = Order.objects.create(client=Client.objects.create(
        first_name="Main", last_name="Client", phone="1"))
    field = Order.objects.create(
        client=Client.objects.create(
            first_name="Field", last_name="Client", phone="2"),
        department="field",
    )
    main_event = EventLog.objects.create(
        event_type="status", message="main", order=main)
    field_event = EventLog.objects.create(event_type="status", message="field", order=field)

    response = auth_client(operator).get("/api/events/")

    assert response.status_code == 200
    assert {row["id"] for row in response.data} == {main_event.id, field_event.id}
