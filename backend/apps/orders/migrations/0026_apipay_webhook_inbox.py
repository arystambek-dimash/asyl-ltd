from django.db import migrations, models


INVOICE_EVENTS = {
    "invoice.status_changed",
    "invoice.qr_scanned",
    "invoice.refunded",
}


def _positive_int(value):
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _semantic_key(event_name, payload):
    invoice = payload.get("invoice")
    if event_name not in INVOICE_EVENTS or not isinstance(invoice, dict):
        return None

    if event_name == "invoice.refunded":
        refund = payload.get("refund")
        if not isinstance(refund, dict):
            return None
        refund_id = _positive_int(refund.get("id"))
        refund_status = refund.get("status")
        if refund_id and isinstance(refund_status, str) and refund_status:
            key = f"refund:{refund_id}:{refund_status}"
            return key if len(key) <= 191 else None
        return None

    invoice_id = _positive_int(invoice.get("id"))
    invoice_status = invoice.get("status")
    event_version = payload.get("timestamp")
    if (
        invoice_id
        and isinstance(invoice_status, str)
        and invoice_status
        and isinstance(event_version, str)
        and event_version
    ):
        key = (
            f"invoice:{invoice_id}:{event_name}:"
            f"{invoice_status}:{event_version}"
        )
        return key if len(key) <= 191 else None
    return None


def backfill_webhook_inbox(apps, schema_editor):
    WebhookEvent = apps.get_model("orders", "ApiPayWebhookEvent")
    used_semantic_keys = set()
    for row in WebhookEvent.objects.order_by("created_at", "pk").iterator():
        payload = row.payload if isinstance(row.payload, dict) else {}
        invoice_payload = payload.get("invoice")
        provider_invoice_id = (
            _positive_int(invoice_payload.get("id"))
            if isinstance(invoice_payload, dict)
            else None
        )
        semantic_key = _semantic_key(row.event, payload)
        # Keep every historical audit row. If old raw-body dedupe allowed the
        # same provider state more than once, only the first gets the new
        # semantic uniqueness key.
        if semantic_key in used_semantic_keys:
            semantic_key = None
        elif semantic_key:
            used_semantic_keys.add(semantic_key)
        WebhookEvent.objects.filter(pk=row.pk).update(
            provider_invoice_id=provider_invoice_id,
            semantic_key=semantic_key,
            processed_at=row.created_at,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0025_payment_refunds"),
    ]

    operations = [
        migrations.AddField(
            model_name="apipaywebhookevent",
            name="semantic_key",
            field=models.CharField(
                blank=True, max_length=191, null=True, unique=True
            ),
        ),
        migrations.AddField(
            model_name="apipaywebhookevent",
            name="provider_invoice_id",
            field=models.BigIntegerField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="apipaywebhookevent",
            name="processed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="apipaywebhookevent",
            name="processing_error",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="apipaywebhookevent",
            name="attempt_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="apipaywebhookevent",
            name="next_attempt_at",
            field=models.DateTimeField(
                blank=True, db_index=True, null=True
            ),
        ),
        migrations.AddField(
            model_name="apipayinvoice",
            name="provider_status_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(
            backfill_webhook_inbox, migrations.RunPython.noop
        ),
    ]
