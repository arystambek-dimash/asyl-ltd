from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from django.db import close_old_connections, connection, connections, transaction
from rest_framework.exceptions import ValidationError

from apps.catalog.models import Product
from apps.eventlog.models import EventLog
from apps.warehouse import services
from apps.warehouse.models import StockItem, StockMovement, Warehouse
from apps.warehouse.services import get_default_warehouse, transfer_stock

pytestmark = pytest.mark.django_db


def _product():
    return Product.objects.create(
        name="Трансферный товар",
        color="Red",
        weight_kg="50",
        price="100.00",
    )


def _secondary(code="second", name="Второй склад"):
    return Warehouse.objects.create(code=code, name=name)


def test_partial_transfer_preserves_total_and_writes_correlated_ledger(boss):
    source_warehouse = get_default_warehouse()
    destination_warehouse = _secondary()
    product = _product()
    services.receive_stock(product, 20, boss, warehouse=source_warehouse)

    result = transfer_stock(
        product,
        7,
        boss,
        from_warehouse=source_warehouse,
        to_warehouse=destination_warehouse,
        note="между цехами",
    )

    source = StockItem.objects.get(
        product=product,
        warehouse=source_warehouse,
    )
    destination = StockItem.objects.get(
        product=product,
        warehouse=destination_warehouse,
    )
    assert source.bags == 13
    assert destination.bags == 7
    assert source.bags + destination.bags == 20
    assert result["source"].pk == source.pk
    assert result["destination"].pk == destination.pk

    movements = list(
        StockMovement.objects.filter(transfer_id=result["transfer_id"]).order_by("id")
    )
    ledger = [
        (row.warehouse_id, row.delta, row.balance_after, row.reason)
        for row in movements
    ]
    assert ledger == [
        (source_warehouse.pk, -7, 13, "transfer_out"),
        (destination_warehouse.pk, 7, 7, "transfer_in"),
    ]
    assert all("между цехами" in row.note for row in movements)

    event = EventLog.objects.get(event_type="stock_transfer")
    assert event.user == boss
    assert event.payload == {
        "transfer_id": str(result["transfer_id"]),
        "product": product.pk,
        "bags": 7,
        "from_warehouse": source_warehouse.pk,
        "from_warehouse_code": source_warehouse.code,
        "from_balance": 13,
        "to_warehouse": destination_warehouse.pk,
        "to_warehouse_code": destination_warehouse.code,
        "to_balance": 7,
        "note": "между цехами",
    }


def test_transfer_adds_to_an_existing_destination_balance(boss):
    source_warehouse = get_default_warehouse()
    destination_warehouse = _secondary()
    product = _product()
    services.receive_stock(product, 20, boss, warehouse=source_warehouse)
    services.receive_stock(product, 4, boss, warehouse=destination_warehouse)

    transfer_stock(
        product,
        5,
        boss,
        from_warehouse=source_warehouse,
        to_warehouse=destination_warehouse,
    )

    assert StockItem.objects.get(
        product=product, warehouse=source_warehouse
    ).bags == 15
    assert StockItem.objects.get(
        product=product, warehouse=destination_warehouse
    ).bags == 9


@pytest.mark.parametrize("bags", [0, -1, True, "not-a-number"])
def test_transfer_rejects_invalid_amount_without_mutation(boss, bags):
    source_warehouse = get_default_warehouse()
    destination_warehouse = _secondary()
    product = _product()
    services.receive_stock(product, 10, boss, warehouse=source_warehouse)

    with pytest.raises(ValidationError) as exc_info:
        transfer_stock(
            product,
            bags,
            boss,
            from_warehouse=source_warehouse,
            to_warehouse=destination_warehouse,
        )

    assert str(exc_info.value.detail["code"]) == "invalid_bags"
    assert StockItem.objects.get(product=product).bags == 10
    assert not StockMovement.objects.filter(transfer_id__isnull=False).exists()


def test_transfer_rejects_same_warehouse_and_insufficient_stock(boss):
    source_warehouse = get_default_warehouse()
    destination_warehouse = _secondary()
    product = _product()
    services.receive_stock(product, 3, boss, warehouse=source_warehouse)

    with pytest.raises(ValidationError) as same_error:
        transfer_stock(
            product,
            1,
            boss,
            from_warehouse=source_warehouse,
            to_warehouse=source_warehouse,
        )
    with pytest.raises(ValidationError) as stock_error:
        transfer_stock(
            product,
            4,
            boss,
            from_warehouse=source_warehouse,
            to_warehouse=destination_warehouse,
        )

    assert str(same_error.value.detail["code"]) == "same_warehouse"
    assert str(stock_error.value.detail["code"]) == "insufficient_stock"
    assert int(stock_error.value.detail["available"]) == 3
    assert not StockItem.objects.filter(
        product=product,
        warehouse=destination_warehouse,
    ).exists()


def test_transfer_rejects_inactive_source_or_destination(boss):
    source_warehouse = get_default_warehouse()
    destination_warehouse = _secondary()
    product = _product()
    services.receive_stock(product, 3, boss, warehouse=source_warehouse)
    destination_warehouse.is_active = False
    destination_warehouse.save(update_fields=["is_active"])

    with pytest.raises(ValidationError) as exc_info:
        transfer_stock(
            product,
            1,
            boss,
            from_warehouse=source_warehouse,
            to_warehouse=destination_warehouse,
        )

    assert str(exc_info.value.detail["code"]) == "warehouse_inactive"


def test_transfer_rolls_back_balances_and_ledger_when_audit_fails(boss, monkeypatch):
    source_warehouse = get_default_warehouse()
    destination_warehouse = _secondary()
    product = _product()
    services.receive_stock(product, 10, boss, warehouse=source_warehouse)
    movement_count = StockMovement.objects.count()

    def fail_audit(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(services, "log_event", fail_audit)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        transfer_stock(
            product,
            4,
            boss,
            from_warehouse=source_warehouse,
            to_warehouse=destination_warehouse,
        )

    assert StockItem.objects.get(
        product=product, warehouse=source_warehouse
    ).bags == 10
    assert not StockItem.objects.filter(
        product=product, warehouse=destination_warehouse
    ).exists()
    assert StockMovement.objects.count() == movement_count


def test_transfer_endpoint_contract_and_permissions(auth_client, operator, boss):
    source_warehouse = get_default_warehouse()
    destination_warehouse = _secondary()
    product = _product()
    services.receive_stock(product, 10, boss, warehouse=source_warehouse)
    payload = {
        "product": product.pk,
        "from_warehouse": source_warehouse.pk,
        "to_warehouse": destination_warehouse.pk,
        "bags": 6,
    }

    denied = auth_client(operator).post("/api/stock/transfer/", payload, format="json")
    response = auth_client(boss).post("/api/stock/transfer/", payload, format="json")

    assert denied.status_code == 403
    assert response.status_code == 200
    assert response.data["product"] == product.pk
    assert response.data["bags"] == 6
    assert response.data["source"]["warehouse"] == source_warehouse.pk
    assert response.data["source"]["bags"] == 4
    assert response.data["destination"]["warehouse"] == destination_warehouse.pk
    assert response.data["destination"]["bags"] == 6
    assert response.data["transfer_id"]


@pytest.mark.django_db(transaction=True)
def test_concurrent_transfers_cannot_overspend(boss):
    if connection.vendor != "postgresql":
        pytest.skip("row-lock concurrency contract requires PostgreSQL")

    source_warehouse = get_default_warehouse()
    first_destination = _secondary("first", "Первый склад")
    second_destination = _secondary("second", "Второй склад")
    product = _product()
    services.receive_stock(product, 10, boss, warehouse=source_warehouse)

    def run(destination_id):
        close_old_connections()
        try:
            transfer_stock(
                Product.objects.get(pk=product.pk),
                7,
                type(boss).objects.get(pk=boss.pk),
                from_warehouse=source_warehouse.pk,
                to_warehouse=destination_id,
            )
            return "ok"
        except ValidationError as exc:
            return str(exc.detail["code"])
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(run, [first_destination.pk, second_destination.pk])
        )

    assert sorted(results) == ["insufficient_stock", "ok"]
    assert StockItem.objects.get(
        product=product, warehouse=source_warehouse
    ).bags == 3
    assert sum(
        StockItem.objects.filter(product=product).values_list("bags", flat=True)
    ) == 10


@pytest.mark.django_db(transaction=True)
def test_transfer_warehouse_locks_do_not_deadlock_with_movement_foreign_key(
    boss,
    monkeypatch,
):
    """A stock writer may hold Product before its warehouse FK is checked."""
    if connection.vendor != "postgresql":
        pytest.skip("row-lock compatibility contract requires PostgreSQL")

    source_warehouse = get_default_warehouse()
    destination_warehouse = _secondary()
    product = _product()
    services.receive_stock(product, 10, boss, warehouse=source_warehouse)

    transfer_reached_product = Event()
    movement_holds_product = Event()
    original_locked_stock_item = services._locked_stock_item

    def synchronize_after_warehouse_locks(*args, **kwargs):
        transfer_reached_product.set()
        if not movement_holds_product.wait(timeout=5):
            raise RuntimeError("movement did not acquire the product lock")
        return original_locked_stock_item(*args, **kwargs)

    monkeypatch.setattr(
        services,
        "_locked_stock_item",
        synchronize_after_warehouse_locks,
    )

    def run_transfer():
        close_old_connections()
        try:
            return services.transfer_stock(
                Product.objects.get(pk=product.pk),
                1,
                type(boss).objects.get(pk=boss.pk),
                from_warehouse=source_warehouse.pk,
                to_warehouse=destination_warehouse.pk,
            )
        finally:
            connections.close_all()

    def insert_movement_while_product_is_locked():
        close_old_connections()
        try:
            if not transfer_reached_product.wait(timeout=5):
                raise RuntimeError("transfer did not acquire the warehouse locks")
            with transaction.atomic():
                locked_product = Product.objects.select_for_update().get(
                    pk=product.pk
                )
                movement_holds_product.set()
                StockMovement.objects.create(
                    warehouse_id=source_warehouse.pk,
                    product=locked_product,
                    delta=0,
                    balance_after=10,
                    reason="adjustment",
                    created_by_id=boss.pk,
                )
            return "movement-written"
        finally:
            movement_holds_product.set()
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        transfer_future = pool.submit(run_transfer)
        movement_future = pool.submit(insert_movement_while_product_is_locked)
        assert movement_future.result(timeout=10) == "movement-written"
        result = transfer_future.result(timeout=10)

    assert result["source"].bags == 9
    assert result["destination"].bags == 1
