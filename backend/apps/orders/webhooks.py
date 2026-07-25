"""Public webhook receiver and durable inbox for ApiPay."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import timedelta
from decimal import InvalidOperation

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .apipay import (
    apply_invoice_status,
    apply_refund_status,
    recover_invoice_mapping_from_payload,
)
from .models import ApiPayInvoice, ApiPayWebhookEvent

logger = logging.getLogger(__name__)

MAX_WEBHOOK_BODY_BYTES = 64 * 1024
WEBHOOK_RETRY_BASE_SECONDS = 5
WEBHOOK_RETRY_MAX_SECONDS = 60 * 60
INVOICE_EVENTS = frozenset({
    "invoice.status_changed",
    "invoice.qr_scanned",
    "invoice.refunded",
})
INVOICE_STATUS_EVENTS = frozenset({
    "invoice.status_changed",
    "invoice.qr_scanned",
})
WEBHOOK_PROCESSING_ERRORS = (
    KeyError,
    TypeError,
    ValueError,
    InvalidOperation,
)


class WebhookPayloadError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def verify_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    if not secret or not signature:
        return False
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def _event_observed_at(payload: dict):
    value = payload.get("timestamp")
    if value in (None, ""):
        # Older/test integrations may omit it; the transition matrix still
        # protects terminal money states in that case.
        return None
    if not isinstance(value, str):
        raise TypeError("event.timestamp is invalid")
    parsed = parse_datetime(value)
    if parsed is None:
        raise ValueError("event.timestamp is invalid")
    return parsed if timezone.is_aware(parsed) else timezone.make_aware(parsed)


def _defer_locked_event(
    event: ApiPayWebhookEvent, error: str
) -> None:
    event.attempt_count += 1
    delay_seconds = min(
        WEBHOOK_RETRY_BASE_SECONDS * (2 ** min(event.attempt_count - 1, 10)),
        WEBHOOK_RETRY_MAX_SECONDS,
    )
    event.processing_error = error
    event.next_attempt_at = timezone.now() + timedelta(seconds=delay_seconds)
    event.save(update_fields=[
        "attempt_count", "processing_error", "next_attempt_at",
    ])


def _defer_event(event_id: int, error: str) -> None:
    with transaction.atomic():
        event = ApiPayWebhookEvent.objects.select_for_update().get(pk=event_id)
        if event.processed_at is None:
            _defer_locked_event(event, error)


def _positive_int(value: object, error_code: str) -> int:
    if isinstance(value, bool):
        raise WebhookPayloadError(error_code)
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise WebhookPayloadError(error_code) from exc
    if result <= 0:
        raise WebhookPayloadError(error_code)
    return result


def _event_metadata(
    payload: dict, event_name: str
) -> tuple[dict | None, int | None, str | None]:
    """Validate routing fields and build ApiPay's documented dedupe key."""
    if event_name not in INVOICE_EVENTS:
        return None, None, None

    invoice_payload = payload.get("invoice")
    if not isinstance(invoice_payload, dict):
        raise WebhookPayloadError("invoice_required")
    provider_invoice_id = _positive_int(
        invoice_payload.get("id"), "invoice_id_required"
    )

    if event_name == "invoice.refunded":
        refund_payload = payload.get("refund")
        if not isinstance(refund_payload, dict):
            raise WebhookPayloadError("refund_required")
        refund_id = _positive_int(
            refund_payload.get("id"), "refund_id_required"
        )
        refund_status = refund_payload.get("status")
        semantic_key = (
            f"refund:{refund_id}:{refund_status}"
            if isinstance(refund_status, str) and refund_status
            else None
        )
    else:
        invoice_status = invoice_payload.get("status")
        if not isinstance(invoice_status, str) or not invoice_status:
            raise WebhookPayloadError("invoice_status_required")
        # A status can legitimately recur (error -> pending) and qr_scanned
        # shares pending with status_changed. ApiPay's required event timestamp
        # is the transition version; exact retries keep the same version.
        event_version = payload.get("timestamp")
        semantic_key = (
            f"invoice:{provider_invoice_id}:{event_name}:"
            f"{invoice_status}:{event_version}"
            if isinstance(event_version, str) and event_version
            else None
        )

    if semantic_key is not None and len(semantic_key) > 191:
        # Provider statuses are short documented enums. Reject an oversized
        # untrusted value instead of allowing a database truncation collision.
        raise WebhookPayloadError("invalid_status")
    return invoice_payload, provider_invoice_id, semantic_key


def _apply_event(
    event_name: str,
    invoice_record: ApiPayInvoice,
    payload: dict,
    invoice_payload: dict,
) -> None:
    observed_at = _event_observed_at(payload)
    if event_name in INVOICE_STATUS_EVENTS:
        apply_invoice_status(
            invoice_record, invoice_payload, observed_at=observed_at
        )
    elif event_name == "invoice.refunded":
        # The refund envelope is also authoritative proof that the original
        # invoice was paid. Apply gross money first so a missed paid webhook
        # cannot leave the refund attached to an unconfirmed Payment.
        apply_invoice_status(
            invoice_record, invoice_payload, observed_at=observed_at
        )
        apply_refund_status(invoice_record, payload["refund"])


def _replay_one_webhook(event_id: int) -> str:
    """Process one inbox row under a lock; safe to call concurrently."""
    with transaction.atomic():
        event = (
            ApiPayWebhookEvent.objects.select_for_update()
            .get(pk=event_id)
        )
        if event.processed_at is not None:
            return "already_processed"
        if event.event not in INVOICE_EVENTS:
            event.processed_at = timezone.now()
            event.processing_error = ""
            event.next_attempt_at = None
            event.save(update_fields=[
                "processed_at", "processing_error", "next_attempt_at",
            ])
            return "processed"

        invoice_payload = event.payload.get("invoice")
        if not isinstance(invoice_payload, dict):
            _defer_locked_event(event, "invoice_required")
            return "failed"
        provider_invoice_id = event.provider_invoice_id
        if provider_invoice_id is None:
            try:
                provider_invoice_id = _positive_int(
                    invoice_payload.get("id"), "invoice_id_required"
                )
            except WebhookPayloadError as exc:
                _defer_locked_event(event, exc.code)
                return "failed"

        invoice_record = ApiPayInvoice.objects.filter(
            invoice_id=provider_invoice_id
        ).first()
        if invoice_record is None:
            invoice_record = recover_invoice_mapping_from_payload(
                invoice_payload
            )
        if invoice_record is None:
            # This is the expected create-response/webhook race, not an error.
            _defer_locked_event(event, "waiting_for_invoice")
            return "waiting_for_invoice"

        try:
            _apply_event(
                event.event, invoice_record, event.payload, invoice_payload
            )
        except WEBHOOK_PROCESSING_ERRORS as exc:
            event.invoice = invoice_record
            _defer_locked_event(event, str(exc))
            event.save(update_fields=["invoice"])
            return "failed"

        event.invoice = invoice_record
        event.provider_invoice_id = provider_invoice_id
        event.processed_at = timezone.now()
        event.processing_error = ""
        event.next_attempt_at = None
        event.save(update_fields=[
            "invoice",
            "provider_invoice_id",
            "processed_at",
            "processing_error",
            "next_attempt_at",
        ])
        return "processed"


def replay_pending_apipay_webhooks(
    *,
    provider_invoice_id: int | None = None,
    event_id: int | None = None,
    limit: int = 100,
) -> dict[str, int]:
    """Resolve and apply stored inbox events.

    The function is intentionally public so reconciliation jobs can invoke it,
    while the post-save hook below closes the normal invoice-creation race.
    Each event is independently locked and committed, so one malformed event
    cannot roll back other provider notifications.
    """
    stats = {
        "processed": 0,
        "already_processed": 0,
        "waiting_for_invoice": 0,
        "failed": 0,
    }
    queryset = ApiPayWebhookEvent.objects.filter(processed_at__isnull=True)
    explicit_retry = provider_invoice_id is not None or event_id is not None
    if provider_invoice_id is not None:
        queryset = queryset.filter(provider_invoice_id=provider_invoice_id)
    if event_id is not None:
        queryset = queryset.filter(pk=event_id)
    if not explicit_retry:
        due_at = timezone.now()
        queryset = queryset.filter(
            Q(next_attempt_at__isnull=True)
            | Q(next_attempt_at__lte=due_at)
        )
    event_ids = list(
        queryset.order_by(
            F("next_attempt_at").asc(nulls_first=True),
            "created_at",
            "pk",
        )
        .values_list("pk", flat=True)[:max(1, min(limit, 1000))]
    )
    for pending_event_id in event_ids:
        try:
            outcome = _replay_one_webhook(pending_event_id)
        except Exception as exc:  # pragma: no cover - defensive observability
            logger.exception(
                "Unexpected error replaying ApiPay webhook %s",
                pending_event_id,
            )
            try:
                _defer_event(pending_event_id, str(exc))
            except Exception:
                logger.exception(
                    "Unable to defer failed ApiPay webhook %s",
                    pending_event_id,
                )
            outcome = "failed"
        stats[outcome] += 1
    return stats


def _replay_safely(
    *, provider_invoice_id: int | None = None, event_id: int | None = None
) -> None:
    try:
        replay_pending_apipay_webhooks(
            provider_invoice_id=provider_invoice_id,
            event_id=event_id,
        )
    except Exception:  # pragma: no cover - database-level last resort
        logger.exception(
            "Unable to start ApiPay webhook replay (invoice=%s, event=%s)",
            provider_invoice_id,
            event_id,
        )


@receiver(
    post_save,
    sender=ApiPayInvoice,
    dispatch_uid="orders.replay_apipay_webhooks_after_invoice_mapping",
)
def replay_webhooks_after_invoice_mapping(
    sender, instance: ApiPayInvoice, created: bool, update_fields=None, **kwargs
) -> None:
    """Apply a webhook that beat persistence of the provider invoice ID."""
    del sender, kwargs
    if instance.invoice_id is None:
        return
    if not created and update_fields is not None and "invoice_id" not in update_fields:
        return

    # Synchronous replay sees changes on this connection (and keeps invoice
    # creation + payment transition atomic when the caller uses a transaction).
    _replay_safely(provider_invoice_id=instance.invoice_id)
    # The second attempt closes the cross-connection ordering where the
    # webhook inbox transaction commits just after the invoice mapping.
    provider_invoice_id = instance.invoice_id
    transaction.on_commit(
        lambda: _replay_safely(provider_invoice_id=provider_invoice_id)
    )


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
    if (
        not isinstance(event_name, str)
        or not event_name
        or len(event_name) > 100
    ):
        return JsonResponse({"error": "event_required"}, status=400)
    try:
        (
            _invoice_payload,
            provider_invoice_id,
            semantic_key,
        ) = _event_metadata(payload, event_name)
    except WebhookPayloadError as exc:
        return JsonResponse({"error": exc.code}, status=400)

    body_sha256 = hashlib.sha256(raw_body).hexdigest()
    duplicate_query = Q(body_sha256=body_sha256)
    if semantic_key is not None:
        duplicate_query |= Q(semantic_key=semantic_key)
    webhook_event = (
        ApiPayWebhookEvent.objects.filter(duplicate_query)
        .order_by("pk")
        .first()
    )
    duplicate = webhook_event is not None

    # Persist the verified raw event in its own transaction before touching any
    # money state. A validation/locking failure during apply must never erase
    # the only durable evidence ApiPay sent us.
    if webhook_event is None:
        invoice_record = None
        if provider_invoice_id is not None:
            invoice_record = ApiPayInvoice.objects.filter(
                invoice_id=provider_invoice_id
            ).first()
        try:
            with transaction.atomic():
                webhook_event = ApiPayWebhookEvent.objects.create(
                    body_sha256=body_sha256,
                    semantic_key=semantic_key,
                    event=event_name,
                    provider_invoice_id=provider_invoice_id,
                    invoice=invoice_record,
                    payload=payload,
                )
        except IntegrityError:
            # A concurrent identical delivery may have committed first.
            webhook_event = (
                ApiPayWebhookEvent.objects.filter(duplicate_query)
                .order_by("pk")
                .first()
            )
            if webhook_event is None:
                logger.exception(
                    "Database error storing ApiPay webhook event=%s invoice=%s",
                    event_name,
                    provider_invoice_id,
                )
                return JsonResponse(
                    {"error": "webhook_processing_failed"}, status=500
                )
            duplicate = True

    # Explicit event retry bypasses backoff. It also closes the cross-connection
    # create-response/webhook race after the inbox transaction has committed.
    try:
        replay_pending_apipay_webhooks(event_id=webhook_event.pk)
    except Exception:  # pragma: no cover - durable async fallback
        logger.exception(
            "Unable to start immediate ApiPay webhook apply event_id=%s",
            webhook_event.pk,
        )
    webhook_event.refresh_from_db(fields=["processed_at"])
    queued = webhook_event.processed_at is None
    response = {"ok": True}
    if duplicate:
        response["duplicate"] = True
    if queued:
        response["queued"] = True
    return JsonResponse(response)
