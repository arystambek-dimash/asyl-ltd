from decimal import Decimal

import pytest

from apps.catalog.models import ClientPrice, Product
from apps.clients.models import Client


pytestmark = pytest.mark.django_db


def _client(**kwargs):
    return Client.objects.create(
        first_name="Личный", last_name="Прайс", phone="1", **kwargs)


def _product(name="Мука"):
    return Product.objects.create(
        name=name, color="Red", weight_kg="50", price="1000.00")


def test_authorized_employee_can_attach_and_remove_client_prices(
        auth_client, user_with_perms):
    user = user_with_perms(
        "price-manager", codes=["clients.view", "clients.set_price"])
    client = _client()
    first = _product()
    second = _product("Отруби")

    response = auth_client(user).put(
        f"/api/clients/{client.id}/prices/",
        {"prices": [
            {"product": first.id, "price": "875.50"},
            {"product": second.id, "price": "920.00"},
        ]}, format="json",
    )

    assert response.status_code == 200
    assert ClientPrice.objects.get(client=client, product=first).price == Decimal("875.50")
    assert ClientPrice.objects.get(client=client, product=first).updated_by == user
    by_product = {row["product"]: row for row in response.data["prices"]}
    assert by_product[first.id]["price"] == "875.50"
    assert by_product[second.id]["price"] == "920.00"

    removed = auth_client(user).put(
        f"/api/clients/{client.id}/prices/",
        {"prices": [{"product": first.id, "price": None}]}, format="json",
    )
    assert removed.status_code == 200
    assert not ClientPrice.objects.filter(client=client, product=first).exists()
    assert ClientPrice.objects.filter(client=client, product=second).exists()


def test_employee_without_price_permission_cannot_change_prices(
        auth_client, user_with_perms):
    user = user_with_perms("viewer", codes=["clients.view"])
    client = _client()
    product = _product()
    response = auth_client(user).put(
        f"/api/clients/{client.id}/prices/",
        {"prices": [{"product": product.id, "price": "900"}]}, format="json",
    )
    assert response.status_code == 403
    assert not ClientPrice.objects.exists()


def test_price_manager_cannot_reach_foreign_department_client(
        auth_client, user_with_perms):
    owner = user_with_perms("owner", codes=["dept2.view"])
    other = user_with_perms(
        "other", codes=["dept2.view", "clients.set_price"])
    client = _client(department="field", manager=owner)
    response = auth_client(other).get(f"/api/clients/{client.id}/prices/")
    assert response.status_code == 404


@pytest.mark.parametrize("price", ["0", "-1", "not-money"])
def test_client_price_must_be_positive(auth_client, user_with_perms, price):
    user = user_with_perms("price-validator", codes=["clients.set_price"])
    client = _client()
    product = _product()
    response = auth_client(user).put(
        f"/api/clients/{client.id}/prices/",
        {"prices": [{"product": product.id, "price": price}]}, format="json",
    )
    assert response.status_code == 400
    assert not ClientPrice.objects.exists()


def test_duplicate_product_in_price_list_is_rejected(auth_client, user_with_perms):
    user = user_with_perms("price-duplicate", codes=["clients.set_price"])
    client = _client()
    product = _product()
    response = auth_client(user).put(
        f"/api/clients/{client.id}/prices/",
        {"prices": [
            {"product": product.id, "price": "900"},
            {"product": product.id, "price": "800"},
        ]}, format="json",
    )
    assert response.status_code == 400
