import pytest
from rest_framework.test import APIClient
from apps.clients.models import Client, Store

pytestmark = pytest.mark.django_db


def _api(user):
    c = APIClient(); c.force_authenticate(user); return c


def test_client_analytics_requires_reports_view(user_with_perms):
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    viewer = user_with_perms("cv", codes=["clients.view"])
    reporter = user_with_perms("rv", codes=["clients.view", "reports.view"])
    assert _api(viewer).get(f"/api/clients/{c.id}/analytics/").status_code == 403
    assert _api(reporter).get(f"/api/clients/{c.id}/analytics/").status_code == 200


def test_client_debts_requires_reports_view(user_with_perms):
    viewer = user_with_perms("cv2", codes=["clients.view"])
    reporter = user_with_perms("rv2", codes=["reports.view"])
    assert _api(viewer).get("/api/clients/debts/").status_code == 403
    assert _api(reporter).get("/api/clients/debts/").status_code == 200


def test_store_debts_requires_reports_view(user_with_perms):
    viewer = user_with_perms("cv3", codes=["clients.view"])
    reporter = user_with_perms("rv3", codes=["reports.view"])
    assert _api(viewer).get("/api/stores/debts/").status_code == 403
    assert _api(reporter).get("/api/stores/debts/").status_code == 200


def test_orders_debts_requires_reports_view(user_with_perms):
    viewer = user_with_perms("ov", codes=["orders.view"])
    reporter = user_with_perms("rv4", codes=["reports.view"])
    assert _api(viewer).get("/api/orders/debts/").status_code == 403
    assert _api(reporter).get("/api/orders/debts/").status_code == 200


def test_check_overdue_requires_clients_edit(user_with_perms):
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    Store.objects.create(client=c, name="S", payment_schedule_type="none")
    viewer = user_with_perms("cv5", codes=["clients.view"])
    editor = user_with_perms("ce", codes=["clients.edit"])
    assert _api(viewer).post("/api/stores/check-overdue/").status_code == 403
    assert _api(editor).post("/api/stores/check-overdue/").status_code == 200
