from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0022_order_repeated_from"),
    ]

    operations = [
        migrations.CreateModel(
            name="ApiPayInvoice",
            fields=[
                ("id", models.BigAutoField(
                    auto_created=True, primary_key=True, serialize=False,
                    verbose_name="ID",
                )),
                ("invoice_id", models.BigIntegerField(
                    blank=True, null=True, unique=True,
                )),
                ("idempotency_key", models.CharField(
                    max_length=191, unique=True,
                )),
                ("status", models.CharField(max_length=32, default="creating")),
                ("error_code", models.CharField(
                    blank=True, default="", max_length=100,
                )),
                ("error_message", models.TextField(blank=True, default="")),
                ("response_payload", models.JSONField(blank=True, default=dict)),
                ("paid_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("payment", models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="apipay_invoice",
                    to="orders.payment",
                )),
            ],
        ),
        migrations.CreateModel(
            name="ApiPayWebhookEvent",
            fields=[
                ("id", models.BigAutoField(
                    auto_created=True, primary_key=True, serialize=False,
                    verbose_name="ID",
                )),
                ("body_sha256", models.CharField(max_length=64, unique=True)),
                ("event", models.CharField(max_length=100)),
                ("payload", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("invoice", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="webhook_events",
                    to="orders.apipayinvoice",
                )),
            ],
        ),
    ]
