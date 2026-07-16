from datetime import datetime

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.clients.models import Client
from apps.orders.models import Order


pytestmark = pytest.mark.django_db


def _api(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


def _order_on(client, year, month, day):
    order = Order.objects.create(client=client)
    created_at = timezone.make_aware(datetime(year, month, day, 12, 0))
    Order.all_objects.filter(pk=order.pk).update(created_at=created_at)
    return order


def test_list_filters_orders_by_inclusive_creation_date_range(manager):
    client = Client.objects.create(first_name="A", last_name="B", phone="1")
    before = _order_on(client, 2026, 6, 30)
    first = _order_on(client, 2026, 7, 1)
    last = _order_on(client, 2026, 7, 15)
    after = _order_on(client, 2026, 7, 16)

    response = _api(manager).get(
        "/api/orders/?date_from=2026-07-01&date_to=2026-07-15")

    assert response.status_code == 200
    assert {row["id"] for row in response.data} == {first.id, last.id}
    assert before.id not in {row["id"] for row in response.data}
    assert after.id not in {row["id"] for row in response.data}


@pytest.mark.parametrize("query", [
    "date_from=not-a-date",
    "date_to=2026-02-31",
    "date_from=2026-07-16&date_to=2026-07-01",
])
def test_list_rejects_invalid_date_filters(manager, query):
    response = _api(manager).get(f"/api/orders/?{query}")
    assert response.status_code == 400


def test_list_filters_by_public_status_group(manager):
    """«Ожидает загрузки» покрывает все внутренние этапы погрузки."""
    client = Client.objects.create(first_name="A", last_name="B", phone="1")
    statuses = {}
    for status in ("pending", "confirmed", "arrived", "loading", "loaded", "shipped"):
        statuses[status] = Order.objects.create(client=client, status=status)

    r = _api(manager).get("/api/orders/?status_group=confirmed")
    assert r.status_code == 200
    assert {row["id"] for row in r.data} == {
        statuses["confirmed"].id, statuses["arrived"].id, statuses["loading"].id}

    r = _api(manager).get("/api/orders/?status_group=shipped")
    assert {row["id"] for row in r.data} == {
        statuses["loaded"].id, statuses["shipped"].id}

    assert _api(manager).get("/api/orders/?status_group=bogus").status_code == 400
