from django.db import migrations, models


def backfill_payment_method(apps, schema_editor):
    Order = apps.get_model("orders", "Order")
    Order.objects.filter(settlement_intent="instant").update(payment_method="invoice")


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0015_payment_note"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="payment_method",
            field=models.CharField(default="debt", max_length=10),
        ),
        migrations.RunPython(backfill_payment_method, migrations.RunPython.noop),
    ]
