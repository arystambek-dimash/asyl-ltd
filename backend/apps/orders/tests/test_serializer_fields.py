import pytest
from decimal import Decimal
from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem, Payment
from apps.orders.serializers import OrderSerializer
from apps.clients.serializers import ClientSerializer

pytestmark = pytest.mark.django_db


def _order(client, qty=200, paid=None):
    prod, _ = Product.objects.get_or_create(
        name="Премиум", color="Red", weight_kg="50", defaults={"price": "100"})
    o = Order.objects.create(client=client, status="draft")
    OrderItem.objects.create(order=o, product=prod, quantity=qty)
    if paid is not None:
        Payment.objects.create(order=o, amount=paid)
    return o, prod


def test_bag_estimate_uses_counted_bags(boss):
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    o, prod = _order(c, qty=200)
    from apps.shipments.models import Shipment
    Shipment.objects.create(order=o, truck_number="X", bags_loaded=150)
    data = OrderSerializer(o).data
    # 150 counted × 50 = 7500 (NOT 200 ordered × 50 = 10000)
    assert data["bag_estimate_kg"] == "7500.00"
    assert data["bag_weight_kg"] == "50.00"


def test_debt_override_by_name_present(boss):
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    o, _ = _order(c)
    o.debt_override = True
    o.debt_override_by = boss
    o.save()
    data = OrderSerializer(o).data
    assert data["debt_override_by_name"] == boss.username

    o2, _ = _order(c)
    assert OrderSerializer(o2).data["debt_override_by_name"] is None


def test_client_debt_total(boss):
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    o, _ = _order(c, qty=10, paid="500")  # total 1000, paid 500 → долг 500
    # Долг считается только для отгружённого заказа «в долг».
    o.status = "shipped"; o.settlement_intent = "debt"; o.save()
    data = ClientSerializer(c).data
    assert data["debt_total"] == "500.00"

    c2 = Client.objects.create(first_name="C", last_name="D", phone="y")
    o2, _ = _order(c2, qty=10, paid="1000")
    o2.status = "shipped"; o2.save()
    assert ClientSerializer(c2).data["debt_total"] == "0.00"


def test_draft_orders_not_counted_as_debt(boss):
    # черновик без оплаты — НЕ долг
    c = Client.objects.create(first_name="E", last_name="F", phone="z")
    _order(c, qty=10)  # status draft, no payment
    assert ClientSerializer(c).data["debt_total"] == "0.00"
