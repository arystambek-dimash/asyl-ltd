import pytest
from apps.clients.models import Client
from apps.orders.models import Order, Payment
from apps.orders.serializers import OrderSerializer, PaymentSerializer

pytestmark = pytest.mark.django_db


def test_serializer_exposes_payment_fields():
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    o = Order.objects.create(client=c)
    data = OrderSerializer(o).data
    assert data["payment_status"] == "unpaid"
    assert data["settlement_intent"] == "debt"
    assert data["payment_method"] == "debt"
    assert "remaining_amount" in data
    assert data["is_debt"] is False


@pytest.mark.parametrize(
    ("method", "label"),
    [("invoice", "Счет на оплату"), ("kaspi", "Kaspi"),
     ("cash", "Наличные"), ("debt", "Долг")],
)
def test_payment_method_labels(method, label):
    client = Client.objects.create(first_name="A", last_name="B", phone=method)
    order = Order.objects.create(client=client)
    payment = Payment.objects.create(order=order, amount="100", method=method)
    assert PaymentSerializer(payment).data["method_label"] == label
