from django.db import migrations

from apps.common.migration_ops import db_on_delete


class Migration(migrations.Migration):
    dependencies = [
        ("shipments", "0012_shipment_report_sent_db_on_delete"),
    ]

    operations = [
        # Expand/contract rollout: поле уходит из состояния Django, nullable
        # колонка остаётся для образа автоотката. Ключ обнуляет сама база —
        # ORM, удаляя сообщение бота, про вагоны больше не знает.
        db_on_delete("shipments", "ShipmentWagon", "source_message", "SET NULL"),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(model_name="shipmentwagon", name="source_message"),
            ],
            database_operations=[],
        ),
    ]
