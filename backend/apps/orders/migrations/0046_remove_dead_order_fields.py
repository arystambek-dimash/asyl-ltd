from django.db import migrations, models

from apps.common.migration_ops import db_on_delete


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0045_remove_order_review_fields"),
    ]

    operations = [
        # Expand/contract rollout, как 0032 и 0045: поля уходят из состояния
        # Django, колонки остаются для образа автоотката. NOT NULL колонкам
        # сначала нужен db_default — новый код их в INSERT не передаёт.
        migrations.AlterField(
            model_name="order",
            name="debt_override",
            field=models.BooleanField(db_default=False, default=False),
        ),
        migrations.AlterField(
            model_name="orderitem",
            name="product_ask_truck_weight_snapshot",
            field=models.BooleanField(db_default=False, default=False),
        ),
        migrations.AlterField(
            model_name="apipayinvoice",
            name="total_refunded",
            field=models.DecimalField(
                db_default=0, decimal_places=2, default=0, max_digits=12
            ),
        ),
        # Ключ остаётся в базе: удаление сотрудника обнуляет его сама база,
        # ORM про поле больше не знает.
        db_on_delete("orders", "Order", "debt_override_by", "SET NULL"),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(model_name="order", name="debt_override"),
                migrations.RemoveField(model_name="order", name="debt_override_by"),
                migrations.RemoveField(
                    model_name="orderitem",
                    name="product_ask_truck_weight_snapshot",
                ),
                migrations.RemoveField(model_name="apipayinvoice", name="total_refunded"),
            ],
            database_operations=[],
        ),
    ]
