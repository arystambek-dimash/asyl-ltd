import pytest
from datetime import date
from unittest.mock import patch
from apps.catalog.models import Product
from apps.clients.models import Client, Store
from apps.orders.models import Order, OrderItem
from apps.orders.services import add_payment
from apps.clients.services import detect_overdue
from rest_framework.exceptions import ValidationError

pytestmark = pytest.mark.django_db


def _shipped_store_order():
    p = Product.objects.create(name="P", color="Red", weight_kg="50", price="100.00")
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    s = Store.objects.create(client=c, name="S",
                             payment_schedule_type="monthly", payment_days=[5])
    o = Order.objects.create(client=c, store=s, status="shipped")
    OrderItem.objects.create(order=o, product=p, quantity=1, unit_price="100.00")  # total 100
    return o, s


def test_payment_blocked_outside_window(boss):
    o, s = _shipped_store_order()
    with patch("apps.orders.services.timezone.localdate",
               return_value=date(2026, 6, 6)):  # not the 5th
        with pytest.raises(ValidationError) as e:
            add_payment(o, "100", boss)
    assert e.value.detail["code"] == "payment_window_closed"


def test_payment_allowed_inside_window(boss, settle_payment):
    o, s = _shipped_store_order()
    with patch("apps.orders.services.timezone.localdate",
               return_value=date(2026, 6, 5)):
        pay = add_payment(o, "100", boss)
    settle_payment(pay, boss)
    o.refresh_from_db()
    assert o.payment_status == "settled"


def test_detect_overdue_notifies(boss):
    o, s = _shipped_store_order()
    from apps.notifications.models import Notification
    assert detect_overdue(s, date(2026, 6, 5)) == 1
    assert Notification.objects.filter(client=s.client, text__icontains="Просрочка").exists()
