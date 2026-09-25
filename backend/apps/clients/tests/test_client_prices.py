from decimal import Decimal

import pytest

from apps.catalog.models import ClientPrice
from apps.clients.models import Client
from apps.clients.views import ClientViewSet
from apps.sales.models import Department

pytestmark = pytest.mark.django_db


def _client(**kwargs):
    return Client.objects.create_with_user(
        first_name="Личный", last_name="Прайс", phone="1", **kwargs)


def test_authorized_employee_can_attach_and_remove_client_prices(
        auth_client, user_with_perms, make_product):
    user = user_with_perms(
        "price-manager", codes=["clients.view", "clients.set_price"])
    client = _client()
    first = make_product()
    second = make_product("Отруби")

    response = auth_client(user).put(
        f"/api/clients/{client.id}/prices/",
        {"prices": [
            {"product": first.id, "currency": "KZT", "price": "875.50"},
            {"product": first.id, "currency": "USD", "price": "1.95"},
            {"product": second.id, "currency": "KZT", "price": "920.00"},
        ]}, format="json",
    )

    assert response.status_code == 200
    kzt = ClientPrice.objects.get(client=client, product=first, currency="KZT")
    usd = ClientPrice.objects.get(client=client, product=first, currency="USD")
    assert kzt.price == Decimal("875.50")
    assert usd.price == Decimal("1.95")
    assert kzt.updated_by == user
    by_key = {(row["product"], row["currency"]): row
              for row in response.data["prices"]}
    assert by_key[(first.id, "KZT")]["price"] == "875.50"
    assert by_key[(first.id, "USD")]["price"] == "1.95"
    assert by_key[(second.id, "KZT")]["price"] == "920.00"

    removed = auth_client(user).put(
        f"/api/clients/{client.id}/prices/",
        {"prices": [{"product": first.id, "currency": "KZT", "price": None}]}, format="json",
    )
    assert removed.status_code == 200
    assert not ClientPrice.objects.filter(
        client=client, product=first, currency="KZT").exists()
    assert ClientPrice.objects.filter(
        client=client, product=first, currency="USD").exists()
    assert ClientPrice.objects.filter(client=client, product=second).exists()


def test_employee_without_price_permission_cannot_change_prices(
        auth_client, user_with_perms, make_product):
    user = user_with_perms("viewer", codes=["clients.view"])
    client = _client()
    product = make_product()
    response = auth_client(user).put(
        f"/api/clients/{client.id}/prices/",
        {"prices": [{"product": product.id, "price": "900"}]}, format="json",
    )
    assert response.status_code == 403
    assert not ClientPrice.objects.exists()


@pytest.mark.parametrize("price", ["0", "-1", "not-money"])
def test_client_price_must_be_positive(auth_client, user_with_perms, price, make_product):
    user = user_with_perms("price-validator", codes=["clients.set_price"])
    client = _client()
    product = make_product()
    response = auth_client(user).put(
        f"/api/clients/{client.id}/prices/",
        {"prices": [{"product": product.id, "price": price}]}, format="json",
    )
    assert response.status_code == 400
    assert not ClientPrice.objects.exists()


def test_duplicate_product_in_price_list_is_rejected(auth_client, user_with_perms, make_product):
    user = user_with_perms("price-duplicate", codes=["clients.set_price"])
    client = _client()
    product = make_product()
    response = auth_client(user).put(
        f"/api/clients/{client.id}/prices/",
        {"prices": [
            {"product": product.id, "price": "900"},
            {"product": product.id, "price": "800"},
        ]}, format="json",
    )
    assert response.status_code == 400


def test_same_product_in_two_currencies_is_allowed(auth_client, user_with_perms, make_product):
    user = user_with_perms("price-bilingual", codes=["clients.set_price"])
    client = _client()
    product = make_product()

    response = auth_client(user).put(
        f"/api/clients/{client.id}/prices/",
        {"prices": [
            {"product": product.id, "currency": "KZT", "price": "15000"},
            {"product": product.id, "currency": "USD", "price": "31.50"},
        ]}, format="json",
    )

    assert response.status_code == 200
    assert ClientPrice.objects.filter(client=client, product=product).count() == 2


def test_stale_price_write_rechecks_department_after_client_lock(
    auth_client,
    user_with_perms,
    monkeypatch,
    make_product,
):
    first = Department.objects.create(code="price-stale-a", name="Отдел A")
    second = Department.objects.create(code="price-stale-b", name="Отдел B")
    user = user_with_perms(
        "price-stale-writer",
        codes=["clients.view", "clients.set_price"],
        department=first,
    )
    client = _client(department=first)
    stale_client = Client.objects.get(pk=client.pk)
    client.department = second
    client.save(update_fields=["department"])
    product = make_product("Прайс после переноса")
    monkeypatch.setattr(ClientViewSet, "get_object", lambda _view: stale_client)

    response = auth_client(user).put(
        f"/api/clients/{client.pk}/prices/",
        {"prices": [{"product": product.pk, "price": "900"}]},
        format="json",
    )

    assert response.status_code == 403
    assert not ClientPrice.objects.filter(client=client, product=product).exists()
