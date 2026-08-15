from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0031_order_purge_tombstone"),
    ]

    operations = [
        # Expand/contract rollout: remove the field from Django state now, but
        # keep the DB column (it already has db_default=True) until every
        # production process and rollback image no longer references it.
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(
                    model_name="order",
                    name="scale_weighing_required",
                ),
            ],
            database_operations=[],
        ),
    ]
