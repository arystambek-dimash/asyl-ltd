import pytest
from unittest.mock import patch
from apps.catalog.models import Product
from apps.clients.models import Client, Store
from apps.clients.serializers import ClientReadSerializer
from apps.orders.models import Order, OrderItem

pytestmark = pytest.mark.django_db


def test_client_history_requires_reports_view(user_with_perms, api_as):
    c = Client.objects.create_with_user(first_name="A", last_name="B", phone="x")
    viewer = user_with_perms("cv", codes=["clients.view"])
    reporter = user_with_perms("rv", codes=["clients.view", "reports.view"])
    assert api_as(viewer).get(f"/api/clients/{c.id}/history/").status_code == 403
    assert api_as(reporter).get(f"/api/clients/{c.id}/history/").status_code == 200


def test_client_debts_allow_reports_or_payment_entry(user_with_perms, api_as):
    viewer = user_with_perms("cv2", codes=["clients.view"])
    reporter = user_with_perms("rv2", codes=["reports.view"])
    recorder = user_with_perms("pay2", codes=["payments.create"])
    assert api_as(viewer).get("/api/clients/debts/").status_code == 403
    assert api_as(reporter).get("/api/clients/debts/").status_code == 200
    assert api_as(recorder).get("/api/clients/debts/").status_code == 200


def test_client_list_hides_debt_without_reports_view(user_with_perms, api_as):
    client = Client.objects.create_with_user(first_name="A", last_name="B", phone="x")
    product = Product.objects.create(
        name="P", color="Red", weight_kg="50"
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

    hidden = api_as(viewer).get("/api/clients/")
    visible = api_as(reporter).get("/api/clients/")

    assert hidden.status_code == visible.status_code == 200
    assert set(ClientReadSerializer.FINANCIAL_FIELDS).isdisjoint(hidden.data[0])
    assert visible.data[0]["debt_total"] == "200.00"


def test_client_prices_hides_debt_without_reports_view(user_with_perms, api_as):
    client = Client.objects.create_with_user(first_name="A", last_name="B", phone="x")
    price_manager = user_with_perms(
        "price-no-reports",
        codes=["clients.view", "clients.set_price"],
    )

    response = api_as(price_manager).get(f"/api/clients/{client.id}/prices/")

    assert response.status_code == 200
    assert response.data["client"] == {"id": client.id, "name": client.name}


def test_payment_recorder_gets_minimal_client_debt_detail(user_with_perms, api_as):
    client = Client.objects.create_with_user(
        first_name="A", last_name="B", phone="87000000000",
        bank="Test bank", bank_account="KZ00TESTACCOUNT",
    )
    product = Product.objects.create(
        name="Debt item", color="Red", weight_kg="50",
    )
    order = Order.objects.create(
        client=client, status="shipped", settlement_intent="debt",
    )
    OrderItem.objects.create(
        order=order, product=product, quantity=1, unit_price="100.00",
    )
    recorder = user_with_perms("pay-detail", codes=["payments.create"])

    response = api_as(recorder).get(f"/api/clients/{client.id}/debt-detail/")

    assert response.status_code == 200
    assert response.data["client"] == {
        "id": client.id,
        "name": client.name,
        "phone": client.phone,
        "currency": client.currency,
    }
    assert response.data["debt_total"] == "100.00"
    assert response.data["overdue_by_currency"] == {}
    assert response.data["orders"][0]["id"] == order.id
    assert api_as(recorder).get(f"/api/clients/{client.id}/history/").status_code == 403


def test_check_overdue_requires_stores_edit(user_with_perms, api_as):
    c = Client.objects.create_with_user(first_name="A", last_name="B", phone="x")
    Store.objects.create(client=c, name="S", payment_schedule_type="none")
    viewer = user_with_perms("cv5", codes=["stores.view", "clients.edit"])
    editor = user_with_perms("ce", codes=["stores.edit"])
    assert api_as(viewer).post("/api/stores/check-overdue/").status_code == 403
    assert api_as(editor).post("/api/stores/check-overdue/").status_code == 200


def test_check_overdue_checks_all_clients(user_with_perms, api_as):
    main = Client.objects.create_with_user(first_name="Main", last_name="Client", phone="1")
    field = Client.objects.create_with_user(first_name="Field", last_name="Client", phone="2")
    main_store = Store.objects.create(
        client=main, name="Main store", payment_schedule_type="weekly", payment_days=[1]
    )
    Store.objects.create(
        client=field,
        name="Field store",
        payment_schedule_type="weekly",
        payment_days=[1],
    )
    editor = user_with_perms("scoped-editor", codes=["stores.edit"])

    with patch("apps.clients.views.detect_overdue", return_value=0) as detect:
        response = api_as(editor).post("/api/stores/check-overdue/")

    assert response.status_code == 200
    assert response.data["checked"] == 2
    assert {call.args[0].id for call in detect.call_args_list} == {
        main_store.id,
        field.stores.get().id,
    }
