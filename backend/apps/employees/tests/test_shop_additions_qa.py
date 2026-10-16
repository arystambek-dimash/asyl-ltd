"""Независимая проверка учёта денег магазина и сохранности прежних продаж."""

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from apps.employees.models import ShopSale, ShopSalePayment, ShopStock
from apps.employees.shop import set_employee_shop
from apps.orders.models import Order, Payment
from apps.shipments.models import Shipment
from apps.warehouse.models import StockItem
from apps.warehouse.services import receive_stock

pytestmark = pytest.mark.django_db


@pytest.fixture
def shop_additions(user_with_perms, departments, make_product, boss):
    owner = user_with_perms(
        "shop-additions-qa", department=departments[0],
        codes=["orders.create", "orders.view", "orders.edit", "payments.create", "reports.view"],
    )
    shop = set_employee_shop(owner.employee, boss, enabled=True)
    product = make_product(name="Мука для отдельного учёта")
    receive_stock(product, 100, boss)
    ShopStock.objects.create(shop=shop, product=product, bags=0)
    return owner, shop, product


def test_paid_and_backdated_shop_sales_leave_mill_cash_and_reports_unchanged(auth_client, shop_additions):
    owner, shop, product = shop_additions
    api = auth_client(owner)
    report = api.get("/api/reports/summary/")
    assert report.status_code == 200, report.data
    request = {
        "idempotency_key": str(uuid4()),
        "items": [{"product": product.pk, "quantity": 2, "unit_price": "50.00"}],
        "payment": {"amount": "25.00", "method": "cash"},
    }
    created = api.post("/api/shop-orders/", request, format="json")
    assert created.status_code == 201, created.data
    assert created.data["paid_total"] == "25.00"
    assert created.data["remaining"] == "75.00"
    assert created.data["stock_deducted"] is True
    assert ShopStock.objects.get(shop=shop, product=product).bags == -2
    request["payment"]["amount"] = "25"
    assert api.post("/api/shop-orders/", request, format="json").data == created.data
    assert ShopSalePayment.objects.count() == 1
    cancel = f"/api/shop-orders/{created.data['id']}/cancel/"
    first_cancel = api.post(cancel)
    assert first_cancel.status_code == api.post(cancel).status_code == 200
    assert first_cancel.data["paid_total"] == first_cancel.data["remaining"] == "0.00"
    assert first_cancel.data["payment"]["amount"] == "25.00"
    assert first_cancel.data["payment"]["voided_at"] is not None
    assert ShopStock.objects.get(shop=shop, product=product).bags == 0

    day = timezone.localdate() - timedelta(days=5)
    historical = api.post("/api/shop-orders/", {
        "idempotency_key": str(uuid4()),
        "items": [{"product": product.pk, "quantity": 2, "unit_price": "50.00"}],
        "backdate": {"date": day.isoformat(), "paid": True, "payment_method": "bank"},
    }, format="json")
    assert historical.status_code == 201, historical.data
    assert historical.data["stock_deducted"] is False
    assert historical.data["paid_total"] == "100.00"
    assert historical.data["remaining"] == "0.00"
    historical_sale = ShopSale.objects.get(pk=historical.data["id"])
    assert timezone.localdate(historical_sale.sold_at) == day
    assert timezone.localdate(historical_sale.created_at) == timezone.localdate()
    assert timezone.localdate(historical_sale.payment.paid_at) == day
    cancel = f"/api/shop-orders/{historical_sale.pk}/cancel/"
    assert api.post(cancel).status_code == api.post(cancel).status_code == 200
    assert ShopStock.objects.get(shop=shop, product=product).bags == 0
    assert ShopSalePayment.objects.count() == 2
    assert not ShopSalePayment.objects.filter(voided_at__isnull=True).exists()
    assert not Order.objects.exists()
    assert not Payment.objects.exists()
    assert not Shipment.objects.exists()
    assert StockItem.objects.get(warehouse=shop.warehouse, product=product).bags == 100
    assert api.get("/api/reports/summary/").data == report.data


@pytest.mark.django_db(transaction=True)
def test_shop_additions_migration_keeps_historical_sale_dates_payload_and_stock(shop_additions):
    owner, shop, product = shop_additions
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    previous = [("employees", "0014_allow_negative_shop_stock")]
    try:
        executor.migrate(previous)
        state = executor.loader.project_state(previous).apps
        old_sale = state.get_model("employees", "ShopSale")
        old_item = state.get_model("employees", "ShopSaleItem")
        payload = {"client": None, "items": [{"product": product.pk, "quantity": 1}]}
        sale = old_sale.objects.create(
            shop_id=shop.pk, created_by_id=owner.pk, currency="KZT",
            idempotency_key=uuid4(), request_payload=payload,
        )
        old_item.objects.create(
            sale=sale, product_id=product.pk, product_label_snapshot="Старая продажа",
            quantity=1, unit_price=Decimal("125.50"),
        )
        original_date = timezone.now() - timedelta(days=9)
        old_sale.objects.filter(pk=sale.pk).update(created_at=original_date)
        MigrationExecutor(connection).migrate(latest)
        migrated = ShopSale.objects.get(pk=sale.pk)
        assert migrated.created_at == migrated.sold_at == original_date
        assert migrated.stock_deducted is True
        assert migrated.backdated_at is None
        assert migrated.request_payload == payload
        assert migrated.total_amount == Decimal("125.50")
        assert not ShopSalePayment.objects.exists()
        assert ShopStock.objects.get(shop=shop, product=product).bags == 0
    finally:
        MigrationExecutor(connection).migrate(latest)
