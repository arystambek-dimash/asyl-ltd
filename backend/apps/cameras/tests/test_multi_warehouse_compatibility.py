import pytest

from apps.cameras import production
from apps.catalog.models import Product
from apps.warehouse.models import StockItem, Warehouse
from apps.warehouse.services import get_default_warehouse

pytestmark = pytest.mark.django_db


def test_production_payload_exposes_every_stock_card_for_product():
    main = get_default_warehouse()
    secondary = Warehouse.objects.create(
        code="second",
        name="Мельница 2",
        is_active=False,
    )
    product = Product.objects.create(
        name="Товар двух складов",
        color="Red",
        weight_kg="50",
        price="100.00",
    )
    StockItem.objects.create(product=product, warehouse=main, bags=12)
    StockItem.objects.create(product=product, warehouse=secondary, bags=0)

    payload = production.production_payload("cam3")

    product_row = next(row for row in payload["products"] if row["id"] == product.pk)
    assert product_row["warehouse"] == main.pk
    assert product_row["warehouse_ids"] == [main.pk, secondary.pk]
