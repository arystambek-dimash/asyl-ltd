import pytest
from apps.warehouse.models import StockItem, StockMovement, Warehouse
from apps.warehouse.services import adjust_stock, deduct_stock
from rest_framework.exceptions import ValidationError

pytestmark = pytest.mark.django_db


def test_adjust_positive_and_negative(boss, make_product):
    prod = make_product()
    adjust_stock(prod, 200, boss, note="инвентаризация")
    adjust_stock(prod, -30, boss, note="бой мешков")
    assert StockItem.objects.get(product=prod).bags == 170


def test_adjust_cannot_go_negative(boss, make_product):
    prod = make_product()
    adjust_stock(prod, 10, boss)
    with pytest.raises(ValidationError):
        adjust_stock(prod, -50, boss)


def test_positive_adjustment_partially_restores_overdrawn_stock(boss, make_product):
    prod = make_product()
    warehouse = Warehouse.objects.get(code="main")
    stock = StockItem.objects.create(product=prod, warehouse=warehouse, bags=-100)
    adjust_stock(prod, 10, boss, warehouse=warehouse)
    stock.refresh_from_db()
    assert stock.bags == -90
    with pytest.raises(ValidationError):
        adjust_stock(prod, -1, boss, warehouse=warehouse)


def test_adjust_zero_rejected(boss, make_product):
    prod = make_product()
    with pytest.raises(ValidationError):
        adjust_stock(prod, 0, boss)


def test_every_change_recorded_as_movement(boss, make_product):
    prod = make_product()
    adjust_stock(prod, 100, boss, note="старт")
    deduct_stock(prod, 40, boss)
    moves = StockMovement.objects.filter(product=prod).order_by("id")
    assert [m.delta for m in moves] == [100, -40]
    assert [m.balance_after for m in moves] == [100, 60]
    assert moves[0].reason == "adjustment"
    assert moves[1].reason == "shipment"


def test_adjust_endpoint_manager_only(auth_client, operator, boss, make_product):
    prod = make_product()
    denied = auth_client(operator).post(
        "/api/stock/adjust/", {"product": prod.id, "delta": 50}, format="json"
    )
    assert denied.status_code == 403
    ok = auth_client(boss).post(
        "/api/stock/adjust/", {"product": prod.id, "delta": 50, "note": "приход"},
        format="json",
    )
    assert ok.status_code == 200
    assert ok.data["bags"] == 50
    assert ok.data["packaging"] == "50 кг"


def test_adjust_endpoint_rejects_unknown_product(auth_client, boss):
    for product in (999999, "abc"):
        response = auth_client(boss).post(
            "/api/stock/adjust/", {"product": product, "delta": 5}, format="json"
        )
        assert response.status_code == 400
        assert response.data["detail"]["product"] == ["Товар не найден"]

