import pytest
from apps.clients.models import Client
from apps.orders.models import Order, StatusChangeRequest
from apps.orders import services
from rest_framework.exceptions import ValidationError

pytestmark = pytest.mark.django_db


def _order():
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    return Order.objects.create(client=c, status="confirmed")


def test_editor_changes_status_immediately(manager):
    # manager has orders.edit
    o = _order()
    res = services.request_status_change(o, "arrived", manager)
    assert res["applied"] is True
    o.refresh_from_db()
    assert o.status == "arrived"
    assert StatusChangeRequest.objects.count() == 0


def test_operator_creates_request(operator):
    # operator lacks orders.edit
    o = _order()
    res = services.request_status_change(o, "arrived", operator)
    assert res["applied"] is False
    o.refresh_from_db()
    assert o.status == "confirmed"  # unchanged
    req = StatusChangeRequest.objects.get()
    assert req.status == "pending"
    assert req.to_status == "arrived"


def test_approve_request_applies_status(operator, boss):
    o = _order()
    services.request_status_change(o, "arrived", operator)
    req = StatusChangeRequest.objects.get()
    services.approve_status_change(req, boss)
    o.refresh_from_db(); req.refresh_from_db()
    assert o.status == "arrived"
    assert req.status == "approved"
    assert req.decided_by == boss


def test_reject_request_keeps_status(operator, boss):
    o = _order()
    services.request_status_change(o, "arrived", operator)
    req = StatusChangeRequest.objects.get()
    services.reject_status_change(req, boss)
    o.refresh_from_db(); req.refresh_from_db()
    assert o.status == "confirmed"
    assert req.status == "rejected"


def test_double_decide_rejected(operator, boss):
    o = _order()
    services.request_status_change(o, "arrived", operator)
    req = StatusChangeRequest.objects.get()
    services.approve_status_change(req, boss)
    with pytest.raises(ValidationError):
        services.reject_status_change(req, boss)


def test_set_status_endpoint_operator_gets_202(auth_client, operator):
    o = _order()
    r = auth_client(operator).post(f"/api/orders/{o.id}/set-status/", {"status": "arrived"}, format="json")
    assert r.status_code == 202
    assert r.data["applied"] is False
    assert r.data["request"]["to_status"] == "arrived"


def test_set_status_endpoint_editor_applies(auth_client, manager):
    o = _order()
    r = auth_client(manager).post(f"/api/orders/{o.id}/set-status/", {"status": "arrived"}, format="json")
    assert r.status_code == 200
    assert r.data["applied"] is True
    assert r.data["order"]["status"] == "arrived"


def test_approve_endpoint(auth_client, operator, manager):
    o = _order()
    services.request_status_change(o, "arrived", operator)
    req = StatusChangeRequest.objects.get()
    r = auth_client(manager).post(f"/api/orders/{o.id}/status-requests/{req.id}/approve/")
    assert r.status_code == 200
    o.refresh_from_db()
    assert o.status == "arrived"
