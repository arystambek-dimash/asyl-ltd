import pytest
from apps.catalog.models import Product
from apps.warehouse.models import StockItem
from apps.clients.models import Client, Store
from apps.orders.models import Order

pytestmark = pytest.mark.django_db


def _client_for(user):
    return Client.objects.create(first_name="Мой", last_name="К", phone="x", user=user)


def test_portal_lists_own_stores(auth_client, client_user, make_user):
    c = _client_for(client_user)
    Store.objects.create(client=c, name="Мой магазин")
    other_user = make_user(username="other", client=True)
    other_c = Client.objects.create(first_name="O", last_name="O", phone="y", user=other_user)
    Store.objects.create(client=other_c, name="Чужой")

    r = auth_client(client_user).get("/api/portal/stores/")
    assert r.status_code == 200
    names = [s["name"] for s in r.data]
    assert "Мой магазин" in names
    assert "Чужой" not in names


def test_portal_order_with_own_store(auth_client, client_user):
    c = _client_for(client_user)
    s = Store.objects.create(client=c, name="Мой магазин")
    p = Product.objects.create(name="P", color="Red", weight_kg="50", price="100.00")
    StockItem.objects.create(product=p, bags=500)
    r = auth_client(client_user).post("/api/portal/orders/", {
        "items": [{"product": p.id, "quantity": 1}], "store": s.id,
    }, format="json")
    assert r.status_code == 201
    assert Order.objects.get().store_id == s.id


def test_portal_order_rejects_foreign_store(auth_client, client_user, make_user):
    _client_for(client_user)
    other_user = make_user(username="other", client=True)
    other_c = Client.objects.create(first_name="O", last_name="O", phone="y", user=other_user)
    foreign = Store.objects.create(client=other_c, name="Чужой")
    p = Product.objects.create(name="P", color="Red", weight_kg="50", price="100.00")
    StockItem.objects.create(product=p, bags=500)
    r = auth_client(client_user).post("/api/portal/orders/", {
        "items": [{"product": p.id, "quantity": 1}], "store": foreign.id,
    }, format="json")
    assert r.status_code == 400
