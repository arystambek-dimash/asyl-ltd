from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import close_old_connections, connection, connections
from rest_framework.exceptions import ValidationError

from apps.catalog.models import Product
from apps.warehouse import services
from apps.warehouse.models import StockItem, Warehouse
from apps.warehouse.services import deduct_stock, get_default_warehouse, receive_stock

pytestmark = pytest.mark.django_db


def test_phase_a_operates_on_existing_exact_rows_after_phase_b_rollback(
    boss,
    monkeypatch,
):
    monkeypatch.setattr(
        services,
        "MULTI_WAREHOUSE_STOCK_WRITES_ENABLED",
        False,
    )
    main = get_default_warehouse()
    secondary = Warehouse.objects.create(code="second", name="Мельница 2")
    product = Product.objects.create(
        name="Товар двух складов",
        color="Red",
        weight_kg="50",
        price="100.00",
    )
    StockItem.objects.create(product=product, warehouse=main, bags=12)
    StockItem.objects.create(product=product, warehouse=secondary, bags=4)

    receive_stock(product, 3, boss, warehouse=secondary)
    deduct_stock(product, 2, boss, warehouse=main)

    assert StockItem.objects.get(product=product, warehouse=main).bags == 10
    assert StockItem.objects.get(product=product, warehouse=secondary).bags == 7


@pytest.mark.django_db(transaction=True)
def test_phase_a_concurrent_first_assignment_creates_only_one_stock_card(
    boss,
    monkeypatch,
):
    if connection.vendor != "postgresql":
        pytest.skip("row-lock contract requires PostgreSQL")

    monkeypatch.setattr(
        services,
        "MULTI_WAREHOUSE_STOCK_WRITES_ENABLED",
        False,
    )

    main = get_default_warehouse()
    secondary = Warehouse.objects.create(code="second", name="Мельница 2")
    product = Product.objects.create(
        name="Конкурентная привязка",
        color="Blue",
        weight_kg="50",
        price="100.00",
    )
    start = Barrier(2)

    def assign(warehouse_id):
        close_old_connections()
        try:
            start.wait(timeout=5)
            receive_stock(
                Product.objects.get(pk=product.pk),
                1,
                type(boss).objects.get(pk=boss.pk),
                warehouse=warehouse_id,
            )
            return "ok"
        except ValidationError as exc:
            return str(exc.detail["code"])
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(assign, [main.pk, secondary.pk]))

    assert sorted(results) == ["ok", "product_in_other_warehouse"]
    assert StockItem.objects.filter(product=product, bags=1).count() == 1
