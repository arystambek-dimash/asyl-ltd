from collections import defaultdict
from decimal import Decimal

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def backfill_refunded_amount(apps, schema_editor):
    ApiPayRefund = apps.get_model("orders", "ApiPayRefund")
    Payment = apps.get_model("orders", "Payment")
    PaymentRefund = apps.get_model("orders", "PaymentRefund")
    totals = defaultdict(
        lambda: {"completed": Decimal("0"), "pending": Decimal("0")}
    )
    for provider_refund in ApiPayRefund.objects.select_related("invoice").iterator():
        status = provider_refund.status
        if status in {"pending", "processing"}:
            status = "pending"
        generic_refund = PaymentRefund.objects.create(
            payment_id=provider_refund.invoice.payment_id,
            provider_refund_id=provider_refund.id,
            amount=provider_refund.amount,
            method="apipay",
            status=status,
            reason=provider_refund.reason,
            requested_by_id=provider_refund.requested_by_id,
            completed_at=(
                provider_refund.updated_at if status == "completed" else None
            ),
        )
        PaymentRefund.objects.filter(pk=generic_refund.pk).update(
            created_at=provider_refund.created_at,
            updated_at=provider_refund.updated_at,
        )
        if status in totals[generic_refund.payment_id]:
            totals[generic_refund.payment_id][status] += provider_refund.amount
    for payment_id, values in totals.items():
        Payment.objects.filter(pk=payment_id).update(
            refunded_amount=values["completed"],
            pending_refund_amount=values["pending"],
        )


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0024_apipay_qr_refunds"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="payment",
            name="refunded_amount",
            field=models.DecimalField(
                decimal_places=2, default=0, max_digits=12
            ),
        ),
        migrations.AddField(
            model_name="payment",
            name="pending_refund_amount",
            field=models.DecimalField(
                decimal_places=2, default=0, max_digits=12
            ),
        ),
        migrations.CreateModel(
            name="PaymentRefund",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("amount", models.DecimalField(decimal_places=2, max_digits=12)),
                ("method", models.CharField(max_length=20)),
                ("status", models.CharField(default="pending", max_length=20)),
                ("reason", models.CharField(max_length=500)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("payment", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="payment_refunds", to="orders.payment")),
                ("provider_refund", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="payment_refund", to="orders.apipayrefund")),
                ("requested_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="requested_payment_refunds", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.RunPython(
            backfill_refunded_amount, migrations.RunPython.noop
        ),
    ]
