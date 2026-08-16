from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("clients", "0009_dynamic_departments"),
        # orders.0010 still reads Client.department while backfilling legacy
        # orders.  On a fresh database Django may otherwise remove the field
        # here before that cross-app data migration gets its turn.
        ("orders", "0010_payment_chain_backfill"),
    ]

    operations = [
        migrations.RemoveField(model_name="client", name="department"),
        migrations.RemoveField(model_name="client", name="manager"),
    ]
