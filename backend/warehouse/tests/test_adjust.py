import pytest
from catalog.models import Grade, Packaging, Product
from warehouse.models import StockItem, StockMovement
from warehouse.services import adjust_stock, receive_stock, deduct_stock
from rest_framework.exceptions import ValidationError

pytestmark = pytest.mark.django_db


def _product():
    g = Grade.objects.create(name="Премиум")
    p = Packaging.objects.create(name="50 кг", weight_kg="50.00")
    return Product.objects.create(grade=g, packaging=p, price="100.00")


def test_adjust_positive_and_negative(boss):
    prod = _product()
    adjust_stock(prod, 200, boss, note="инвентаризация")
    adjust_stock(prod, -30, boss, note="бой мешков")
    assert StockItem.objects.get(product=prod).bags == 170


def test_adjust_cannot_go_negative(boss):
    prod = _product()
    adjust_stock(prod, 10, boss)
    with pytest.raises(ValidationError):
        adjust_stock(prod, -50, boss)


def test_adjust_zero_rejected(boss):
    prod = _product()
    with pytest.raises(ValidationError):
        adjust_stock(prod, 0, boss)


def test_every_change_recorded_as_movement(boss):
    prod = _product()
    adjust_stock(prod, 100, boss, note="старт")
    deduct_stock(prod, 40, boss)
    moves = StockMovement.objects.filter(product=prod).order_by("id")
    assert [m.delta for m in moves] == [100, -40]
    assert [m.balance_after for m in moves] == [100, 60]
    assert moves[0].reason == "adjustment"
    assert moves[1].reason == "shipment"


def test_adjust_endpoint_manager_only(auth_client, operator, manager):
    prod = _product()
    denied = auth_client(operator).post(
        "/api/stock/adjust/", {"product": prod.id, "delta": 50}, format="json"
    )
    assert denied.status_code == 403
    ok = auth_client(manager).post(
        "/api/stock/adjust/", {"product": prod.id, "delta": 50, "note": "приход"},
        format="json",
    )
    assert ok.status_code == 201
    assert ok.data["bags"] == 50


def test_movements_endpoint_lists_history(auth_client, manager):
    prod = _product()
    adjust_stock(prod, 80, manager)
    resp = auth_client(manager).get(f"/api/stock/movements/?product={prod.id}")
    assert resp.status_code == 200
    assert len(resp.data) == 1
    assert resp.data[0]["delta"] == 80
    assert resp.data[0]["balance_after"] == 80
