from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0026_apipay_webhook_inbox"),
    ]

    operations = [
        migrations.AddField(
            model_name="apipayinvoice",
            name="refund_checked_at",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                null=True,
            ),
        ),
    ]
