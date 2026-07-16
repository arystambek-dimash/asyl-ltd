import pytest

from apps.catalog.models import ClientPrice, Product
from apps.clients.models import Client


pytestmark = pytest.mark.django_db


def test_client_prices_respect_department_scope(
        auth_client, dept2_manager, user_with_perms):
    other_manager = user_with_perms(
        "other-city-manager", codes=["dept2.view", "dept2.create"])
    mine = Client.objects.create(
        first_name="Mine", last_name="Client", phone="1",
        department="field", manager=dept2_manager)
    foreign = Client.objects.create(
        first_name="Other", last_name="Client", phone="2",
        department="field", manager=other_manager)
    main = Client.objects.create(
        first_name="Main", last_name="Client", phone="3", department="main")
    product = Product.objects.create(
        name="Scoped", color="Red", weight_kg="50", price="100.00")
    ClientPrice.objects.create(client=mine, product=product, price="90.00")
    ClientPrice.objects.create(client=foreign, product=product, price="80.00")
    ClientPrice.objects.create(client=main, product=product, price="70.00")

    own = auth_client(dept2_manager).get(
        "/api/client-prices/", {"client": mine.id})
    other = auth_client(dept2_manager).get(
        "/api/client-prices/", {"client": foreign.id})
    main_response = auth_client(dept2_manager).get(
        "/api/client-prices/", {"client": main.id})

    assert own.status_code == 200
    assert own.data == {str(product.id): "90.00"}
    assert other.data == {}
    assert main_response.data == {}
