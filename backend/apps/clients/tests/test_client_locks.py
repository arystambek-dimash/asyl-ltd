"""Блокировки клиента (clients/services.py): общие для view клиента и для
внесения оплаты по клиенту в кассе."""
import pytest
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from apps.clients.models import Client
from apps.clients.services import (
    ClientNoLongerAvailable,
    lock_client_orders,
    lock_client_with_orders,
    lock_scoped_client,
)
from apps.orders.models import Order

pytestmark = pytest.mark.django_db


def test_lock_scoped_client_checks_existence_and_department(departments, user_with_perms):
    mill, city = departments
    client = Client.objects.create_with_user(first_name="Свой", phone="1", department=mill)
    own = user_with_perms("own-cashier", department=mill)
    foreign = user_with_perms("foreign-cashier", department=city)
    gone = Client.objects.create_with_user(first_name="Удалённый", phone="2")
    gone_pk = gone.pk
    gone.delete()

    assert lock_scoped_client(client.pk) == client
    assert lock_scoped_client(client.pk, own) == client
    with pytest.raises(PermissionDenied):
        lock_scoped_client(client.pk, foreign)
    with pytest.raises(ClientNoLongerAvailable) as missing:
        lock_scoped_client(gone_pk)
    assert missing.value.status_code == 409
    assert missing.value.detail["code"] == "client_not_active"


def test_lock_client_orders_takes_every_order_of_client_including_trash():
    client = Client.objects.create_with_user(first_name="А", phone="1")
    live = Order.objects.create(client=client, status="shipped")
    trashed = Order.objects.create(client=client, status="shipped", deleted_at=timezone.now())
    other = Client.objects.create_with_user(first_name="Б", phone="2")
    Order.objects.create(client=other, status="shipped")

    assert [order.pk for order in lock_client_orders(client.pk)] == [live.pk, trashed.pk]
    locked, orders = lock_client_with_orders(client.pk, None)
    assert locked == client
    assert [order.pk for order in orders] == [live.pk, trashed.pk]
