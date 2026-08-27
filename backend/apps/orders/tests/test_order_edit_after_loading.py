"""Safe order-content corrections after the physical loading workflow."""

from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.cameras.models import AiCountingSession
from apps.catalog.models import Product
from apps.clients.models import Client
from apps.eventlog.models import EventLog
from apps.orders.models import Order, OrderItem, Payment
from apps.shipments.models import Shipment
from apps.warehouse.models import StockItem, StockMovement

pytestmark = pytest.mark.django_db

_sequence = [0]


def _product(*, stock=100):
    _sequence[0] += 1
    product = Product.objects.create(
        name=f"Safe edit product {_sequence[0]}",
        color="Red",
        weight_kg="50",
        price="100.00",
    )
    if stock is not None:
        StockItem.objects.create(product=product, bags=stock)
    return product


def _order(*, status, rows):
    client = Client.objects.create_with_user(
        first_name="Safe",
        last_name="Edit",
        phone=f"safe-edit-{_sequence[0]}",
    )
    order = Order.objects.create(client=client, status=status)
    for product, quantity, price in rows:
        OrderItem.objects.create(
            order=order,
            product=product,
            quantity=quantity,
            unit_price=price,
        )
    return order


def _api(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


def _patch_items(user, order, rows, *, reason=None):
    payload = {
        "items": [
            {"product": product.id, "quantity": quantity}
            for product, quantity, _price in rows
        ],
        "prices": {
            str(product.id): price
            for product, _quantity, price in rows
        },
    }
    if reason is not None:
        payload["edit_reason"] = reason
    return _api(user).patch(
        f"/api/orders/{order.id}/",
        payload,
        format="json",
    )


def test_shipped_correction_reconciles_product_net_deltas_and_audits(manager):
    first = _product(stock=90)
    removed = _product(stock=46)
    added = _product(stock=30)
    order = _order(
        status="shipped",
        rows=[
            (first, 10, "100.00"),
            (removed, 4, "100.00"),
        ],
    )
    Shipment.objects.create(order=order, bags_loaded=14)

    response = _patch_items(
        manager,
        order,
        [(first, 6, "100.00"), (added, 7, "100.00")],
        reason="Исправлена накладная",
    )

    assert response.status_code == 200, response.data
    assert "edit_reason" not in response.data
    assert StockItem.objects.get(product=first).bags == 94
    assert StockItem.objects.get(product=removed).bags == 50
    assert StockItem.objects.get(product=added).bags == 23
    assert set(
        StockMovement.objects.filter(reason="shipment_correction")
        .values_list("product_id", "delta")
    ) == {
        (first.id, 4),
        (removed.id, 4),
        (added.id, -7),
    }
    event = EventLog.objects.get(order=order, event_type="order_edit")
    assert event.payload["action"] == "shipment_correction"
    assert event.payload["reason"] == "Исправлена накладная"
    assert event.payload["shipment_bags_loaded"] == 14
    assert {row["product"]: row["delta"] for row in event.payload["stock_changes"]} == {
        first.id: 4,
        removed.id: 4,
        added.id: -7,
    }
    # The physical counter snapshot is evidence, not an editable order total.
    assert Shipment.objects.get(order=order).bags_loaded == 14


def test_shipped_correction_requires_reason_and_is_atomic(manager):
    product = _product(stock=90)
    order = _order(
        status="shipped",
        rows=[(product, 10, "100.00")],
    )

    response = _patch_items(
        manager,
        order,
        [(product, 5, "100.00")],
    )

    assert response.status_code == 400
    assert response.data["code"] == "edit_reason_required"
    assert order.items.get().quantity == 10
    assert StockItem.objects.get(product=product).bags == 90
    assert not StockMovement.objects.filter(reason="shipment_correction").exists()


def test_shipped_correction_allows_negative_stock_and_logs_warning(manager):
    product = _product(stock=0)
    order = _order(
        status="shipped",
        rows=[(product, 1, "100.00")],
    )

    response = _patch_items(
        manager,
        order,
        [(product, 6, "100.00")],
        reason="Уточнён реальный объём",
    )

    assert response.status_code == 200, response.data
    assert StockItem.objects.get(product=product).bags == -5
    movement = StockMovement.objects.get(reason="shipment_correction")
    assert movement.delta == -5
    assert movement.balance_after == -5
    warning = EventLog.objects.get(order=order, event_type="stock_negative")
    assert warning.payload["action"] == "shipment_correction"
    assert warning.payload["balance"] == -5


def test_active_payment_exposure_blocks_shipped_edit_atomically(manager):
    product = _product(stock=90)
    order = _order(
        status="shipped",
        rows=[(product, 10, "100.00")],
    )
    Payment.objects.create(
        order=order,
        amount="900.00",
        status="received",
    )

    response = _patch_items(
        manager,
        order,
        [(product, 5, "100.00")],
        reason="Исправлено количество",
    )

    assert response.status_code == 400
    assert response.data["code"] == "active_payments_exceed_total"
    assert order.items.get().quantity == 10
    assert StockItem.objects.get(product=product).bags == 90
    assert not StockMovement.objects.filter(reason="shipment_correction").exists()


def test_confirmed_overpayment_is_preserved_after_shipped_edit(manager):
    product = _product(stock=90)
    order = _order(
        status="shipped",
        rows=[(product, 10, "100.00")],
    )
    payment = Payment.objects.create(
        order=order,
        amount="900.00",
        status="confirmed",
    )

    response = _patch_items(
        manager,
        order,
        [(product, 5, "100.00")],
        reason="Исправлено количество",
    )

    assert response.status_code == 200, response.data
    order.refresh_from_db()
    payment.refresh_from_db()
    assert payment.amount == Decimal("900.00")
    assert payment.status == "confirmed"
    assert order.payment_status == "settled"
    assert order.remaining_amount == Decimal("-400.00")
    assert StockItem.objects.get(product=product).bags == 95


def test_open_ai_session_blocks_item_edits(manager):
    product = _product(stock=100)
    order = _order(
        status="confirmed",
        rows=[(product, 10, "100.00")],
    )
    session = AiCountingSession.objects.create(
        order=order,
        camera="cam-safe-edit",
        status=AiCountingSession.STARTING,
        started_by=manager,
    )

    response = _patch_items(
        manager,
        order,
        [(product, 5, "100.00")],
    )

    assert response.status_code == 400
    assert response.data["code"] == "ai_session_active"
    assert order.items.get().quantity == 10
    assert AiCountingSession.objects.filter(pk=session.pk).exists()


def test_loaded_order_can_be_corrected_without_rewriting_ai_snapshot(manager):
    product = _product(stock=100)
    order = _order(
        status="loaded",
        rows=[(product, 2, "100.00")],
    )
    shipment = Shipment.objects.create(order=order, bags_loaded=2)
    session = AiCountingSession.objects.create(
        order=order,
        camera="cam-loaded-edit",
        status=AiCountingSession.CLOSED,
        final_total=2,
        started_by=manager,
        closed_by=manager,
    )

    response = _patch_items(
        manager,
        order,
        [(product, 3, "100.00")],
    )

    assert response.status_code == 200, response.data
    assert order.items.get().quantity == 3
    assert StockItem.objects.get(product=product).bags == 100
    assert not StockMovement.objects.filter(reason="shipment_correction").exists()
    shipment.refresh_from_db()
    session.refresh_from_db()
    assert shipment.bags_loaded == 2
    assert session.final_total == 2


@pytest.mark.parametrize("status", ["rejected", "cancelled"])
def test_closed_unshipped_order_can_be_corrected_without_stock_movement(
    manager,
    status,
):
    product = _product(stock=100)
    order = _order(
        status=status,
        rows=[(product, 2, "100.00")],
    )

    response = _patch_items(
        manager,
        order,
        [(product, 3, "100.00")],
    )

    assert response.status_code == 200, response.data
    assert order.items.get().quantity == 3
    assert StockItem.objects.get(product=product).bags == 100
    assert not StockMovement.objects.filter(reason="shipment_correction").exists()


def test_loading_order_remains_locked_without_ai_session(manager):
    product = _product(stock=100)
    order = _order(
        status="loading",
        rows=[(product, 2, "100.00")],
    )

    response = _patch_items(
        manager,
        order,
        [(product, 3, "100.00")],
    )

    assert response.status_code == 400
    assert response.data["code"] == "items_locked"
    assert order.items.get().quantity == 2


def test_shipped_edit_with_deleted_historical_product_is_blocked(manager):
    deleted_product = _product(stock=90)
    order = _order(
        status="shipped",
        rows=[(deleted_product, 10, "100.00")],
    )
    deleted_product.delete()
    replacement = _product(stock=100)

    response = _patch_items(
        manager,
        order,
        [(replacement, 10, "100.00")],
        reason="Исправлен удалённый товар",
    )

    assert response.status_code == 400
    assert response.data["code"] == "product_deleted"
    historical = order.items.get()
    assert historical.product_id is None
    assert historical.quantity == 10
    assert StockItem.objects.get(product=replacement).bags == 100
    assert not StockMovement.objects.filter(reason="shipment_correction").exists()
