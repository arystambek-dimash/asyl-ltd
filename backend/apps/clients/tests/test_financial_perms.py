import pytest
from unittest.mock import patch
from rest_framework.test import APIClient
from apps.catalog.models import Product
from apps.clients.models import Client, Store
from apps.clients.serializers import ClientSerializer
from apps.orders.models import Order, OrderItem

pytestmark = pytest.mark.django_db


def _api(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def test_client_history_requires_reports_view(user_with_perms):
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    viewer = user_with_perms("cv", codes=["clients.view"])
    reporter = user_with_perms("rv", codes=["clients.view", "reports.view"])
    assert _api(viewer).get(f"/api/clients/{c.id}/history/").status_code == 403
    assert _api(reporter).get(f"/api/clients/{c.id}/history/").status_code == 200


def test_client_debts_requires_reports_view(user_with_perms):
    viewer = user_with_perms("cv2", codes=["clients.view"])
    reporter = user_with_perms("rv2", codes=["reports.view"])
    assert _api(viewer).get("/api/clients/debts/").status_code == 403
    assert _api(reporter).get("/api/clients/debts/").status_code == 200


def test_client_list_hides_debt_without_reports_view(user_with_perms):
    client = Client.objects.create(first_name="A", last_name="B", phone="x")
    product = Product.objects.create(
        name="P", color="Red", weight_kg="50", price="100.00"
    )
    order = Order.objects.create(
        client=client,
        status="shipped",
        settlement_intent="debt",
    )
    OrderItem.objects.create(
        order=order,
        product=product,
        quantity=2,
        unit_price="100.00",
    )
    viewer = user_with_perms("client-only", codes=["clients.view"])
    reporter = user_with_perms(
        "client-reporter", codes=["clients.view", "reports.view"]
    )

    hidden = _api(viewer).get("/api/clients/")
    visible = _api(reporter).get("/api/clients/")

    assert hidden.status_code == visible.status_code == 200
    assert set(ClientSerializer.FINANCIAL_FIELDS).isdisjoint(hidden.data[0])
    assert visible.data[0]["debt_total"] == "200.00"


def test_client_prices_hides_debt_without_reports_view(user_with_perms):
    client = Client.objects.create(first_name="A", last_name="B", phone="x")
    price_manager = user_with_perms(
        "price-no-reports",
        codes=["clients.view", "clients.set_price"],
    )

    response = _api(price_manager).get(f"/api/clients/{client.id}/prices/")

    assert response.status_code == 200
    assert response.data["client"] == {"id": client.id, "name": client.name}


def test_store_debts_requires_reports_view(user_with_perms):
    viewer = user_with_perms("cv3", codes=["clients.view"])
    reporter = user_with_perms("rv3", codes=["reports.view"])
    assert _api(viewer).get("/api/stores/debts/").status_code == 403
    assert _api(reporter).get("/api/stores/debts/").status_code == 200


def test_check_overdue_requires_clients_edit(user_with_perms):
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    Store.objects.create(client=c, name="S", payment_schedule_type="none")
    viewer = user_with_perms("cv5", codes=["clients.view"])
    editor = user_with_perms("ce", codes=["clients.edit"])
    assert _api(viewer).post("/api/stores/check-overdue/").status_code == 403
    assert _api(editor).post("/api/stores/check-overdue/").status_code == 200


def test_check_overdue_checks_all_clients(user_with_perms):
    main = Client.objects.create(first_name="Main", last_name="Client", phone="1")
    field = Client.objects.create(first_name="Field", last_name="Client", phone="2")
    main_store = Store.objects.create(
        client=main, name="Main store", payment_schedule_type="weekly", payment_days=[1]
    )
    Store.objects.create(
        client=field,
        name="Field store",
        payment_schedule_type="weekly",
        payment_days=[1],
    )
    editor = user_with_perms("scoped-editor", codes=["clients.edit"])

    with patch("apps.clients.views.detect_overdue", return_value=0) as detect:
        response = _api(editor).post("/api/stores/check-overdue/")

    assert response.status_code == 200
    assert response.data["checked"] == 2
    assert {call.args[0].id for call in detect.call_args_list} == {
        main_store.id,
        field.stores.get().id,
    }
