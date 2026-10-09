"""Кассовые возвраты уже закрытых «Возвратов» товара — к своему возврату.

Закрытие «Из кассы» писало ``refund_ids`` в событие ``goods_return`` каждого
заказа (с первой версии возвратов). По ним связь ставится точно — без поиска
по сумме: исправление возврата отменит ровно те возвраты оплат, которые он
отдал. Возврат удалён — связь не ставим.
"""

from django.db import migrations


def link_goods_return_refunds(apps, schema_editor):
    EventLog = apps.get_model("eventlog", "EventLog")
    GoodsReturn = apps.get_model("orders", "GoodsReturn")
    PaymentRefund = apps.get_model("orders", "PaymentRefund")
    returns = set(GoodsReturn.objects.values_list("pk", flat=True))
    payloads = EventLog.objects.filter(event_type="goods_return").values_list("payload", flat=True)
    for payload in payloads.iterator(chunk_size=2000):
        return_id = payload.get("goods_return_id")
        refund_ids = payload.get("refund_ids") or []
        if return_id in returns and refund_ids:
            PaymentRefund.objects.filter(
                pk__in=refund_ids, method="cash", goods_return__isnull=True,
            ).update(goods_return_id=return_id)


class Migration(migrations.Migration):

    dependencies = [
        ("eventlog", "0003_eventlog_eventlog_recent_idx_and_more"),
        ("orders", "0053_paymentrefund_goods_return"),
    ]

    operations = [
        migrations.RunPython(link_goods_return_refunds, migrations.RunPython.noop),
    ]
