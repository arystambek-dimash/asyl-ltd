from decimal import Decimal

import pytest

from apps.employees.models import ShopReplenishment, ShopStock
from apps.employees.shop import set_employee_shop
from apps.employees.shop_stock import deduct_shop_stock
from apps.orders.models import Order, OrderItem
from apps.orders.services import replace_items
from apps.shipments.services import dispatch_order, rollback_shipment
from apps.warehouse.models import StockItem
from apps.warehouse.services import receive_stock

pytestmark = pytest.mark.django_db


@pytest.fixture
def replenishment(user_with_perms, boss, make_product):
    owner = user_with_perms("shop-correction-owner", codes=["orders.create"])
    shop = set_employee_shop(owner.employee, boss, enabled=True)
    product = make_product(name="Shop correction flour")
    receive_stock(product, 100, boss)
    order = Order.objects.create(client=shop.client, status="confirmed", warehouse=shop.warehouse)
    OrderItem.objects.create(order=order, product=product, quantity=10, unit_price=Decimal("100"))
    dispatch_order(order, boss)
    return shop, product, order


def test_shipped_replenishment_correction_moves_both_stocks_and_rollback_uses_corrected_receipt(
    replenishment, boss,
):
    shop, product, order = replenishment
    replace_items(
        order, [{"product": product, "quantity": 15}], {str(product.pk): Decimal("100")},
        boss, edit_reason="Исправлено количество пополнения",
    )
    assert StockItem.objects.get(product=product, warehouse=shop.warehouse).bags == 85
    assert ShopStock.objects.get(shop=shop, product=product).bags == 15
    assert ShopReplenishment.objects.get(order=order).items.get(product=product).quantity == 15

    rollback_shipment(order, boss, target_status="confirmed", reason="Отмена исправленного пополнения")
    assert StockItem.objects.get(product=product, warehouse=shop.warehouse).bags == 100
    assert ShopStock.objects.get(shop=shop, product=product).bags == 0


def test_correction_after_shop_sale_moves_both_stocks_and_records_deficit(replenishment, boss):
    shop, product, order = replenishment
    deduct_shop_stock(shop, [{"product": product, "quantity": 7}])
    replace_items(
        order, [{"product": product, "quantity": 2}], {str(product.pk): Decimal("100")},
        boss, edit_reason="Уменьшение количества пополнения",
    )
    assert order.items.get(product=product).quantity == 2
    assert StockItem.objects.get(product=product, warehouse=shop.warehouse).bags == 98
    assert ShopStock.objects.get(shop=shop, product=product).bags == -5
    assert ShopReplenishment.objects.get(order=order).items.get(product=product).quantity == 2


def test_correction_replaces_product_in_both_stocks(replenishment, boss, make_product):
    shop, product, order = replenishment
    replacement = make_product(name="Replacement shop flour")
    receive_stock(replacement, 20, boss)
    replace_items(
        order, [{"product": replacement, "quantity": 5}], {str(replacement.pk): Decimal("200")},
        boss, edit_reason="В пополнении указан другой товар",
    )
    assert StockItem.objects.get(product=product, warehouse=shop.warehouse).bags == 100
    assert StockItem.objects.get(product=replacement, warehouse=shop.warehouse).bags == 15
    assert ShopStock.objects.get(shop=shop, product=product).bags == 0
    assert ShopStock.objects.get(shop=shop, product=replacement).bags == 5
