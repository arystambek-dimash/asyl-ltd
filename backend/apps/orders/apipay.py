"""Server-side ApiPay client and payment lifecycle integration."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import ValidationError

from apps.eventlog.services import log_event

from .models import (
    ApiPayInvoice, ApiPayRefund, Order, Payment, PaymentRefund,
)
from .services import create_client_payment, reject_payment, sync_payment_status


class ApiPayConfigurationError(RuntimeError):
    pass


@dataclass
class ApiPayAPIError(RuntimeError):
    status_code: int
    error_code: str
    message: str
    payload: dict[str, Any]

    def __str__(self) -> str:
        return self.message


def normalize_phone(value: str) -> str:
    """Return the strict 8XXXXXXXXXX format required by POST /invoices."""
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 11 and digits[0] in ("7", "8"):
        return "8" + digits[1:]
    if len(digits) == 10:
        return "8" + digits
    raise ValidationError({
        "detail": "Для оплаты Kaspi укажите телефон в формате 8XXXXXXXXXX.",
        "code": "invalid_kaspi_phone",
    })


def _credentials() -> tuple[str, str]:
    api_key = settings.APIPAY_API_KEY
    if not api_key:
        raise ApiPayConfigurationError("APIPAY_API_KEY is not configured")
    return api_key, settings.APIPAY_BASE_URL


def api_request(
    method: str, path: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Call ApiPay with the server-side X-API-Key header."""
    api_key, base_url = _credentials()
    body = None
    headers = {
        "Accept": "application/json",
        "X-API-Key": api_key,
    }
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"{base_url}/{path.lstrip('/')}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(
            request, timeout=settings.APIPAY_TIMEOUT_SECONDS
        ) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            error_payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            error_payload = {}
        code = str(
            error_payload.get("error_code")
            or error_payload.get("error")
            or "apipay_error"
        )
        message = str(
            error_payload.get("message")
            or error_payload.get("detail")
            or f"Платёжный сервис вернул HTTP {exc.code}"
        )
        raise ApiPayAPIError(exc.code, code, message, error_payload) from exc
    except (OSError, TimeoutError, urllib.error.URLError) as exc:
        raise ApiPayAPIError(
            503, "apipay_unavailable", "Счёт на оплату временно недоступен", {}
        ) from exc

    if not raw:
        return {}
    try:
        result = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApiPayAPIError(
            502, "invalid_apipay_response", "Платёжный сервис вернул некорректный ответ", {}
        ) from exc
    if not isinstance(result, dict):
        raise ApiPayAPIError(
            502, "invalid_apipay_response", "Платёжный сервис вернул некорректный ответ", {}
        )
    return result


def create_invoice(
    payment: Payment, *, channel: str = "phone", phone_number: str | None = None
) -> ApiPayInvoice:
    """Create or recover an idempotent ApiPay invoice for a payment."""
    record, _ = ApiPayInvoice.objects.get_or_create(
        payment=payment,
        defaults={"idempotency_key": f"asyl-payment-{payment.pk}"},
    )
    if record.invoice_id is not None:
        return record

    order = payment.order
    if order.currency != "KZT":
        raise ValidationError({
            "detail": "Счёт на оплату доступен только в тенге.",
            "code": "apipay_kzt_only",
        })
    phone = normalize_phone(phone_number or order.client.phone) if channel == "phone" else ""
    request_payload = {
        "amount": float(Decimal(payment.amount).quantize(Decimal("0.01"))),
        "description": f"Заказ №{order.pk}",
        "external_order_id": f"order_{order.pk}",
        "external_order_id_idempotency": record.idempotency_key,
    }
    if channel == "phone":
        request_payload["phone_number"] = phone
    path = "/invoices/qr" if channel == "qr" else "/invoices"
    try:
        response = api_request("POST", path, request_payload)
    except ApiPayAPIError as exc:
        if exc.status_code == 409 and exc.error_code == "duplicate_idempotency_key":
            response = {
                "id": exc.payload.get("invoice_id"),
                "status": exc.payload.get("status", "processing"),
            }
        else:
            record.status = "error"
            record.error_code = exc.error_code
            record.error_message = exc.message
            record.response_payload = exc.payload
            record.save(update_fields=[
                "status", "error_code", "error_message",
                "response_payload", "updated_at",
            ])
            raise

    try:
        invoice_id = int(response["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ApiPayAPIError(
            502, "invalid_apipay_response",
            "Платёжный сервис не вернул идентификатор счёта", response,
        ) from exc
    record.invoice_id = invoice_id
    record.status = str(response.get("status") or "processing")
    record.channel = channel
    record.phone_number = phone
    record.qr_token_url = str(response.get("qr_token_url") or "")
    record.qr_image_url = str(response.get("qr_image_url") or "")
    record.qr_expires_at = _parsed_datetime(response.get("qr_expires_at"))
    record.error_code = ""
    record.error_message = ""
    record.response_payload = response
    record.save(update_fields=[
        "invoice_id", "status", "channel", "phone_number", "qr_token_url",
        "qr_image_url", "qr_expires_at", "error_code", "error_message",
        "response_payload", "updated_at",
    ])
    log_event(
        "payment",
        f"Счёт на оплату №{invoice_id} создан для заказа №{order.pk}",
        user=payment.recorded_by,
        order=order,
        payload={
            "action": "apipay_invoice_created",
            "payment_id": payment.pk,
            "apipay_invoice_id": invoice_id,
            "status": record.status,
        },
    )
    return record


def start_order_payment(
    order: Order, user, *, channel: str = "phone", phone_number: str | None = None,
    payment_method: str = "kaspi", amount=None,
) -> ApiPayInvoice:
    """Validate, create the internal payment, then issue the ApiPay invoice."""
    if order.currency != "KZT":
        raise ValidationError({
            "detail": "Счёт на оплату доступен только в тенге.",
            "code": "apipay_kzt_only",
        })
    if channel not in ("phone", "qr"):
        raise ValidationError({"detail": "Выберите QR или оплату по номеру."})
    if channel == "phone":
        normalize_phone(phone_number or order.client.phone)
    if payment_method not in ("kaspi", "invoice"):
        raise ValidationError({"detail": "Недопустимый способ оплаты по счёту."})
    payment = create_client_payment(order, payment_method, user, amount=amount)
    try:
        return create_invoice(payment, channel=channel, phone_number=phone_number)
    except (ApiPayAPIError, ApiPayConfigurationError, ValidationError):
        payment.refresh_from_db()
        if payment.status in Payment.IN_PROGRESS_STATUSES:
            reject_payment(payment, user)
        raise


def get_invoice(invoice_id: int) -> dict[str, Any]:
    return api_request("GET", f"/invoices/{invoice_id}")


def check_invoice_statuses(invoice_ids: list[int]) -> dict[str, Any]:
    return api_request(
        "POST", "/invoices/status/check", {"invoice_ids": invoice_ids}
    )


def cancel_invoice(record: ApiPayInvoice) -> ApiPayInvoice:
    if record.channel == "qr":
        raise ValidationError({
            "detail": (
                "Kaspi не поддерживает отмену активного QR-счёта. "
                "Дождитесь его истечения."
            ),
            "code": "qr_cancel_unsupported",
        })
    if record.invoice_id is None:
        raise ValidationError({
            "detail": "Счёт на оплату ещё не создан.",
            "code": "invoice_not_created",
        })
    response = api_request("POST", f"/invoices/{record.invoice_id}/cancel", {})
    invoice_payload = response.get("invoice")
    if isinstance(invoice_payload, dict):
        record.status = str(invoice_payload.get("status") or "cancelled")
        record.response_payload = invoice_payload
    else:
        record.status = "cancelling"
        record.response_payload = response
    record.save(update_fields=["status", "response_payload", "updated_at"])
    return record


def _validated_refund_amount(payment: Payment, amount: object) -> Decimal:
    raw = payment.available_for_refund if amount in (None, "") else amount
    try:
        value = Decimal(str(raw)).quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise ValidationError({"detail": "Некорректная сумма возврата."}) from exc
    if value <= 0:
        raise ValidationError({"detail": "Сумма возврата должна быть больше нуля."})
    if value > payment.available_for_refund:
        raise ValidationError({
            "detail": (
                f"Доступно к возврату: "
                f"{payment.available_for_refund} {payment.order.currency}."
            ),
            "code": "refund_exceeds_available",
        })
    return value


def _sync_refund_totals(payment: Payment) -> None:
    totals = payment.payment_refunds.values("status").annotate(total=Sum("amount"))
    by_status = {row["status"]: row["total"] for row in totals}
    payment.refunded_amount = by_status.get("completed", Decimal("0"))
    payment.pending_refund_amount = by_status.get("pending", Decimal("0"))
    payment.save(update_fields=["refunded_amount", "pending_refund_amount"])
    sync_payment_status(payment.order)


@transaction.atomic
def create_cash_refund(
    payment: Payment, user, *, amount: object = None, reason: str = ""
) -> PaymentRefund:
    payment = (
        Payment.objects.select_for_update().select_related("order")
        .get(pk=payment.pk)
    )
    if payment.status != "confirmed":
        raise ValidationError({
            "detail": "Вернуть можно только подтверждённую оплату.",
            "code": "payment_not_confirmed",
        })
    reason = reason.strip()
    if not reason:
        raise ValidationError({
            "detail": "Укажите причину возврата.",
            "code": "refund_reason_required",
        })
    value = _validated_refund_amount(payment, amount)
    refund = PaymentRefund.objects.create(
        payment=payment,
        amount=value,
        method="cash",
        status="completed",
        reason=reason[:500],
        requested_by=user,
        completed_at=timezone.now(),
    )
    _sync_refund_totals(payment)
    log_event(
        "payment",
        f"Возврат из кассы {value} {payment.order.currency}",
        user=user,
        order=payment.order,
        payload={
            "action": "cash_refund_completed",
            "payment_id": payment.pk,
            "refund_id": refund.pk,
            "amount": str(value),
            "reason": reason[:500],
        },
    )
    return refund


@transaction.atomic
def create_refund(
    record: ApiPayInvoice, user, *, amount: object = None, reason: str = ""
) -> ApiPayRefund:
    if record.channel == "qr":
        raise ValidationError({
            "detail": "Возврат по QR-счёту не поддерживается.",
            "code": "qr_refund_unsupported",
        })
    payment = (
        Payment.objects.select_for_update().select_related("order")
        .get(pk=record.payment_id)
    )
    if payment.status != "confirmed":
        raise ValidationError({
            "detail": "Вернуть можно только подтверждённую оплату.",
            "code": "payment_not_confirmed",
        })
    value = _validated_refund_amount(payment, amount)
    payload: dict[str, Any] = {"amount": float(value)}
    if reason:
        payload["reason"] = reason[:500]
    response = api_request("POST", f"/invoices/{record.invoice_id}/refund", payload)
    refund_payload = response.get("refund") or {}
    refund_id = int(refund_payload["id"])
    provider_refund, _ = ApiPayRefund.objects.update_or_create(
        refund_id=refund_id,
        defaults={
            "invoice": record,
            "amount": Decimal(str(refund_payload.get("amount") or value)),
            "status": str(refund_payload.get("status") or "pending"),
            "reason": reason[:500],
            "kaspi_refund_id": str(refund_payload.get("kaspi_refund_id") or ""),
            "response_payload": response,
            "requested_by": user,
        },
    )
    provider_status = str(refund_payload.get("status") or "pending")
    status = "pending" if provider_status in {"pending", "processing"} else provider_status
    generic_refund, _ = PaymentRefund.objects.update_or_create(
        provider_refund=provider_refund,
        defaults={
            "payment": payment,
            "amount": provider_refund.amount,
            "method": "apipay",
            "status": status,
            "reason": reason[:500],
            "requested_by": user,
            "completed_at": timezone.now() if status == "completed" else None,
        },
    )
    _sync_refund_totals(payment)
    log_event(
        "payment",
        f"Возврат по счёту {value} {payment.order.currency}: {status}",
        user=user,
        order=payment.order,
        payload={
            "action": "apipay_refund_created",
            "payment_id": payment.pk,
            "refund_id": generic_refund.pk,
            "provider_refund_id": refund_id,
            "amount": str(value),
            "reason": reason[:500],
        },
    )
    return provider_refund


@transaction.atomic
def apply_refund_status(record: ApiPayInvoice, payload: dict[str, Any]) -> None:
    refund_id = int(payload["id"])
    refund, _ = ApiPayRefund.objects.update_or_create(
        refund_id=refund_id,
        defaults={
            "invoice": record,
            "amount": Decimal(str(payload["amount"])),
            "status": str(payload.get("status") or "pending"),
            "reason": str(payload.get("reason") or ""),
            "kaspi_refund_id": str(payload.get("kaspi_refund_id") or ""),
            "error_code": str(payload.get("error_code") or ""),
            "error_message": str(payload.get("error_message") or ""),
            "response_payload": payload,
        },
    )
    payment = Payment.objects.select_for_update().get(pk=record.payment_id)
    generic_status = (
        "pending" if refund.status in {"pending", "processing"} else refund.status
    )
    generic_refund, _ = PaymentRefund.objects.update_or_create(
        provider_refund=refund,
        defaults={
            "payment": payment,
            "amount": refund.amount,
            "method": "apipay",
            "status": generic_status,
            "reason": refund.reason,
            "requested_by": refund.requested_by,
            "completed_at": (
                timezone.now() if generic_status == "completed" else None
            ),
        },
    )
    _sync_refund_totals(payment)
    record.total_refunded = (
        record.refunds.filter(status="completed").aggregate(total=Sum("amount"))[
            "total"
        ]
        or Decimal("0")
    )
    record.save(update_fields=["total_refunded", "updated_at"])
    log_event(
        "payment",
        f"Статус возврата по счёту: {generic_refund.status}",
        user=None,
        order=payment.order,
        payload={
            "action": "apipay_refund_status",
            "payment_id": payment.pk,
            "refund_id": generic_refund.pk,
            "provider_refund_id": refund.refund_id,
            "status": generic_refund.status,
            "amount": str(generic_refund.amount),
        },
    )


def _parsed_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    parsed = parse_datetime(value)
    if parsed is None:
        return None
    return parsed if timezone.is_aware(parsed) else timezone.make_aware(parsed)


@transaction.atomic
def apply_invoice_status(
    record: ApiPayInvoice, invoice_payload: dict[str, Any]
) -> bool:
    """Apply a verified webhook. Returns True only when state changed."""
    record = (
        ApiPayInvoice.objects.select_for_update()
        .select_related("payment__order")
        .get(pk=record.pk)
    )
    payment = Payment.objects.select_for_update().get(pk=record.payment_id)
    order = Order.objects.select_for_update().get(pk=payment.order_id)

    status = str(invoice_payload.get("status") or "")
    if not status:
        raise ValueError("invoice.status is required")
    amount_raw = invoice_payload.get("amount")
    if amount_raw is not None:
        try:
            webhook_amount = Decimal(str(amount_raw))
        except InvalidOperation as exc:
            raise ValueError("invoice.amount is invalid") from exc
        if webhook_amount != payment.amount:
            raise ValueError("invoice.amount does not match payment")

    previous_invoice_status = record.status
    previous_payment_status = payment.status
    record.status = status
    record.error_code = str(invoice_payload.get("error_code") or "")
    record.error_message = str(invoice_payload.get("error_message") or "")
    record.response_payload = invoice_payload
    if status == "paid":
        record.paid_at = _parsed_datetime(invoice_payload.get("paid_at")) or timezone.now()
        if payment.status != "confirmed":
            payment.status = "confirmed"
            payment.confirmed_at = record.paid_at
            payment.save(update_fields=["status", "confirmed_at"])
            # QR нельзя отозвать у провайдера. Если клиент уже заменил его
            # другим способом, поздняя фактическая оплата имеет приоритет:
            # снимаем только те новые резервы, которые теперь дали бы переплату.
            confirmed_total = sum(
                (
                    row.net_amount for row in Payment.objects.select_for_update()
                    .filter(order=order, status="confirmed")
                ),
                Decimal("0"),
            )
            capacity = max(Decimal("0"), order.total_amount - confirmed_total)
            pending = list(
                Payment.objects.select_for_update()
                .filter(order=order, status__in=Payment.IN_PROGRESS_STATUSES)
                .exclude(pk=payment.pk)
                .order_by("-paid_at")
            )
            reserved = sum((row.amount for row in pending), Decimal("0"))
            for pending_payment in pending:
                if reserved <= capacity:
                    break
                pending_payment.status = "rejected"
                pending_payment.save(update_fields=["status"])
                reserved -= pending_payment.amount
            sync_payment_status(order)
    elif status in ("cancelled", "expired", "error"):
        if payment.status in Payment.IN_PROGRESS_STATUSES:
            payment.status = "rejected"
            payment.save(update_fields=["status"])

    record.save(update_fields=[
        "status", "error_code", "error_message", "response_payload",
        "paid_at", "updated_at",
    ])
    changed = (
        previous_invoice_status != record.status
        or previous_payment_status != payment.status
    )
    if changed:
        log_event(
            "payment",
            f"Счёт на оплату №{record.invoice_id} получил статус {status}",
            order=order,
            payload={
                "action": "apipay_status_changed",
                "payment_id": payment.pk,
                "apipay_invoice_id": record.invoice_id,
                "status": status,
                "payment_stage": payment.status,
                "error_code": record.error_code or None,
            },
        )
    return changed
