"""Бэкфилл Order.payment_status одним проходом.

Список должников теперь не считает заказы с payment_status="settled", поэтому
статус обязан совпадать с фактом для всей истории, а не только для заказов,
которые прошли через сервис оплат после появления синхронизации. Формулы —
те же, что у services._payment_status_for и querysets.with_order_amounts
(в миграции они повторены намеренно: код приложения меняется, миграция — нет).
"""
from decimal import Decimal

from django.db import migrations
from django.db.models import DecimalField, F, Sum, Value
from django.db.models.functions import Coalesce, Greatest

BATCH = 1000


def _payment_status(total, paid):
    if paid <= 0:
        return "unpaid"
    if paid >= total and total > 0:
        return "settled"
    return "partial"


def backfill_payment_status(apps, schema_editor):
    Order = apps.get_model("orders", "Order")
    OrderItem = apps.get_model("orders", "OrderItem")
    Payment = apps.get_model("orders", "Payment")
    money = DecimalField(max_digits=30, decimal_places=2)
    zero = Value(Decimal("0"), output_field=money)
    totals = dict(
        OrderItem.objects.order_by()
        .values("order_id")
        .annotate(value=Sum(F("quantity") * Coalesce("unit_price", zero), output_field=money))
        .values_list("order_id", "value")
    )
    paid = dict(
        Payment.objects.filter(status="confirmed")
        .order_by()
        .values("order_id")
        .annotate(
            value=Sum(Greatest(F("amount") - F("refunded_amount"), zero), output_field=money)
        )
        .values_list("order_id", "value")
    )
    none = Decimal("0")
    fixes = []
    for order in Order.objects.order_by().only("id", "payment_status").iterator(chunk_size=5000):
        want = _payment_status(totals.get(order.id) or none, paid.get(order.id) or none)
        if want != order.payment_status:
            order.payment_status = want
            fixes.append(order)
    for start in range(0, len(fixes), BATCH):
        Order.objects.bulk_update(fixes[start:start + BATCH], ["payment_status"])


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0038_payment_order_status_idx"),
    ]

    operations = [
        migrations.RunPython(backfill_payment_status, migrations.RunPython.noop),
    ]
