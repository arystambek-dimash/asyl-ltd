"""Сквозная изоляция заказов мельницы и конкурентных продаж магазина."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from django.db import close_old_connections

from apps.catalog.models import ClientPrice
from apps.clients.models import Client
from apps.employees.models import ShopReplenishment, ShopSale, ShopStock
from apps.employees.shop import set_employee_shop
from apps.orders.models import Order
from apps.shipments.models import Shipment
from apps.warehouse.models import StockItem
from apps.warehouse.services import receive_stock

pytestmark = pytest.mark.django_db


@pytest.fixture
def workflow(user_with_perms, departments, make_product, boss):
    owner = user_with_perms(
        "shop-qa-owner", department=departments[0],
        codes=["orders.create", "orders.view", "orders.confirm", "clients.view", "reports.view"],
    )
    loader = user_with_perms(
        "shop-qa-loader", department=departments[0],
        codes=["loader.view", "loader.confirm", "loader.trucks"],
    )
    shop = set_employee_shop(owner.employee, boss, enabled=True)
    product = make_product(name="Сквозная мука")
    receive_stock(product, 100, boss)
    ClientPrice.objects.create(client=shop.client, product=product, currency=shop.client.currency, price="500")
    return owner, loader, shop, product


def _sale(api, product, quantity=3, key=None):
    return api.post("/api/shop-orders/", {
        "idempotency_key": str(key or uuid4()),
        "items": [{"product": product.pk, "quantity": quantity}],
    }, format="json")


def _replenish(api, shop, product, quantity=10):
    response = api.post("/api/orders/", {
        "client": shop.client_id,
        "warehouse": shop.warehouse_id,
        "items": [{"product": product.pk, "quantity": quantity}],
        "prices": {str(product.pk): "500"},
    }, format="json")
    assert response.status_code == 201, response.data
    assert response.data["status"] == "confirmed", response.data
    return response.data["id"]


def _dispatch(api, order_id):
    return api.post(f"/api/loader/orders/{order_id}/dispatch/", {"truck_number": "403BJN13"}, format="json")


def _balances(shop, product):
    return (
        StockItem.objects.get(warehouse=shop.warehouse, product=product).bags,
        ShopStock.objects.filter(shop=shop, product=product).values_list("bags", flat=True).first() or 0,
    )


def test_standard_order_to_loader_to_shop_sale_and_rollback(auth_client, workflow):
    owner, loader, shop, product = workflow
    api, loading = auth_client(owner), auth_client(loader)
    order_id = _replenish(api, shop, product)
    ordinary = Client.objects.create_with_user(first_name="Обычный покупатель", department=shop.client.department)
    ordinary_response = api.post("/api/orders/", {
        "client": ordinary.pk, "items": [{"product": product.pk, "quantity": 2}],
        "prices": {str(product.pk): "500"},
    }, format="json")
    assert ordinary_response.status_code == 201, ordinary_response.data
    ordinary_id = ordinary_response.data["id"]
    assert {row["id"] for row in loading.get("/api/loader/queue/").data} == {order_id, ordinary_id}
    assert _balances(shop, product) == (100, 0)
    assert _dispatch(loading, ordinary_id).status_code == 200
    assert _balances(shop, product) == (98, 0)
    assert _dispatch(loading, order_id).status_code == 200
    assert _balances(shop, product) == (88, 10)
    assert _dispatch(loading, order_id).status_code == 400
    assert _balances(shop, product) == (88, 10)
    assert ShopReplenishment.objects.get(order_id=order_id).items.get().quantity == 10
    report_before = api.get("/api/reports/summary/")
    assert report_before.status_code == 200
    sale = _sale(api, product)
    assert sale.status_code == 201, sale.data
    assert _balances(shop, product) == (88, 7)
    assert Order.objects.count() == Shipment.objects.count() == 2
    assert {row["id"] for row in api.get("/api/orders/").data} == {order_id, ordinary_id}
    assert loading.get("/api/loader/queue/").data == []
    assert {row["id"] for row in loading.get("/api/loader/history/").data} == {order_id, ordinary_id}
    assert set(loading.get("/api/loader/truck-report/").data["order_ids"]) == {order_id, ordinary_id}
    assert api.get("/api/reports/summary/").data == report_before.data
    cancel = f"/api/shop-orders/{sale.data['id']}/cancel/"
    assert api.post(cancel).status_code == api.post(cancel).status_code == 200
    assert _balances(shop, product) == (88, 10)
    rollback = f"/api/loader/orders/{order_id}/rollback/"
    assert loading.post(rollback).status_code == 200
    assert _balances(shop, product) == (98, 0)
    assert loading.post(rollback).status_code == 400
    assert _dispatch(loading, order_id).status_code == 200
    assert _balances(shop, product) == (88, 10)
    assert ShopReplenishment.objects.filter(order_id=order_id).count() == 1


def _concurrent(calls):
    barrier = Barrier(len(calls))

    def run(call):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            response = call()
            return response.status_code, response.data
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=len(calls)) as workers:
        return list(workers.map(run, calls))


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("same_key", [True, False])
def test_concurrent_sale_requests_are_idempotent_and_allow_negative_stock(auth_client, workflow, same_key):
    owner, _, shop, product = workflow
    ShopStock.objects.create(shop=shop, product=product, bags=10)
    key = uuid4()
    calls = [lambda: _sale(auth_client(owner), product, quantity=6, key=key)]
    calls.append(lambda: _sale(auth_client(owner), product, quantity=6, key=key if same_key else uuid4()))
    outcomes = _concurrent(calls)
    assert [status for status, _ in outcomes] == [201, 201]
    assert ShopSale.objects.count() == (1 if same_key else 2)
    assert _balances(shop, product) == (100, 4 if same_key else -2)
    if same_key:
        assert outcomes[0][1] == outcomes[1][1]


@pytest.mark.django_db(transaction=True)
def test_sale_and_replenishment_concurrently_preserve_both_stocks(auth_client, workflow):
    owner, loader, shop, product = workflow
    ShopStock.objects.create(shop=shop, product=product, bags=5)
    order_id = _replenish(auth_client(owner), shop, product, quantity=10)
    outcomes = _concurrent([
        lambda: _sale(auth_client(owner), product, quantity=4),
        lambda: _dispatch(auth_client(loader), order_id),
    ])
    assert sorted(status for status, _ in outcomes) == [200, 201]
    assert _balances(shop, product) == (90, 11)
    assert ShopSale.objects.count() == ShopReplenishment.objects.count() == Shipment.objects.count() == 1


def test_replenishment_offsets_negative_sale_balance_and_cancel_restores_once(auth_client, workflow):
    owner, loader, shop, product = workflow
    api = auth_client(owner)
    sale = _sale(api, product, quantity=12)
    assert sale.status_code == 201, sale.data
    assert _balances(shop, product) == (100, -12)
    order_id = _replenish(api, shop, product, quantity=10)
    assert _dispatch(auth_client(loader), order_id).status_code == 200
    assert _balances(shop, product) == (90, -2)
    assert _dispatch(auth_client(loader), order_id).status_code == 400
    assert _balances(shop, product) == (90, -2)
    cancel = f"/api/shop-orders/{sale.data['id']}/cancel/"
    assert api.post(cancel).status_code == api.post(cancel).status_code == 200
    assert _balances(shop, product) == (90, 10)
