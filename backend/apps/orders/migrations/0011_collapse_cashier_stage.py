from django.db import migrations
from decimal import Decimal


def forward(apps, schema_editor):
    """Схлопывание стадии кассира: бухгалтер-касса финализирует оплату.

    Оплаты, застрявшие на «сверена бухгалтером» (accountant_ok), становятся
    «подтверждена» (confirmed) — переносим штамп бухгалтера в поля подтверждения,
    если подтверждение не проставлено. Затем пересчитываем payment_status заказов.
    """
    Payment = apps.get_model("orders", "Payment")
    Order = apps.get_model("orders", "Order")

    touched_orders = set()
    for p in Payment.objects.filter(status="accountant_ok"):
        p.status = "confirmed"
        if p.confirmed_by_id is None:
            p.confirmed_by_id = p.accountant_by_id
            p.confirmed_at = p.accountant_at
        p.save(update_fields=["status", "confirmed_by", "confirmed_at"])
        touched_orders.add(p.order_id)

    # Пересчёт payment_status по фактически подтверждённым оплатам.
    for order in Order.objects.filter(id__in=touched_orders):
        paid = sum((pp.amount for pp in order.payments.filter(status="confirmed")),
                   Decimal("0"))
        if paid <= 0:
            new = "unpaid"
        elif order.total_amount > 0 and paid >= order.total_amount:
            new = "settled"
        else:
            new = "partial"
        if order.payment_status != new:
            order.payment_status = new
            order.save(update_fields=["payment_status"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [("orders", "0010_payment_chain_backfill")]
    operations = [migrations.RunPython(forward, noop)]
