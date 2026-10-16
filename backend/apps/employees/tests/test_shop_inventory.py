"""Пополнение магазина использует обычную отгрузку, продажи — свой остаток."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import close_old_connections
from rest_framework.exceptions import ValidationError

from apps.clients.models import Client
from apps.employees.models import ShopReplenishment, ShopStock
from apps.employees.shop import set_employee_shop
from apps.employees.shop_stock import (
    deduct_shop_stock,
    receive_shop_replenishment,
    reconcile_shop_replenishment,
    restore_shop_stock,
)
from apps.orders.models import Order, OrderItem
from apps.shipments.models import Shipment
from apps.shipments.services import dispatch_order, rollback_shipment
from apps.warehouse.models import StockItem
from apps.warehouse.services import receive_stock

pytestmark = pytest.mark.django_db


@pytest.fixture
def shop(boss):
    return set_employee_shop(boss.employee, boss, enabled=True)


@pytest.fixture
def product(boss, make_product):
    product = make_product(name="Мука магазина")
    receive_stock(product, 100, boss)
    return product


def _order(client, product, quantity=10, **fields):
    order = Order.objects.create(client=client, status="confirmed", **fields)
    OrderItem.objects.create(order=order, product=product, quantity=quantity, unit_price="500")
    return order


def _mill(product):
    return StockItem.objects.get(product=product).bags


def _shop(shop, product):
    return ShopStock.objects.filter(shop=shop, product=product).values_list("bags", flat=True).first() or 0


def test_replenishment_only_when_loader_confirms(shop, product, boss):
    order = _order(shop.client, product)
    assert _shop(shop, product) == 0
    assert _mill(product) == 100
    dispatch_order(order, boss)
    assert _mill(product) == 90
    assert _shop(shop, product) == 10
    receipt = ShopReplenishment.objects.get(order=order)
    assert receipt.shop == shop
    assert receipt.items.get().quantity == 10
    with pytest.raises(ValidationError):
        dispatch_order(order, boss)
    receive_shop_replenishment(order, shop, order.items.all())
    assert _mill(product) == 90
    assert _shop(shop, product) == 10


def test_ordinary_order_unchanged(shop, product, boss):
    client = Client.objects.create_with_user(first_name="Обычный клиент")
    order = _order(client, product)
    dispatch_order(order, boss)
    assert _mill(product) == 90
    assert _shop(shop, product) == 0
    assert not ShopReplenishment.objects.exists()


def test_shop_sale_stock_can_go_negative_without_affecting_mill(shop, product):
    restore_shop_stock(shop, [{"product": product, "quantity": 10}])
    deduct_shop_stock(shop, [{"product": product, "quantity": 3}])
    assert _shop(shop, product) == 7
    assert _mill(product) == 100
    deduct_shop_stock(shop, [{"product": product, "quantity": 8}])
    assert _shop(shop, product) == -1
    assert _mill(product) == 100


def test_duplicate_product_lines_are_aggregated(shop, product):
    restore_shop_stock(shop, [{"product": product, "quantity": 10}])
    deduct_shop_stock(shop, [{"product": product, "quantity": 6}] * 2)
    assert _shop(shop, product) == -2


def test_multi_product_deduction_is_atomic(shop, product, make_product):
    other = make_product(name="Другой товар")
    restore_shop_stock(shop, [{"product": product, "quantity": 10}])
    with pytest.raises(ValidationError):
        deduct_shop_stock(shop, [{"product": product, "quantity": 3}, {"product": other, "quantity": -1}])
    assert _shop(shop, product) == 10
    assert not ShopStock.objects.filter(shop=shop, product=other).exists()


def test_rollback_and_reship_balance_both_stocks_once(shop, product, boss):
    order = _order(shop.client, product)
    dispatch_order(order, boss)
    rollback_shipment(order, boss, target_status="confirmed", reason="Повторная отгрузка")
    assert _mill(product) == 100
    assert _shop(shop, product) == 0
    assert ShopReplenishment.objects.get(order=order).reversed_at is not None
    with pytest.raises(ValidationError):
        rollback_shipment(order, boss, target_status="confirmed", reason="Повторная отгрузка")
    dispatch_order(order, boss)
    assert _mill(product) == 90
    assert _shop(shop, product) == 10
    assert ShopReplenishment.objects.count() == 1


def test_rollback_after_goods_sold_records_deficit(shop, product, boss):
    order = _order(shop.client, product)
    dispatch_order(order, boss)
    deduct_shop_stock(shop, [{"product": product, "quantity": 1}])
    rollback_shipment(order, boss, target_status="confirmed", reason="Откат после продажи")
    assert _mill(product) == 100
    assert _shop(shop, product) == -1
    order.refresh_from_db()
    assert order.status == "confirmed"
    assert not Shipment.objects.filter(order=order).exists()
    assert ShopReplenishment.objects.get(order=order).reversed_at is not None


def test_historical_shipment_without_receipt_does_not_change_shop(shop, product, boss):
    order = _order(shop.client, product)
    order.status = "shipped"
    order.save(update_fields=["status"])
    Shipment.objects.create(order=order, stock_deducted=False)
    rollback_shipment(order, boss, target_status="confirmed", reason="Историческая фиксация")
    assert _mill(product) == 100
    assert _shop(shop, product) == 0
    assert not ShopReplenishment.objects.exists()


def test_receipt_correction_and_rollback_use_updated_snapshot(shop, product, boss):
    order = _order(shop.client, product)
    dispatch_order(order, boss)
    reconcile_shop_replenishment(order, [{"product": product, "quantity": 15}])
    assert _shop(shop, product) == 15
    assert ShopReplenishment.objects.get(order=order).items.get().quantity == 15
    deduct_shop_stock(shop, [{"product": product, "quantity": 12}])
    reconcile_shop_replenishment(order, [{"product": product, "quantity": 5}])
    assert _shop(shop, product) == -7
    assert ShopReplenishment.objects.get(order=order).items.get().quantity == 5


@pytest.mark.django_db(transaction=True)
def test_concurrent_stock_deductions_preserve_negative_balance(shop, product):
    restore_shop_stock(shop, [{"product": product, "quantity": 10}])
    barrier = Barrier(2)

    def sell():
        close_old_connections()
        try:
            barrier.wait(timeout=5)
            deduct_shop_stock(shop, [{"product": product, "quantity": 6}])
            return "sold"
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as workers:
        outcomes = list(workers.map(lambda _: sell(), range(2)))
    assert outcomes == ["sold", "sold"]
    assert _shop(shop, product) == -2
    assert _mill(product) == 100
