from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("clients", "0017_backfill_unambiguous_departments"),
    ]

    operations = [
        # Expand/contract rollout: поле уходит из состояния Django, nullable
        # колонка остаётся в базе для образа автоотката.
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(model_name="store", name="contract_signed_at"),
            ],
            database_operations=[],
        ),
    ]
