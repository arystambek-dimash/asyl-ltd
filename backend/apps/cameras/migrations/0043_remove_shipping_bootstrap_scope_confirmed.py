from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("cameras", "0042_remove_retired_monoblock_account"),
    ]

    operations = [
        # Expand/contract: перенос истории отгрузки завершён, подтверждение
        # роли больше никто не пишет и не читает. Колонка nullable и остаётся
        # в базе для образа автоотката.
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(
                    model_name="shippinganalyticsbootstrap",
                    name="scope_confirmed_at",
                ),
            ],
            database_operations=[],
        ),
    ]
