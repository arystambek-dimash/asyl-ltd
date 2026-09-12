"""Бэкфилл payment_status (миграция 0039) чинит устаревшие статусы и совпадает с сервисом."""
import importlib

import pytest
from django.apps import apps as django_apps
from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem, Payment
from apps.orders.services import _payment_status_for

pytestmark = pytest.mark.django_db

migration = importlib.import_module("apps.orders.migrations.0039_backfill_payment_status")


def _order(client, product, qty, marked, paid=None, payment_status="confirmed", refunded="0"):
    order = Order.objects.create(client=client, status="shipped", payment_status=marked)
    OrderItem.objects.create(order=order, product=product, quantity=qty, unit_price="100.00")
    if paid is not None:
        Payment.objects.create(
            order=order, amount=paid, status=payment_status, refunded_amount=refunded)
    return order


def test_backfill_fixes_stale_statuses_in_one_pass():
    product = Product.objects.create(name="P", color="Red", weight_kg="50", price="100.00")
    client = Client.objects.create_with_user(first_name="Дана", last_name="X", phone="1")
    # Долг, ошибочно помеченный погашенным, — именно его список должников потерял бы.
    hidden_debt = _order(client, product, 2, "settled", paid="50")
    settled = _order(client, product, 1, "partial", paid="100")
    unpaid = _order(client, product, 1, "settled")
    rejected_only = _order(client, product, 1, "partial", paid="100", payment_status="rejected")
    refunded_back = _order(client, product, 1, "settled", paid="100", refunded="100")
    fine = _order(client, product, 1, "partial", paid="40")
    unpriced = Order.objects.create(client=client, status="shipped", payment_status="settled")
    OrderItem.objects.create(order=unpriced, product=product, quantity=1)

    migration.backfill_payment_status(django_apps, None)

    expected = {
        hidden_debt.id: "partial",
        settled.id: "settled",
        unpaid.id: "unpaid",
        rejected_only.id: "unpaid",
        refunded_back.id: "unpaid",
        fine.id: "partial",
        unpriced.id: "unpaid",
    }
    actual = dict(Order.objects.filter(pk__in=expected).values_list("id", "payment_status"))
    assert actual == expected
    # Миграция и сервис оплат считают статус одинаково.
    for order in Order.objects.filter(pk__in=expected).prefetch_related("items__product", "payments"):
        assert _payment_status_for(order) == order.payment_status
