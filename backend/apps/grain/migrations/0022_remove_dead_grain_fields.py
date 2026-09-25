from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("grain", "0021_wagon_workflow_default_simple"),
    ]

    operations = [
        # Expand/contract rollout, как orders 0045/0046: мёртвые поля уходят из
        # состояния Django, колонки остаются для образа автоотката. NOT NULL
        # колонкам сначала нужен db_default — новый код их в INSERT не передаёт.
        migrations.AlterField(
            model_name="grainsettings",
            name="sensor_warning_percent",
            field=models.DecimalField(
                db_default=5, decimal_places=2, default=5, max_digits=5
            ),
        ),
        migrations.AlterField(
            model_name="grainsupply",
            name="contract",
            field=models.CharField(
                blank=True, db_default="", default="", max_length=200
            ),
        ),
        migrations.AlterField(
            model_name="labcheck",
            name="infestation",
            field=models.BooleanField(db_default=False, default=False),
        ),
        migrations.AlterField(
            model_name="labcheck",
            name="damage",
            field=models.CharField(
                blank=True, db_default="", default="", max_length=300
            ),
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(
                    model_name="grainsettings", name="sensor_warning_percent"
                ),
                migrations.RemoveField(model_name="silo", name="sensor_estimated_kg"),
                migrations.RemoveField(model_name="grainsupply", name="contract"),
                migrations.RemoveField(model_name="grainsupply", name="expected_date"),
                migrations.RemoveField(
                    model_name="grainsupply", name="document_weight_kg"
                ),
                migrations.RemoveField(
                    model_name="grainsupply", name="wagons_expected"
                ),
                migrations.RemoveField(
                    model_name="automaticpassagecapture",
                    name="orientation_confidence",
                ),
                migrations.RemoveField(model_name="labcheck", name="nature"),
                migrations.RemoveField(model_name="labcheck", name="infestation"),
                migrations.RemoveField(model_name="labcheck", name="damage"),
            ],
            database_operations=[],
        ),
    ]
