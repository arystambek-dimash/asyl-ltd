"""Суперадминская зачистка тестовых клиентов вместе с историей."""
from decimal import Decimal

import pytest

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem, Payment

pytestmark = pytest.mark.django_db


def _client_with_history():
    product = Product.objects.create(
        name="Мука", color="Red", weight_kg="50", price="1000")
    client = Client.objects.create(
        first_name="Тест", last_name="Клиент", phone="x")
    order = Order.objects.create(client=client, status="shipped")
    OrderItem.objects.create(
        order=order, product=product, quantity=1, unit_price=Decimal("1000"))
    Payment.objects.create(
        order=order, amount=Decimal("100"), method="cash", status="confirmed")
    return client


def test_superadmin_purges_client_with_orders(auth_client, make_user):
    client = _client_with_history()
    root = make_user(username="root")
    root.is_superuser = True
    root.save(update_fields=["is_superuser"])

    resp = auth_client(root).post(f"/api/clients/{client.id}/purge/")

    assert resp.status_code == 204
    assert not Client.objects.filter(pk=client.pk).exists()
    assert not Order.all_objects.filter(client_id=client.pk).exists()
    assert not Payment.objects.filter(order__client_id=client.pk).exists()


def test_purge_denied_for_non_superadmin(auth_client, user_with_perms):
    client = _client_with_history()
    manager = user_with_perms(
        "cleaner", codes=["clients.view", "clients.delete"])

    resp = auth_client(manager).post(f"/api/clients/{client.id}/purge/")

    assert resp.status_code == 403
    assert Client.objects.filter(pk=client.pk).exists()
