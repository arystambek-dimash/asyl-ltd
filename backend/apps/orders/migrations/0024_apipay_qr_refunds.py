from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0023_apipay_invoice_and_webhook_event"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="apipayinvoice",
            name="channel",
            field=models.CharField(default="phone", max_length=16),
        ),
        migrations.AddField(
            model_name="apipayinvoice",
            name="phone_number",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
        migrations.AddField(
            model_name="apipayinvoice",
            name="qr_token_url",
            field=models.URLField(blank=True, default="", max_length=1000),
        ),
        migrations.AddField(
            model_name="apipayinvoice",
            name="qr_image_url",
            field=models.URLField(blank=True, default="", max_length=1000),
        ),
        migrations.AddField(
            model_name="apipayinvoice",
            name="qr_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="apipayinvoice",
            name="total_refunded",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.CreateModel(
            name="ApiPayRefund",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("refund_id", models.BigIntegerField(unique=True)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=12)),
                ("status", models.CharField(default="pending", max_length=20)),
                ("reason", models.CharField(blank=True, default="", max_length=500)),
                ("kaspi_refund_id", models.CharField(blank=True, default="", max_length=100)),
                ("error_code", models.CharField(blank=True, default="", max_length=100)),
                ("error_message", models.TextField(blank=True, default="")),
                ("response_payload", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("invoice", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="refunds", to="orders.apipayinvoice")),
                ("requested_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="requested_apipay_refunds", to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
