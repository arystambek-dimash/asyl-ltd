import pytest
from apps.clients.models import Client
from apps.orders.models import Order
from apps.orders.serializers import OrderSerializer

pytestmark = pytest.mark.django_db


def test_serializer_exposes_payment_fields():
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    o = Order.objects.create(client=c)
    data = OrderSerializer(o).data
    assert data["payment_status"] == "unpaid"
    assert data["settlement_intent"] == "debt"
    assert "remaining_amount" in data
    assert data["is_debt"] is False
