"""Оплаты и внесение продаж магазина задним числом без кассы мельницы."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
from django.db import close_old_connections
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.employees.models import ShopSale, ShopSalePayment, ShopStock
from apps.employees.shop import set_employee_shop
from apps.employees.shop_sales import place_shop_sale
from apps.orders.backdate import backdate_moment
from apps.orders.models import Order, Payment
from apps.shipments.models import Shipment

pytestmark = pytest.mark.django_db


@pytest.fixture
def setup(user_with_perms, make_product, boss):
    owner = user_with_perms("shop-additions", codes=["orders.create", "orders.edit", "payments.create"])
    shop = set_employee_shop(owner.employee, boss, enabled=True)
    product = make_product(name="Дополнения магазина")
    ShopStock.objects.create(shop=shop, product=product, bags=10)
    return owner, shop, product


def _body(product, **extra):
    return {
        "idempotency_key": str(uuid4()),
        "items": [{"product": product.pk, "quantity": 2, "unit_price": "500"}],
        **extra,
    }


def _stock(shop, product):
    return ShopStock.objects.get(shop=shop, product=product).bags


def _remove(owner, code, get_permission):
    owner.employee.permissions.remove(get_permission(code))
    owner.__dict__.pop("perm_codes", None)


@pytest.mark.parametrize("amount,remaining", [("250.50", "749.50"), ("1000", "0.00")])
def test_shop_payment_is_separate_and_atomic_snapshot(auth_client, setup, amount, remaining):
    owner, shop, product = setup
    api = auth_client(owner)
    body = _body(product, payment={"amount": amount, "method": "cash"})
    response = api.post("/api/shop-orders/", body, format="json")
    assert response.status_code == 201, response.data
    assert response.data["paid_total"] == f"{Decimal(amount):.2f}"
    assert response.data["remaining"] == remaining
    assert response.data["payment"]["voided_at"] is None
    assert response.data["stock_deducted"] is True
    assert _stock(shop, product) == 8
    assert not Order.objects.exists() and not Payment.objects.exists() and not Shipment.objects.exists()
    assert api.post("/api/shop-orders/", body, format="json").data == response.data
    assert ShopSalePayment.objects.count() == 1
    assert ShopSalePayment.objects.get().recorded_by == owner
    body["payment"]["amount"] = "250.51"
    assert api.post("/api/shop-orders/", body, format="json").status_code == 400
    assert _stock(shop, product) == 8


@pytest.mark.parametrize("paid", [False, True])
def test_backdate_skips_stock_and_cancel_does_not_inflate_it(auth_client, setup, paid):
    owner, shop, product = setup
    day = timezone.localdate() - timedelta(days=7)
    api = auth_client(owner)
    body = _body(product, backdate={"date": day.isoformat(), "paid": paid})
    response = api.post("/api/shop-orders/", body, format="json")
    assert response.status_code == 201, response.data
    sale = ShopSale.objects.get()
    assert sale.backdated_at == backdate_moment(day)
    assert sale.created_at > sale.sold_at
    assert sale.stock_deducted is False
    assert response.data["paid_total"] == ("1000.00" if paid else "0.00")
    assert response.data["remaining"] == ("0.00" if paid else "1000.00")
    assert _stock(shop, product) == 10
    if paid:
        assert sale.payment.paid_at == sale.sold_at
    assert api.post("/api/shop-orders/", body, format="json").data == response.data
    url = f"/api/shop-orders/{sale.pk}/cancel/"
    first = api.post(url)
    assert first.status_code == 200, first.data
    assert first.data["paid_total"] == first.data["remaining"] == "0.00"
    assert first.data["payment"] is None if not paid else first.data["payment"]["voided_at"] is not None
    assert api.post(url).data == first.data
    assert _stock(shop, product) == 10
    assert not Payment.objects.exists()


def test_unpaid_backdate_replay_after_edit_permission_removed(auth_client, setup, get_permission):
    owner, _, product = setup
    api = auth_client(owner)
    body = _body(product, backdate={"date": timezone.localdate().isoformat()})
    response = api.post("/api/shop-orders/", body, format="json")
    _remove(owner, "orders.edit", get_permission)
    assert api.post("/api/shop-orders/", body, format="json").data == response.data
    body["idempotency_key"] = str(uuid4())
    assert api.post("/api/shop-orders/", body, format="json").status_code == 403


def test_paid_cancel_and_replay_with_revoked_permission(auth_client, setup, get_permission):
    owner, shop, product = setup
    api = auth_client(owner)
    body = _body(product, payment={"amount": "250", "method": "cash"})
    response = api.post("/api/shop-orders/", body, format="json")
    url = f"/api/shop-orders/{response.data['id']}/cancel/"
    _remove(owner, "payments.create", get_permission)
    assert api.post("/api/shop-orders/", body, format="json").data == response.data
    assert api.post(url).status_code == 403
    assert ShopSalePayment.objects.get().voided_at is None
    assert _stock(shop, product) == 8
    owner.employee.permissions.add(get_permission("payments.create"))
    owner.__dict__.pop("perm_codes", None)
    cancelled = api.post(url)
    assert cancelled.status_code == 200
    assert cancelled.data["paid_total"] == cancelled.data["remaining"] == "0.00"
    payment = ShopSalePayment.objects.get()
    assert payment.amount == Decimal("250") and payment.voided_at is not None
    _remove(owner, "payments.create", get_permission)
    assert api.post(url).data == cancelled.data
    assert _stock(shop, product) == 10


@pytest.mark.parametrize("extra,permission", [
    ({"payment": {"amount": "1", "method": "cash"}}, "payments.create"),
    ({"backdate": {"date": "2026-01-01"}}, "orders.edit"),
    ({"backdate": {"date": "2026-01-01", "paid": True}}, "payments.create"),
])
def test_additions_permissions_enforced(auth_client, setup, get_permission, extra, permission):
    owner, shop, product = setup
    _remove(owner, permission, get_permission)
    response = auth_client(owner).post("/api/shop-orders/", _body(product, **extra), format="json")
    assert response.status_code == 403, response.data
    assert not ShopSale.objects.exists() and not ShopSalePayment.objects.exists()
    assert _stock(shop, product) == 10


@pytest.mark.parametrize("extra", [
    {"payment": {"amount": "1000.01", "method": "cash"}},
    *({"payment": {"amount": amount, "method": "cash"}} for amount in ("0", "-1", "NaN", "Infinity", "1.001", None, True)),
    {"payment": {"amount": "1", "method": "invoice"}},
    {"payment": {"amount": "1", "method": []}},
    {"backdate": {"date": "garbage"}},
    {"backdate": {"date": "2999-01-01"}},
    {"backdate": {"date": "2026-01-01"}, "payment": {"amount": "1", "method": "cash"}},
])
def test_invalid_additions_rejected_by_api_and_service_without_writes(auth_client, setup, extra):
    owner, shop, product = setup
    response = auth_client(owner).post("/api/shop-orders/", _body(product, **extra), format="json")
    assert response.status_code == 400, response.data
    with pytest.raises(ValidationError):
        place_shop_sale(owner, idempotency_key=uuid4(), items=[{
            "product": product, "quantity": 2, "unit_price": "500",
        }], **extra)
    assert not ShopSale.objects.exists() and not ShopSalePayment.objects.exists()
    assert _stock(shop, product) == 10


@pytest.mark.parametrize("method", ["kaspi", "remote"])
def test_payment_methods_enforce_shop_currency(auth_client, setup, method):
    owner, shop, product = setup
    shop.client.currency = "USD"
    shop.client.save(update_fields=["currency"])
    response = auth_client(owner).post("/api/shop-orders/", _body(product, payment={"amount": "1", "method": method}), format="json")
    assert response.status_code == 400
    assert response.data["code"] == "payment_kzt_only"
    assert not ShopSale.objects.exists()


def test_paid_replay_returns_snapshot_after_shop_currency_change(auth_client, setup):
    owner, shop, product = setup
    api = auth_client(owner)
    body = _body(product, payment={"amount": "500", "method": "kaspi"})
    original = api.post("/api/shop-orders/", body, format="json")
    assert original.status_code == 201
    shop.client.currency = "USD"
    shop.client.save(update_fields=["currency"])
    assert api.post("/api/shop-orders/", body, format="json").data == original.data
    assert ShopSalePayment.objects.count() == 1


def test_effective_sale_date_orders_history(auth_client, setup):
    owner, _, product = setup
    api = auth_client(owner)
    current = api.post("/api/shop-orders/", _body(product), format="json").data
    historical = api.post("/api/shop-orders/", _body(product, backdate={"date": "2026-01-01"}), format="json").data
    assert [row["id"] for row in api.get("/api/shop-orders/").data] == [current["id"], historical["id"]]


@pytest.mark.django_db(transaction=True)
def test_concurrent_paid_same_key_creates_payment_and_deducts_once(auth_client, setup):
    owner, shop, product = setup
    barrier = Barrier(2)
    body = _body(product, payment={"amount": "1000", "method": "bank"})

    def run(_):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            response = auth_client(owner).post("/api/shop-orders/", body, format="json")
            return response.status_code, response.data
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(run, range(2)))
    assert responses[0] == responses[1]
    assert responses[0][0] == 201
    assert ShopSale.objects.count() == ShopSalePayment.objects.count() == 1
    assert _stock(shop, product) == 8
