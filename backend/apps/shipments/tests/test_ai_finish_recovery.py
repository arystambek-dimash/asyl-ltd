"""Завершение погрузки не должно зависеть от исправности внешнего AI-воркера.

Воркер на ПК цеха — сторонний процесс: он может ответить пустым телом,
без поля total или строкой. Раньше такой ответ ронял finish_ai_loading
посреди разбора сессии, и заказ застревал в loading с открытой AI-сессией,
которую не принимали ни ручное завершение, ни откат.
"""
import pytest
from decimal import Decimal

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem
from apps.shipments.services import finish_ai_loading, record_arrival, record_count
from apps.warehouse.models import StockItem
from apps.warehouse.services import receive_stock

pytestmark = pytest.mark.django_db


def _loading_order(boss, operator, qty=50, stock=100):
    product = Product.objects.create(name="Высший", color="Red", weight_kg="50", price="25000")
    receive_stock(product, stock, boss)
    client = Client.objects.create_with_user(first_name="L", last_name="К", phone="x")
    order = Order.objects.create(client=client, status="confirmed", truck_number="01A123")
    OrderItem.objects.create(order=order, product=product, quantity=qty)
    record_arrival(order, Decimal("8000"), operator)
    record_count(order, qty, operator)
    order.refresh_from_db()
    assert order.status == "loading"
    return order, product


@pytest.mark.parametrize("bad_total", [None, "40", 12.5, True, -1])
def test_bad_ai_total_falls_back_to_ordered_quantity(boss, operator, bad_total):
    """Мусор от воркера не оставляет заказ в loading: считаем по заказу."""
    order, product = _loading_order(boss, operator)

    finish_ai_loading(order, bad_total, operator)

    order.refresh_from_db()
    assert order.status == "shipped"
    # Счёт не выдуман: раз AI-число негодное, берём заказанное.
    assert order.shipment.bags_loaded == 50
    assert StockItem.objects.get(product=product).bags == 50


def test_valid_ai_total_is_kept(boss, operator):
    """Годное число воркера сохраняется как есть."""
    order, product = _loading_order(boss, operator)

    finish_ai_loading(order, 48, operator)

    order.refresh_from_db()
    assert order.status == "shipped"
    assert order.shipment.bags_loaded == 48
