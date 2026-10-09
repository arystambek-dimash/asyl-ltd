"""Возврат товара без денег: новый образ не пишет «что с деньгами», платные мешки и связь с кассой.

Expand/contract — колонки остаются в базе, физически их снимет отдельная
миграция позже, когда откат на прежний образ уже не понадобится:

* ``GoodsReturn.settlement`` остаётся в модели (подпись старых возвратов), но
  становится необязательной: ``db_default ''`` — строку без неё вставляет и
  новый образ, а прежний пишет «debt» или «cash» как раньше;
* ``GoodsReturnItem.paid_bags`` — сначала ``db_default 0`` в базе, потом колонка
  уходит из модели: новый образ её не вставляет, прежний вставляет сам;
* ``PaymentRefund.goods_return`` — ключ сначала повторяет ``ON DELETE SET NULL``
  в базе (удаление возврата новым образом не знает о связи), потом уходит из
  модели. Столбец обнуляемый — вставка без него проходит.
"""

from django.db import migrations, models

from apps.common.migration_ops import db_on_delete


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0055_goodsreturn_closed_by_storekeeper"),
    ]

    operations = [
        migrations.AlterField(
            model_name="goodsreturn",
            name="settlement",
            field=models.CharField(
                blank=True,
                choices=[("debt", "В счёт долга"), ("cash", "Из кассы")],
                db_default="",
                default="",
                max_length=10,
            ),
        ),
        migrations.AlterField(
            model_name="goodsreturnitem",
            name="paid_bags",
            field=models.PositiveIntegerField(db_default=0),
        ),
        db_on_delete("orders", "PaymentRefund", "goods_return", "SET NULL"),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(model_name="goodsreturnitem", name="paid_bags"),
                migrations.RemoveField(model_name="paymentrefund", name="goods_return"),
            ],
        ),
    ]
