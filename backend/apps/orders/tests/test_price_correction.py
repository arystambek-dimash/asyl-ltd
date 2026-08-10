from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.eventlog.models import EventLog
from apps.orders.models import Order, OrderItem, Payment


pytestmark = pytest.mark.django_db


def _api(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


def _order(*, quantities=(50,), prices=("100.00",), payment_status="unpaid"):
    client = Client.objects.create_with_user(first_name="Ерхан", last_name="Тетрадь", phone="x")
    order = Order.objects.create(
        client=client,
        status="shipped",
        settlement_intent="debt",
        payment_status=payment_status,
    )
    for index, (quantity, price) in enumerate(zip(quantities, prices, strict=True)):
        product = Product.objects.create(
            name=f"Мука {index}", color="Белый", weight_kg="50", price="1.00"
        )
        OrderItem.objects.create(
            order=order,
            product=product,
            quantity=quantity,
            unit_price=price,
        )
    return order


def test_shipped_order_total_is_divided_by_bag_count(user_with_perms):
    user = user_with_perms("corrector", codes=["orders.correct_price"])
    order = _order()

    response = _api(user).post(
        f"/api/orders/{order.id}/correct-price/",
        {"total_amount": "4000000000.00"},
        format="json",
    )

    assert response.status_code == 200
    order.refresh_from_db()
    item = order.items.get()
    assert item.unit_price == Decimal("80000000.00")
    assert order.total_amount == Decimal("4000000000.00")
    assert order.remaining_amount == Decimal("4000000000.00")
    event = EventLog.objects.get(order=order, event_type="order_price_correction")
    assert event.payload["old_total"] == "5000.00"
    assert event.payload["new_total"] == "4000000000.00"
    assert event.payload["mode"] == "total"


def test_prices_can_be_corrected_per_item(user_with_perms):
    user = user_with_perms("corrector", codes=["orders.correct_price"])
    order = _order(quantities=(20, 30), prices=("10.00", "20.00"))
    items = list(order.items.order_by("id"))

    response = _api(user).post(
        f"/api/orders/{order.id}/correct-price/",
        {"prices": {str(items[0].id): "30.00", str(items[1].id): "40.00"}},
        format="json",
    )

    assert response.status_code == 200
    order.refresh_from_db()
    assert list(order.items.order_by("id").values_list("unit_price", flat=True)) == [
        Decimal("30.00"),
        Decimal("40.00"),
    ]
    assert order.total_amount == Decimal("1800.00")


def test_correction_recomputes_cashier_payment_status(user_with_perms):
    user = user_with_perms("corrector", codes=["orders.correct_price"])
    order = _order(quantities=(1,), prices=("100.00",), payment_status="settled")
    Payment.objects.create(order=order, amount="100.00", status="confirmed")

    response = _api(user).post(
        f"/api/orders/{order.id}/correct-price/",
        {"total_amount": "200.00"},
        format="json",
    )

    assert response.status_code == 200
    order.refresh_from_db()
    assert order.payment_status == "partial"
    assert order.remaining_amount == Decimal("100.00")
    assert response.data["payment_status"] == "partial"
    assert response.data["remaining_amount"] == "100.00"


def test_lower_total_keeps_confirmed_cash_and_marks_order_settled(user_with_perms):
    user = user_with_perms("corrector", codes=["orders.correct_price"])
    order = _order(quantities=(1,), prices=("200.00",), payment_status="partial")
    payment = Payment.objects.create(order=order, amount="150.00", status="confirmed")

    response = _api(user).post(
        f"/api/orders/{order.id}/correct-price/",
        {"total_amount": "100.00"},
        format="json",
    )

    assert response.status_code == 200
    order.refresh_from_db()
    payment.refresh_from_db()
    assert payment.amount == Decimal("150.00")
    assert payment.status == "confirmed"
    assert order.payment_status == "settled"
    assert order.remaining_amount == Decimal("-50.00")


def test_active_payment_that_would_exceed_new_total_blocks_correction(user_with_perms):
    user = user_with_perms("corrector", codes=["orders.correct_price"])
    order = _order(quantities=(1,), prices=("200.00",))
    Payment.objects.create(order=order, amount="150.00", status="received")

    response = _api(user).post(
        f"/api/orders/{order.id}/correct-price/",
        {"total_amount": "100.00"},
        format="json",
    )

    assert response.status_code == 400
    assert response.data["code"] == "active_payments_exceed_total"
    assert order.items.get().unit_price == Decimal("200.00")


def test_total_must_produce_an_exact_cent_price(user_with_perms):
    user = user_with_perms("corrector", codes=["orders.correct_price"])
    order = _order(quantities=(3,), prices=("10.00",))

    response = _api(user).post(
        f"/api/orders/{order.id}/correct-price/",
        {"total_amount": "100.00"},
        format="json",
    )

    assert response.status_code == 400
    assert response.data["code"] == "total_not_divisible"
    assert order.items.get().unit_price == Decimal("10.00")


def test_correction_requires_dedicated_permission(manager):
    order = _order()

    response = _api(manager).post(
        f"/api/orders/{order.id}/correct-price/",
        {"total_amount": "1000.00"},
        format="json",
    )

    assert response.status_code == 403
