import pytest
from clients.models import Client
from orders.models import Order
from django.contrib.auth.models import Group

pytestmark = pytest.mark.django_db


def test_confirm_moves_draft_to_confirmed(auth_client, manager):
    c = Client.objects.create(first_name="L", last_name="К", phone="x")
    o = Order.objects.create(client=c, status="draft")
    resp = auth_client(manager).post(f"/api/orders/{o.id}/confirm/")
    assert resp.status_code == 200
    o.refresh_from_db()
    assert o.status == "confirmed"
