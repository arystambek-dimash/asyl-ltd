import pytest
from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem, Payment

pytestmark = pytest.mark.django_db


def _order(status, intent, paid):
    c = Client.objects.create_with_user(first_name="A", last_name="B", phone="x")
    p = Product.objects.create(name="P", color="Red", weight_kg="50")
    o = Order.objects.create(client=c, status=status, settlement_intent=intent,
                             payment_status="unpaid")
    OrderItem.objects.create(order=o, product=p, quantity=2, unit_price="100.00")  # 200
    if paid:
        Payment.objects.create(order=o, amount=paid, status="confirmed")
    return o


@pytest.mark.parametrize("status,intent,paid,expected", [
    ("shipped", "debt", None, True),
    ("pending", "debt", None, False),
    ("draft", "debt", None, False),
    # Товар уехал — клиент должен, даже если собирался платить сразу или не выбрал способ.
    ("shipped", "instant", None, True),
    ("shipped", "pending", None, True),
    ("shipped", "debt", "200", False),
])
def test_is_debt(status, intent, paid, expected):
    assert _order(status, intent, paid).is_debt is expected
