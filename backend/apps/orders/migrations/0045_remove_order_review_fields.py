from django.db import migrations

from apps.common.migration_ops import db_on_delete


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0044_order_rail_station"),
    ]

    operations = [
        # Expand/contract rollout: «взять на рассмотрение» больше нет, поля
        # уходят из состояния Django. Колонки nullable и остаются в базе, пока
        # на них может опираться образ автоотката. Ключ reviewed_by обнуляет
        # сама база: ORM про поле больше не знает.
        db_on_delete("orders", "Order", "reviewed_by", "SET NULL"),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(
                    model_name="order",
                    name="reviewed_at",
                ),
                migrations.RemoveField(
                    model_name="order",
                    name="reviewed_by",
                ),
            ],
            database_operations=[],
        ),
    ]
