import pytest

from apps.catalog.models import ClientPrice, Product
from apps.clients.models import Client


pytestmark = pytest.mark.django_db


def test_client_prices_are_available_for_order_creation(auth_client, manager):
    mine = Client.objects.create(
        first_name="Mine", last_name="Client", phone="1")
    foreign = Client.objects.create(
        first_name="Other", last_name="Client", phone="2")
    product = Product.objects.create(
        name="Scoped", color="Red", weight_kg="50", price="100.00")
    ClientPrice.objects.create(client=mine, product=product, price="90.00")
    ClientPrice.objects.create(client=foreign, product=product, price="80.00")

    own = auth_client(manager).get(
        "/api/client-prices/", {"client": mine.id})
    other = auth_client(manager).get(
        "/api/client-prices/", {"client": foreign.id})

    assert own.status_code == 200
    assert own.data == {str(product.id): "90.00"}
    assert other.data == {str(product.id): "80.00"}
