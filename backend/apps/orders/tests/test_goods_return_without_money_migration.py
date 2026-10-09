"""Миграция 0056: возврат без денег — колонки остаются в базе, вставляют и новый образ, и прежний (откат)."""

from decimal import Decimal

import pytest
from django.db import connection
from django.utils import timezone

from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import GoodsReturn, Order, OrderItem, PaymentRefund
from apps.orders.services import record_staff_payment
from apps.shipments.models import Shipment
from apps.warehouse.services import get_default_warehouse

pytestmark = pytest.mark.django_db


def _sql(sql, params=()):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchall() if cursor.description else None


def _client():
    return Client.objects.create_with_user(first_name="Клиент", phone="+7 (705) 565-65-65")


def _paid_order(client, boss):
    product = Product.objects.create(name="Первый сорт", color="Blue", weight_kg=Decimal("50"))
    order = Order.objects.create(client=client, status="shipped")
    OrderItem.objects.create(order=order, product=product, quantity=10, unit_price=Decimal("1000"))
    Shipment.objects.create(order=order, shipped_at=timezone.now())
    return product, record_staff_payment(order, Decimal("10000"), boss, method="cash")


def test_new_image_inserts_rows_without_the_money_columns(boss):
    client = _client()
    product, payment = _paid_order(client, boss)
    [(return_id,)] = _sql(
        "INSERT INTO orders_goodsreturn (client_id, warehouse_id, created_at, status, closed_by_storekeeper)"
        " VALUES (%s, %s, now(), 'pending', false) RETURNING id",
        [client.pk, get_default_warehouse().pk],
    )
    _sql(
        "INSERT INTO orders_goodsreturnitem (goods_return_id, product_id, product_label_snapshot, bags)"
        " VALUES (%s, %s, 'x', 4)",
        [return_id, product.pk],
    )
    refund = PaymentRefund.objects.create(payment=payment, amount=Decimal("1"), method="cash", reason="x")

    assert GoodsReturn.objects.get(pk=return_id).settlement == ""
    assert _sql("SELECT bags, paid_bags FROM orders_goodsreturnitem WHERE goods_return_id = %s", [return_id]) == [
        (4, 0),
    ]
    assert _sql("SELECT goods_return_id FROM orders_paymentrefund WHERE id = %s", [refund.pk]) == [(None,)]


def test_previous_image_inserts_with_the_money_columns(boss):
    """Откат образа: прежний код пишет «что с деньгами», платные мешки и связь кассового возврата."""
    client = _client()
    product, payment = _paid_order(client, boss)
    [(return_id,)] = _sql(
        "INSERT INTO orders_goodsreturn"
        " (client_id, settlement, warehouse_id, created_at, status, closed_by_storekeeper)"
        " VALUES (%s, 'cash', %s, now(), 'pending', false) RETURNING id",
        [client.pk, get_default_warehouse().pk],
    )
    _sql(
        "INSERT INTO orders_goodsreturnitem (goods_return_id, product_id, product_label_snapshot, bags, paid_bags)"
        " VALUES (%s, %s, 'x', 4, 3)",
        [return_id, product.pk],
    )
    _sql(
        "INSERT INTO orders_paymentrefund"
        " (payment_id, amount, method, status, reason, goods_return_id, completed_at, created_at, updated_at)"
        " VALUES (%s, 1000, 'cash', 'completed', 'Возврат товара', %s, now(), now(), now())",
        [payment.pk, return_id],
    )

    goods_return = GoodsReturn.objects.get(pk=return_id)
    assert (goods_return.settlement, goods_return.get_settlement_display()) == ("cash", "Из кассы")
    assert list(goods_return.items.values_list("bags", flat=True)) == [4]


def test_deleting_a_return_unlinks_its_old_cash_refunds_in_the_database(boss):
    """Связи кассового возврата в модели больше нет: обнуляет её ``ON DELETE SET NULL`` базы."""
    client = _client()
    _product, payment = _paid_order(client, boss)
    goods_return = GoodsReturn.objects.create(
        client=client, settlement="cash", warehouse=get_default_warehouse(), status="full",
    )
    refund = PaymentRefund.objects.create(
        payment=payment, amount=Decimal("1000"), method="cash", status="completed", reason="Возврат товара",
    )
    _sql("UPDATE orders_paymentrefund SET goods_return_id = %s WHERE id = %s", [goods_return.pk, refund.pk])

    goods_return.delete()
    _sql("SET CONSTRAINTS ALL IMMEDIATE")  # проверка ключей, как при коммите

    assert _sql("SELECT goods_return_id FROM orders_paymentrefund WHERE id = %s", [refund.pk]) == [(None,)]
    assert _sql(
        """
        SELECT con.confdeltype
        FROM pg_constraint con
        JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = ANY(con.conkey)
        WHERE con.contype = 'f' AND con.conrelid = 'orders_paymentrefund'::regclass
          AND att.attname = 'goods_return_id'
        """,
    ) == [("n",)]
