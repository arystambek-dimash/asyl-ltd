from importlib import import_module
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.db import connection

from apps.clients.models import Client
from apps.eventlog.models import EventLog
from apps.orders.models import Order, Payment
from apps.sales.models import Department

pytestmark = pytest.mark.django_db


def test_cleanup_only_untouched_portal_default_and_api_labels(auth_client, manager):
    dept = Department.objects.create(
        code="legacy-default", name="Мельница", is_default=True
    )
    Department.objects.exclude(pk=dept.pk).update(is_default=False)
    client = Client.objects.create_with_user(first_name="No department", phone="x")

    def request(**kwargs):
        values = dict(
            client=client,
            department=dept.code,
            status="pending",
            settlement_intent="pending",
            payment_method="pending",
        )
        values.update(kwargs)
        return Order.objects.create(**values)

    legacy = request()
    confirmed = request(status="confirmed")
    staff = request(created_by=manager)
    assigned_client = Client.objects.create_with_user(
        first_name="Assigned", phone="y", department=dept
    )
    assigned = request(client=assigned_client)
    edited = request()
    EventLog.objects.create(
        order=edited, event_type="status", message="Отдел заказа изменён"
    )
    paid = request()
    Payment.objects.create(order=paid, amount=1)
    different = request(department="another")
    migration = import_module(
        "apps.orders.migrations.0036_clear_legacy_portal_department"
    )
    for _ in range(2):
        migration.clear_legacy_portal_department(
            apps, SimpleNamespace(connection=connection)
        )
    legacy.refresh_from_db()
    assert legacy.department == ""
    assert legacy.events.filter(event_type="order_department_cleanup").count() == 1
    for order in [confirmed, staff, assigned, edited, paid]:
        order.refresh_from_db()
        assert order.department == dept.code
    different.refresh_from_db()
    assert different.department == "another"
    api = auth_client(manager)
    detail = api.get(f"/api/orders/{legacy.pk}/").data
    assert detail["department"] == ""
    assert detail["department_name"] == "Нет отдела"
    rows = api.get("/api/orders/?status=pending&page=1").data["results"]
    assert (
        next(row for row in rows if row["id"] == legacy.pk)["department_name"]
        == "Нет отдела"
    )
