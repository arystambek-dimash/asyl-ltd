import pytest
from rest_framework.exceptions import ValidationError

from apps.warehouse.models import StockItem, StockMovement, StockReceipt, Warehouse
from apps.warehouse.services import return_stock

pytestmark = pytest.mark.django_db


def test_returned_bags_come_back_as_client_return_not_receipt(boss, make_product):
    flour = make_product(name="Д1с")
    main = Warehouse.objects.get(code="main")

    return_stock(flour, 30, boss, main, note="Возврат товара №1")

    assert StockItem.objects.get(product=flour, warehouse=main).bags == 30
    movement = StockMovement.objects.get(product=flour)
    assert (movement.delta, movement.reason, movement.note) == (30, "client_return", "Возврат товара №1")
    assert movement.get_reason_display() == "Возврат от клиента"
    assert not StockReceipt.objects.exists()  # не производство


def test_return_needs_positive_bags(boss, make_product):
    with pytest.raises(ValidationError):
        return_stock(make_product(name="Д1с"), 0, boss, Warehouse.objects.get(code="main"))
