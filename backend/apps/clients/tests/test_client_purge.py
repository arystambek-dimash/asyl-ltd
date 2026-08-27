"""Суперадминская зачистка тестовых клиентов вместе с историей."""
from decimal import Decimal

import pytest

from apps.cameras.models import AiCountingSession
from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem, Payment

pytestmark = pytest.mark.django_db


def _client_with_history():
    product = Product.objects.create(
        name="Мука", color="Red", weight_kg="50", price="1000")
    client = Client.objects.create_with_user(
        first_name="Тест", last_name="Клиент", phone="x")
    client.user.is_active = True
    client.user.save(update_fields=["is_active"])
    order = Order.objects.create(client=client, status="shipped")
    OrderItem.objects.create(
        order=order, product=product, quantity=1, unit_price=Decimal("1000"))
    Payment.objects.create(
        order=order, amount=Decimal("100"), method="cash", status="confirmed")
    return client


def test_superadmin_purges_client_with_orders(auth_client, make_user):
    client = _client_with_history()
    portal_user = client.user
    root = make_user(username="root")
    root.is_superuser = True
    root.save(update_fields=["is_superuser"])

    resp = auth_client(root).post(f"/api/clients/{client.id}/purge/")

    assert resp.status_code == 204
    assert not Client.objects.filter(pk=client.pk).exists()
    assert not Order.all_objects.filter(client_id=client.pk).exists()
    assert not Payment.objects.filter(order__client_id=client.pk).exists()
    portal_user.refresh_from_db()
    assert portal_user.is_active is False


def test_purge_denied_for_non_superadmin(auth_client, user_with_perms):
    client = _client_with_history()
    manager = user_with_perms(
        "cleaner", codes=["clients.view", "clients.delete"])

    resp = auth_client(manager).post(f"/api/clients/{client.id}/purge/")

    assert resp.status_code == 403
    assert Client.objects.filter(pk=client.pk).exists()


def test_superadmin_cannot_purge_client_with_open_ai_session(
    auth_client, make_user,
):
    client = _client_with_history()
    order = Order.objects.get(client=client)
    order.status = "loading"
    order.loading_camera = "cam2"
    order.save(update_fields=["status", "loading_camera"])
    session = AiCountingSession.objects.create(
        order=order,
        camera="cam2",
        status=AiCountingSession.ACTIVE,
    )
    root = make_user(username="active-purge-root")
    root.is_superuser = True
    root.save(update_fields=["is_superuser"])

    response = auth_client(root).post(f"/api/clients/{client.id}/purge/")

    assert response.status_code == 400
    assert response.data["code"] == "active_loading"
    assert Client.objects.filter(pk=client.pk).exists()
    assert Order.all_objects.filter(pk=order.pk).exists()
    assert AiCountingSession.objects.filter(pk=session.pk).exists()
