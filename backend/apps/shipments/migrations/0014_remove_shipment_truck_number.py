from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("shipments", "0013_remove_shipmentwagon_source_message"),
    ]

    operations = [
        # Expand/contract rollout, как 0013: номер машины живёт только в
        # Order.truck_number, копия уходит из состояния Django. NOT NULL
        # колонка остаётся для образа автоотката, поэтому ей нужен db_default —
        # новый код её в INSERT не передаёт.
        migrations.AlterField(
            model_name="shipment",
            name="truck_number",
            field=models.CharField(blank=True, db_default="", default="", max_length=30),
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(model_name="shipment", name="truck_number"),
            ],
            database_operations=[],
        ),
    ]
