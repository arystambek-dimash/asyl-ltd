import pytest

from apps.catalog.models import ClientPrice, Product
from apps.clients.models import Client
from apps.sales.models import Department

pytestmark = pytest.mark.django_db


def test_client_prices_are_available_for_order_creation(auth_client, manager):
    mine = Client.objects.create_with_user(
        first_name="Mine", last_name="Client", phone="1")
    foreign = Client.objects.create_with_user(
        first_name="Other", last_name="Client", phone="2")
    product = Product.objects.create(
        name="Scoped", color="Red", weight_kg="50", price="100.00")
    ClientPrice.objects.create(client=mine, product=product, price="90.00")
    ClientPrice.objects.create(
        client=mine, product=product, currency="USD", price="0.20")
    ClientPrice.objects.create(client=foreign, product=product, price="80.00")

    own = auth_client(manager).get(
        "/api/client-prices/", {"client": mine.id})
    other = auth_client(manager).get(
        "/api/client-prices/", {"client": foreign.id})
    usd = auth_client(manager).get(
        "/api/client-prices/", {"client": mine.id, "currency": "USD"})

    assert own.status_code == 200
    assert own.data == {str(product.id): "90.00"}
    assert other.data == {str(product.id): "80.00"}
    assert usd.data == {str(product.id): "0.20"}


def test_client_price_api_rejects_unknown_currency(auth_client, manager):
    response = auth_client(manager).get(
        "/api/client-prices/", {"client": 1, "currency": "EUR"})
    assert response.status_code == 400


def test_client_price_api_rejects_clients_from_another_employee_department(
    auth_client,
    user_with_perms,
):
    first = Department.objects.create(code="price-first", name="Первый")
    second = Department.objects.create(code="price-second", name="Второй")
    employee = user_with_perms(
        "scoped-order-price-reader",
        codes=["orders.create"],
    )
    employee.employee.sales_department = first
    employee.employee.save(update_fields=["sales_department"])
    owned = Client.objects.create_with_user(
        first_name="Свой",
        phone="1",
        department=first,
    )
    foreign = Client.objects.create_with_user(
        first_name="Чужой",
        phone="2",
        department=second,
    )
    product = Product.objects.create(
        name="Scoped price",
        color="Red",
        weight_kg="50",
        price="100.00",
    )
    ClientPrice.objects.create(client=owned, product=product, price="90.00")
    ClientPrice.objects.create(client=foreign, product=product, price="80.00")
    api = auth_client(employee)

    allowed = api.get("/api/client-prices/", {"client": owned.id})
    rejected = api.get("/api/client-prices/", {"client": foreign.id})

    assert allowed.status_code == 200
    assert allowed.data == {str(product.id): "90.00"}
    assert rejected.status_code == 404


@pytest.mark.parametrize("client_id", [None, "", "not-a-number", "0", "-1"])
def test_client_price_api_requires_valid_client(
    auth_client,
    manager,
    client_id,
):
    params = {} if client_id is None else {"client": client_id}

    response = auth_client(manager).get("/api/client-prices/", params)

    assert response.status_code == 400
    assert "client" in response.data["detail"]


@pytest.mark.parametrize("method", ["head", "options"])
def test_client_price_head_and_options_use_get_permissions(
    auth_client,
    user_with_perms,
    method,
):
    editor = user_with_perms("prices-editor", codes=["orders.edit"])
    api = auth_client(editor)

    response = getattr(api, method)("/api/client-prices/?client=1")

    assert response.status_code == 200


@pytest.mark.parametrize("method", ["head", "options"])
def test_client_price_head_and_options_do_not_bypass_get_permissions(
    auth_client,
    make_user,
    method,
):
    api = auth_client(make_user(username="no-order-permissions"))

    response = getattr(api, method)("/api/client-prices/?client=1")

    assert response.status_code == 403
