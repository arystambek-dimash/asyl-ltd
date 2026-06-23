import pytest
from apps.catalog.models import Product
from apps.warehouse.models import StockItem
from apps.warehouse.services import receive_stock, deduct_stock
from rest_framework.exceptions import ValidationError

pytestmark = pytest.mark.django_db


def _product():
    return Product.objects.create(name="Премиум", color="Red", weight_kg="50", price="100.00")


def test_receive_stock_increments(boss):
    prod = _product()
    receive_stock(prod, 100, boss)
    receive_stock(prod, 50, boss)
    assert StockItem.objects.get(product=prod).bags == 150


def test_deduct_stock_reduces(boss):
    prod = _product()
    receive_stock(prod, 100, boss)
    deduct_stock(prod, 30)
    assert StockItem.objects.get(product=prod).bags == 70


def test_deduct_more_than_available_raises(boss):
    prod = _product()
    receive_stock(prod, 10, boss)
    with pytest.raises(ValidationError):
        deduct_stock(prod, 50)


def test_receipt_endpoint_manager_only(auth_client, operator):
    prod = _product()
    resp = auth_client(operator).post(
        "/api/stock/receive/", {"product": prod.id, "bags": 10}, format="json"
    )
    assert resp.status_code == 403
