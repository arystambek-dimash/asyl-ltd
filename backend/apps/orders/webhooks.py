"""Public webhook receiver and durable inbox for ApiPay."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import timedelta
from decimal import InvalidOperation

from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .apipay import (
    apply_invoice_status,
    apply_refund_status,
    invoice_department_code,
    parse_provider_datetime,
    positive_provider_id,
    recover_invoice_mapping_from_payload,
)
from .models import ApiPayInvoice, ApiPayQrRefund, ApiPayWebhookEvent
from .qr_refunds import apply_qr_refund_snapshot, auto_execute_qr_refund
from apps.sales.models import Department

logger = logging.getLogger(__name__)

MAX_WEBHOOK_BODY_BYTES = 64 * 1024
# Сколько событий из журнала применяет один проход сверки.
WEBHOOK_REPLAY_BATCH = 100
WEBHOOK_RETRY_BASE_SECONDS = 5
WEBHOOK_RETRY_MAX_SECONDS = 60 * 60
# После стольких попыток (около полусуток) событие ждёт разбора, а не повтора:
# оно остаётся в журнале и повторяется раз в час, но уже не считается сбоем
# сверки и не роняет её здоровье.
WEBHOOK_MAX_RETRYABLE_ATTEMPTS = 20
# Повтор не исправит событие, подписанное ключом чужого отдела.
WEBHOOK_UNRECOVERABLE_ERRORS = frozenset({
    "invoice_department_mismatch",
    "qr_refund_department_mismatch",
})
INVOICE_EVENTS = frozenset({
    "invoice.status_changed",
    "invoice.qr_scanned",
    "invoice.refunded",
})
# Возврат по Kaspi QR через ссылку покупателю: в событии нет счёта, только сессия.
QR_REFUND_EVENTS = frozenset({
    "qr_refund.identified",
    "qr_refund.completed",
    "qr_refund.expired",
    "qr_refund.failed",
    "qr_refund.execution_uncertain",
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


def _departments_with_secret():
    return Department.objects.exclude(
        apipay_webhook_secret_encrypted=""
    ).order_by("created_at", "id")


def _department_for_signature(
    raw_body: bytes, signature: str
) -> Department | None:
    """Отдел, чьим секретом подписано событие; None — не подошёл ни один.

    Адрес вебхука один на всех, а секрет у каждого ключа ApiPay свой:
    совпавший секрет и есть маппинг события на отдел.
    """
    for department in _departments_with_secret():
        if verify_signature(raw_body, signature, department.apipay_webhook_secret):
            return department
    return None


def _same_department(invoice_record: ApiPayInvoice, department: Department) -> bool:
    return invoice_department_code(invoice_record) == department.code


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
    parsed = parse_provider_datetime(value)
    if parsed is None:
        raise ValueError("event.timestamp is invalid")
    return parsed


def _defer_locked_event(
    event: ApiPayWebhookEvent,
    error: str,
    *,
    invoice: ApiPayInvoice | None = None,
) -> str:
    """Отложить событие; вернуть исход для статистики: failed или rejected."""
    event.attempt_count += 1
    delay_seconds = min(
        WEBHOOK_RETRY_BASE_SECONDS * (2 ** min(event.attempt_count - 1, 10)),
        WEBHOOK_RETRY_MAX_SECONDS,
    )
    event.processing_error = error
    event.next_attempt_at = timezone.now() + timedelta(seconds=delay_seconds)
    update_fields = ["attempt_count", "processing_error", "next_attempt_at"]
    if invoice is not None:
        event.invoice = invoice
        update_fields.append("invoice")
    event.save(update_fields=update_fields)
    if (
        error in WEBHOOK_UNRECOVERABLE_ERRORS
        or event.attempt_count >= WEBHOOK_MAX_RETRYABLE_ATTEMPTS
    ):
        return "rejected"
    return "failed"


def _mark_processed(
    event: ApiPayWebhookEvent,
    *,
    invoice: ApiPayInvoice | None = None,
    provider_invoice_id: int | None = None,
) -> str:
    event.processed_at = timezone.now()
    event.processing_error = ""
    event.next_attempt_at = None
    update_fields = ["processed_at", "processing_error", "next_attempt_at"]
    if invoice is not None:
        event.invoice = invoice
        update_fields.append("invoice")
    if provider_invoice_id is not None:
        event.provider_invoice_id = provider_invoice_id
        update_fields.append("provider_invoice_id")
    event.save(update_fields=update_fields)
    return "processed"


def _defer_event(event_id: int, error: str) -> str:
    with transaction.atomic():
        event = ApiPayWebhookEvent.objects.select_for_update().get(pk=event_id)
        if event.processed_at is None:
            return _defer_locked_event(event, error)
    return "failed"


def _positive_int(value: object, error_code: str) -> int:
    try:
        return positive_provider_id(value)
    except ValueError as exc:
        raise WebhookPayloadError(error_code) from exc


def _event_metadata(
    payload: dict, event_name: str
) -> tuple[int | None, str | None]:
    """Validate routing fields and build ApiPay's documented dedupe key."""
    if event_name in QR_REFUND_EVENTS:
        qr_refund = payload.get("qr_refund")
        if not isinstance(qr_refund, dict):
            raise WebhookPayloadError("qr_refund_required")
        session_id = _positive_int(qr_refund.get("id"), "qr_refund_id_required")
        qr_status = qr_refund.get("status")
        if not isinstance(qr_status, str) or not qr_status or len(qr_status) > 40:
            raise WebhookPayloadError("qr_refund_status_required")
        return None, f"qr_refund:{session_id}:{qr_status}"
    if event_name not in INVOICE_EVENTS:
        return None, None

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
    return provider_invoice_id, semantic_key


def _apply_event(
    event_name: str,
    invoice_record: ApiPayInvoice,
    payload: dict,
    invoice_payload: dict,
) -> None:
    # For invoice.refunded the envelope is also authoritative proof that the
    # original invoice was paid. Apply gross money first so a missed paid
    # webhook cannot leave the refund attached to an unconfirmed Payment.
    apply_invoice_status(
        invoice_record,
        invoice_payload,
        observed_at=_event_observed_at(payload),
    )
    if event_name == "invoice.refunded":
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
        if event.event in QR_REFUND_EVENTS:
            return _replay_qr_refund_event(event)
        if event.event not in INVOICE_EVENTS:
            return _mark_processed(event)

        invoice_payload = event.payload.get("invoice")
        if not isinstance(invoice_payload, dict):
            return _defer_locked_event(event, "invoice_required")
        provider_invoice_id = event.provider_invoice_id
        if provider_invoice_id is None:
            try:
                provider_invoice_id = _positive_int(
                    invoice_payload.get("id"), "invoice_id_required"
                )
            except WebhookPayloadError as exc:
                return _defer_locked_event(event, exc.code)

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
        if event.department_id is not None and not _same_department(
            invoice_record, event.department
        ):
            # Счёт нашёлся позже, но принадлежит другому отделу: событие
            # подписано чужим секретом и деньги по нему не применяются.
            return _defer_locked_event(
                event, "invoice_department_mismatch", invoice=invoice_record
            )

        try:
            _apply_event(
                event.event, invoice_record, event.payload, invoice_payload
            )
        except WEBHOOK_PROCESSING_ERRORS as exc:
            return _defer_locked_event(event, str(exc), invoice=invoice_record)

        return _mark_processed(
            event,
            invoice=invoice_record,
            provider_invoice_id=provider_invoice_id,
        )


def _replay_qr_refund_event(event: ApiPayWebhookEvent) -> str:
    """Применить событие QR-возврата под блокировкой события (вызывается из транзакции)."""
    qr_payload = event.payload.get("qr_refund") or {}
    session = (
        ApiPayQrRefund.objects.select_related("invoice__payment__order")
        .filter(session_id=qr_payload.get("id"))
        .first()
    )
    if session is None:
        _defer_locked_event(event, "waiting_for_qr_refund")
        return "waiting_for_invoice"
    if event.department_id is not None and not _same_department(session.invoice, event.department):
        return _defer_locked_event(event, "qr_refund_department_mismatch")
    try:
        needs_execute = apply_qr_refund_snapshot(session.pk, qr_payload, source="webhook")
    except WEBHOOK_PROCESSING_ERRORS as exc:
        return _defer_locked_event(event, str(exc))
    if needs_execute:
        # Денежный запрос — только после коммита события и вне его блокировки.
        transaction.on_commit(lambda: _auto_execute_safely(session.pk))
    return _mark_processed(event, invoice=session.invoice)


def _auto_execute_safely(session_pk: int) -> None:
    try:
        auto_execute_qr_refund(session_pk)
    except Exception:  # сверка повторит выбор покупки
        logger.exception("ApiPay QR refund auto-execute failed session_pk=%s", session_pk)


def replay_pending_apipay_webhooks(
    *,
    provider_invoice_id: int | None = None,
    event_id: int | None = None,
) -> dict[str, int]:
    """Resolve and apply stored inbox events.

    The function is intentionally public so reconciliation jobs can invoke it;
    invoice mapping in ``apipay`` calls it to close the invoice-creation race.
    Each event is independently locked and committed, so one malformed event
    cannot roll back other provider notifications.
    """
    stats = {
        "processed": 0,
        "already_processed": 0,
        "waiting_for_invoice": 0,
        "failed": 0,
        "rejected": 0,
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
        .values_list("pk", flat=True)[:WEBHOOK_REPLAY_BATCH]
    )
    for pending_event_id in event_ids:
        try:
            outcome = _replay_one_webhook(pending_event_id)
        except Exception as exc:  # defensive observability
            logger.exception(
                "Unexpected error replaying ApiPay webhook %s",
                pending_event_id,
            )
            outcome = "failed"
            try:
                outcome = _defer_event(pending_event_id, str(exc))
            except Exception:
                logger.exception(
                    "Unable to defer failed ApiPay webhook %s",
                    pending_event_id,
                )
        stats[outcome] += 1
    return stats


@csrf_exempt
@require_POST
def apipay_webhook(request: HttpRequest) -> JsonResponse:
    raw_body = request.body
    if len(raw_body) > MAX_WEBHOOK_BODY_BYTES:
        return JsonResponse({"error": "payload_too_large"}, status=413)

    if not _departments_with_secret().exists():
        return JsonResponse({"error": "webhook_not_configured"}, status=503)
    signature = request.headers.get("X-Webhook-Signature", "")
    department = _department_for_signature(raw_body, signature)
    if department is None:
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
        provider_invoice_id, semantic_key = _event_metadata(payload, event_name)
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
        if invoice_record is not None and not _same_department(
            invoice_record, department
        ):
            # Подпись отдела A по счёту отдела B: чужой ключ не может двигать
            # деньги этого счёта. Сверка ключом отдела B приведёт его в порядок.
            logger.warning(
                "ApiPay webhook signed by department=%s for invoice=%s of another department",
                department.code,
                provider_invoice_id,
            )
            return JsonResponse(
                {"error": "invoice_department_mismatch"}, status=403
            )
        try:
            with transaction.atomic():
                webhook_event = ApiPayWebhookEvent.objects.create(
                    body_sha256=body_sha256,
                    semantic_key=semantic_key,
                    event=event_name,
                    provider_invoice_id=provider_invoice_id,
                    invoice=invoice_record,
                    department=department,
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
    except Exception:  # durable async fallback
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
