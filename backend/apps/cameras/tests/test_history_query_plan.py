"""Список истории AI-сессий не должен расти по запросам вместе со строками.

Справочник подписей камер (MonoblockCameraSettings.display_names) читался
внутри сериализации каждой строки — на 500 строк это 500 лишних запросов.
"""
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.cameras.models import AiCountingSession, MonoblockCameraSettings
from apps.clients.models import Client
from apps.orders.models import Order

pytestmark = pytest.mark.django_db


def _sessions(count):
    client = Client.objects.create_with_user(first_name="A", last_name="B", phone="p")
    MonoblockCameraSettings.objects.create(
        singleton=True, camera_names={"cam1": "Ворота"})
    for _ in range(count):
        order = Order.objects.create(client=client, status="shipped")
        AiCountingSession.objects.create(
            order=order, camera="cam1", status="finished", final_total=5)


def test_history_query_count_does_not_grow_with_rows(auth_client, user_with_perms):
    user = user_with_perms("cam-history", codes=["shipping.view"])
    _sessions(12)

    with CaptureQueriesContext(connection) as queries:
        response = auth_client(user).get("/api/cameras/ai/history/")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 12
    assert body[0]["camera_name"] == "Ворота"
    # План запросов постоянный: до правки было 9 + по одному на строку.
    assert len(queries) < 15, f"N+1: {len(queries)} запросов на 12 строк"
