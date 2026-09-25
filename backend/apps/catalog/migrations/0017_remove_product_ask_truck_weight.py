from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0016_remove_product_price"),
    ]

    operations = [
        # Expand/contract rollout, как 0016 и orders 0046: галочку «Спрашивать
        # вес машины при въезде» никто не читает. Поле уходит из состояния
        # Django, колонка остаётся для образа автоотката. NOT NULL колонке
        # сначала нужен db_default — новый код её в INSERT не передаёт.
        migrations.AlterField(
            model_name="product",
            name="ask_truck_weight",
            field=models.BooleanField(db_default=False, default=False),
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(model_name="product", name="ask_truck_weight"),
            ],
            database_operations=[],
        ),
    ]
