"""Public webhook receiver for ApiPay."""

from __future__ import annotations

import hashlib
import hmac
import json

from django.conf import settings
from django.db import IntegrityError, transaction
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .apipay import apply_invoice_status, apply_refund_status
from .models import ApiPayInvoice, ApiPayWebhookEvent


MAX_WEBHOOK_BODY_BYTES = 64 * 1024


def verify_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    if not secret or not signature:
        return False
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


@csrf_exempt
@require_POST
def apipay_webhook(request: HttpRequest) -> JsonResponse:
    raw_body = request.body
    if len(raw_body) > MAX_WEBHOOK_BODY_BYTES:
        return JsonResponse({"error": "payload_too_large"}, status=413)

    secret = settings.APIPAY_WEBHOOK_SECRET
    if not secret:
        return JsonResponse({"error": "webhook_not_configured"}, status=503)
    signature = request.headers.get("X-Webhook-Signature", "")
    if not verify_signature(raw_body, signature, secret):
        return JsonResponse({"error": "invalid_signature"}, status=401)

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({"error": "invalid_json"}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"error": "invalid_payload"}, status=400)

    event_name = payload.get("event")
    if not isinstance(event_name, str) or not event_name:
        return JsonResponse({"error": "event_required"}, status=400)

    body_sha256 = hashlib.sha256(raw_body).hexdigest()
    if ApiPayWebhookEvent.objects.filter(body_sha256=body_sha256).exists():
        return JsonResponse({"ok": True, "duplicate": True})

    invoice_record = None
    invoice_payload = payload.get("invoice")
    if event_name in ("invoice.status_changed", "invoice.qr_scanned", "invoice.refunded"):
        if not isinstance(invoice_payload, dict):
            return JsonResponse({"error": "invoice_required"}, status=400)
        try:
            invoice_id = int(invoice_payload["id"])
        except (KeyError, TypeError, ValueError):
            return JsonResponse({"error": "invoice_id_required"}, status=400)
        invoice_record = ApiPayInvoice.objects.filter(
            invoice_id=invoice_id
        ).first()
        if invoice_record is None:
            return JsonResponse({"error": "invoice_not_found"}, status=404)

    try:
        with transaction.atomic():
            if event_name in ("invoice.status_changed", "invoice.qr_scanned"):
                try:
                    apply_invoice_status(invoice_record, invoice_payload)
                except ValueError as exc:
                    return JsonResponse(
                        {"error": "invalid_invoice", "detail": str(exc)}, status=400
                    )
            elif event_name == "invoice.refunded":
                refund_payload = payload.get("refund")
                if not isinstance(refund_payload, dict):
                    return JsonResponse({"error": "refund_required"}, status=400)
                try:
                    apply_refund_status(invoice_record, refund_payload)
                except (KeyError, TypeError, ValueError) as exc:
                    return JsonResponse(
                        {"error": "invalid_refund", "detail": str(exc)}, status=400
                    )
            ApiPayWebhookEvent.objects.create(
                body_sha256=body_sha256,
                event=event_name,
                invoice=invoice_record,
                payload=payload,
            )
    except IntegrityError:
        # Two simultaneous retries of the same signed body are both successful.
        return JsonResponse({"ok": True, "duplicate": True})

    return JsonResponse({"ok": True})
