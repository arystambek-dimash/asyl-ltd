"""Вес груза в карточке заказа считается по всем позициям, а не по первой.

Поле показывается рядом с весом машины на весах, поэтому оно должно
совпадать с расчётом поста погрузки (shipments.estimated_load_kg).
"""
import pytest
from decimal import Decimal

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem
from apps.orders.serializers import OrderSerializer
from apps.shipments.models import Shipment
from apps.shipments.services import estimated_load_kg

pytestmark = pytest.mark.django_db


def _mixed_order():
    heavy = Product.objects.create(name="Тяжёлый", color="Red", weight_kg="50", price="1")
    light = Product.objects.create(name="Лёгкий", color="Blue", weight_kg="25", price="1")
    client = Client.objects.create(first_name="A", last_name="B", phone="x")
    order = Order.objects.create(client=client, status="shipped", truck_number="01A")
    OrderItem.objects.create(order=order, product=heavy, quantity=30, unit_price="1")
    OrderItem.objects.create(order=order, product=light, quantity=20, unit_price="1")
    Shipment.objects.create(order=order, truck_number="01A", bags_loaded=50)
    return order


def test_bag_estimate_matches_loading_post_for_mixed_order():
    """30×50кг + 20×25кг = 2000 кг, а не 50×50 = 2500 по первой позиции."""
    order = _mixed_order()

    data = OrderSerializer(order).data

    assert Decimal(data["bag_estimate_kg"]) == Decimal("2000")
    assert Decimal(data["bag_estimate_kg"]) == estimated_load_kg(order)


def test_bag_estimate_scales_to_counted_bags():
    """Если камера насчитала меньше мешков, вес уменьшается пропорционально."""
    order = _mixed_order()
    order.shipment.bags_loaded = 25  # половина от 50 заказанных
    order.shipment.save(update_fields=["bags_loaded"])

    data = OrderSerializer(order).data

    assert Decimal(data["bag_estimate_kg"]) == Decimal("1000")
