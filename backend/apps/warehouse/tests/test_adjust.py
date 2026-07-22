import pytest
from apps.catalog.models import Product
from apps.warehouse.models import StockItem, StockMovement
from apps.warehouse.services import adjust_stock, deduct_stock
from rest_framework.exceptions import ValidationError

pytestmark = pytest.mark.django_db


def _product():
    return Product.objects.create(name="Премиум", color="Red", weight_kg="50", price="100.00")


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


def test_adjust_endpoint_manager_only(auth_client, operator, boss):
    prod = _product()
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


def test_movements_endpoint_lists_history(auth_client, boss):
    prod = _product()
    adjust_stock(prod, 80, boss)
    resp = auth_client(boss).get(f"/api/stock/movements/?product={prod.id}")
    assert resp.status_code == 200
    assert len(resp.data) == 1
    assert resp.data[0]["delta"] == 80
    assert resp.data[0]["balance_after"] == 80


def test_delete_stock_item_resets_balance_and_keeps_history(auth_client, boss):
    prod = _product()
    item = adjust_stock(prod, 80, boss)

    resp = auth_client(boss).delete(f"/api/stock/{item.id}/")

    assert resp.status_code == 204
    assert not StockItem.objects.filter(pk=item.id).exists()
    removal = StockMovement.objects.filter(product=prod).first()
    assert removal.delta == -80
    assert removal.balance_after == 0
    assert removal.note == "Удаление из складского списка"


def test_delete_stock_item_manager_only(auth_client, operator, boss):
    prod = _product()
    item = adjust_stock(prod, 10, boss)

    resp = auth_client(operator).delete(f"/api/stock/{item.id}/")

    assert resp.status_code == 403
    assert StockItem.objects.filter(pk=item.id).exists()
