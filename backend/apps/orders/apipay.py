from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.eventlog.services import log_event

from .models import (
    ApiPayInvoice,
    ApiPayRefund,
    Order,
    Payment,
    PaymentRefund,
)
from .services import (
    assert_order_user_scope,
    create_client_payment,
    reject_payment,
    sync_payment_status,
)

log = logging.getLogger(__name__)


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


MONEY_RECEIVED_INVOICE_STATUSES = frozenset({"paid", "partially_refunded"})
INVOICE_RECOVERY_NOT_FOUND_GRACE = timedelta(minutes=30)
# Backwards-compatible import name for callers/tests written while recovery
# was QR-only.
QR_RECOVERY_NOT_FOUND_GRACE = INVOICE_RECOVERY_NOT_FOUND_GRACE
PROVIDER_INVOICE_STATUSES = frozenset({
    "processing",
    "pending",
    "paid",
    "cancelling",
    "cancelled",
    "expired",
    "error",
    "partially_refunded",
})

# ApiPay's documented transition graph, extended only where local recovery
# needs it: a locally recorded ``error`` can represent an ambiguous HTTP
# failure rather than a provider-side terminal error, and any pre-money state
# may first be observed as partially_refunded when the paid event was missed.
_ALLOWED_INVOICE_TRANSITIONS = {
    "creating": PROVIDER_INVOICE_STATUSES,
    "processing": PROVIDER_INVOICE_STATUSES,
    "pending": frozenset({
        "pending",
        "paid",
        "cancelling",
        "cancelled",
        "expired",
        "error",
        "partially_refunded",
    }),
    "cancelling": frozenset({
        "cancelling",
        "pending",
        "paid",
        "cancelled",
        "expired",
        "error",
        "partially_refunded",
    }),
    "cancelled": frozenset({
        "cancelled", "paid", "partially_refunded",
    }),
    "expired": frozenset({
        "expired", "paid", "partially_refunded",
    }),
    "error": frozenset({
        "error", "pending", "paid", "partially_refunded",
    }),
    # superseded is an internal marker: the provider-side QR can still emit any
    # subsequent real state and must remain reconcilable.
    "superseded": PROVIDER_INVOICE_STATUSES,
    "paid": frozenset({"paid", "partially_refunded"}),
    "partially_refunded": frozenset({"partially_refunded"}),
}


def _invoice_transition_allowed(current: str, incoming: str) -> bool:
    allowed = _ALLOWED_INVOICE_TRANSITIONS.get(current)
    # Recover legacy/unknown local values from a validated provider state.
    return incoming in PROVIDER_INVOICE_STATUSES if allowed is None else (
        incoming in allowed
    )


@contextmanager
def _invoice_issue_mutex(payment_id: int):
    """Serialize one provider POST without holding monetary row locks.

    ApiPay's idempotency key is still the remote safety net. The advisory lock
    prevents two local workers from racing a rich QR 201 response against a
    sparse duplicate-key 409 response. PostgreSQL releases a session advisory
    lock automatically if the worker connection dies.
    """
    if connection.vendor != "postgresql":
        # Production is PostgreSQL. This keeps lightweight alternative test
        # databases functional, while response merging below remains safe.
        yield
        return

    namespace = 0x415049  # "API"
    lock_key = int(payment_id) % 2_147_483_647
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_lock(%s, %s)",
            [namespace, lock_key],
        )
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_unlock(%s, %s)",
                [namespace, lock_key],
            )


@contextmanager
def _provider_scope_fence(order_id: int, user):
    """Serialize department transfer behind one authorized provider call.

    The first local reservation transaction is deliberately short. Re-locking
    Order -> Client immediately around the network call closes the gap in which
    a stale department-scoped request could otherwise reach ApiPay after an
    administrator transferred the client.
    """
    with transaction.atomic():
        order = Order.all_objects.select_for_update().get(pk=order_id)
        assert_order_user_scope(order, user)
        yield order


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


def _invoice_payload_from_error(
    exc: ApiPayAPIError,
) -> dict[str, Any] | None:
    """Extract a provider-created invoice from a non-2xx response, if present."""
    payload = exc.payload if isinstance(exc.payload, dict) else {}
    nested = payload.get("invoice")
    invoice = dict(nested) if isinstance(nested, dict) else dict(payload)
    raw_invoice_id = (
        invoice.get("id")
        or invoice.get("invoice_id")
        or payload.get("invoice_id")
    )
    if isinstance(raw_invoice_id, bool) or not isinstance(
        raw_invoice_id, (str, int)
    ):
        return None
    try:
        invoice_id = int(raw_invoice_id)
        if invoice_id <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return None
    invoice["id"] = invoice_id
    # Mapping a returned provider ID is safe even when the error body omits the
    # eventual state. Keep it active until webhook/reconciliation gives truth.
    invoice["status"] = str(invoice.get("status") or "processing")
    return invoice


def create_invoice(
    payment: Payment,
    *,
    channel: str = "phone",
    phone_number: str | None = None,
    hydrate_money_response: bool = True,
    user,
) -> ApiPayInvoice:
    """Create once, or search-only recover, an ApiPay invoice for a payment."""
    if channel not in {"phone", "qr"}:
        raise ValidationError({
            "detail": "Выберите QR или оплату по номеру.",
            "code": "invalid_payment_channel",
        })
    with _invoice_issue_mutex(payment.pk):
        # Phase 1: validate and reserve the local mapping using the same global
        # monetary lock order as webhooks and payment transitions.
        with transaction.atomic():
            order = (
                Order.all_objects.select_for_update()
                .select_related("client__user")
                .get(pk=payment.order_id)
            )
            assert_order_user_scope(order, user)
            locked_payment = Payment.objects.select_for_update().get(
                pk=payment.pk
            )
            locked_payment.order = order
            record = (
                ApiPayInvoice.objects.select_for_update()
                .filter(payment=locked_payment)
                .first()
            )
            if record is not None and record.invoice_id is not None:
                return record
            recover_existing_issue = record is not None
            if record is not None and record.status != "creating":
                raise ValidationError({
                    "detail": (
                        "Ключ прежней операции больше нельзя использовать. "
                        "Создайте новую платёжную операцию."
                    ),
                    "code": "provider_issue_key_retired",
                })
            if order.currency != "KZT":
                raise ValidationError({
                    "detail": "Счёт на оплату доступен только в тенге.",
                    "code": "apipay_kzt_only",
                })
            if record is None:
                phone = (
                    normalize_phone(phone_number or order.client.phone)
                    if channel == "phone"
                    else ""
                )
                record = ApiPayInvoice.objects.create(
                    payment=locked_payment,
                    # The trailing version delimiter prevents payment 1 from
                    # being a substring match for payment 10, 100, etc. The
                    # exact row filter still supports legacy unsuffixed keys.
                    idempotency_key=(
                        f"asyl-payment-{locked_payment.pk}-v1"
                    ),
                    status="creating",
                    channel=channel,
                    phone_number=phone,
                )
            else:
                # The first POST may have succeeded before its response was
                # lost. Never change channel or POST again with this key:
                # ApiPay permits a duplicate phone key after terminal states.
                channel = record.channel
                phone = record.phone_number
                if channel not in {"phone", "qr"}:
                    raise ValidationError({
                        "detail": "Канал прежней операции повреждён.",
                        "code": "invalid_payment_channel",
                    })
            if record is not None and record.pk:
                record_id = record.pk
            else:  # pragma: no cover - defensive ORM invariant
                raise RuntimeError("ApiPay invoice reservation was not saved")
            if not recover_existing_issue:
                request_payload = {
                    "amount": float(
                        Decimal(locked_payment.amount).quantize(
                            Decimal("0.01")
                        )
                    ),
                    "description": f"Заказ №{order.pk}",
                    "external_order_id": record.idempotency_key,
                }
                if channel == "phone":
                    request_payload["phone_number"] = phone
                    # This key protects only the first phone POST while the
                    # provider invoice is active. Recovery never POSTs again.
                    request_payload[
                        "external_order_id_idempotency"
                    ] = record.idempotency_key
            else:
                request_payload = {}
            order_id = order.pk

        if recover_existing_issue:
            # Neither channel is safe to POST again after an ambiguous first
            # response. Phone idempotency only rejects duplicates while the
            # previous provider invoice remains active.
            with _provider_scope_fence(order_id, user):
                recovered = recover_invoice_issue_mapping(record)
            if recovered is not None:
                return recovered
            record.refresh_from_db()
            if record.status == "error":
                raise ApiPayAPIError(
                    409,
                    record.error_code or "apipay_invoice_not_found",
                    record.error_message
                    or "Создание счёта не подтверждено платёжным сервисом",
                    record.response_payload,
                )
            raise ApiPayAPIError(
                503,
                "apipay_issue_recovery_pending",
                "Статус создаваемого счёта ещё уточняется",
                {},
            )

        # Phase 2 keeps only the Order -> Client authorization fence over the
        # network. Monetary rows remain unlocked, while a department transfer
        # cannot overtake this already-authorized provider side effect.
        path = "/invoices/qr" if channel == "qr" else "/invoices"
        try:
            with _provider_scope_fence(order_id, user):
                response = api_request("POST", path, request_payload)
        except PermissionDenied:
            _save_invoice_issue_error(
                record_id,
                payment.pk,
                ApiPayAPIError(
                    409,
                    "client_department_changed",
                    "Клиент передан в другой отдел до создания счёта",
                    {},
                ),
                ambiguous=False,
            )
            raise
        except ApiPayConfigurationError as exc:
            _save_invoice_issue_error(
                record_id,
                payment.pk,
                ApiPayAPIError(
                    503,
                    "apipay_not_configured",
                    str(exc),
                    {},
                ),
                ambiguous=False,
            )
            raise
        except ApiPayAPIError as exc:
            if (
                exc.status_code == 409
                and exc.error_code == "duplicate_idempotency_key"
            ):
                response = {
                    "id": exc.payload.get("invoice_id"),
                    "status": exc.payload.get("status", "processing"),
                }
            else:
                recovered_response = _invoice_payload_from_error(exc)
                if recovered_response is not None:
                    response = recovered_response
                    try:
                        invoice_id = int(response["id"])
                    except (KeyError, TypeError, ValueError):
                        invoice_id = 0
                    if invoice_id > 0:
                        record, mapped_now = _merge_invoice_create_response(
                            record_id=record_id,
                            payment_id=payment.pk,
                            invoice_id=invoice_id,
                            response=response,
                            channel=channel,
                            phone=phone,
                        )
                        record = _apply_create_response_safely(
                            record,
                            response,
                            hydrate_money_response=hydrate_money_response,
                        )
                        if mapped_now:
                            from .webhooks import (
                                replay_pending_apipay_webhooks,
                            )

                            replay_pending_apipay_webhooks(
                                provider_invoice_id=invoice_id,
                            )
                            record.refresh_from_db()
                        if record.status not in {
                            "error", "cancelled", "expired",
                        }:
                            return record
                        # The provider ID and terminal evidence are already
                        # durable. Still surface the original failed issuance
                        # to the caller instead of reporting a usable invoice.
                        raise
                recovered = _save_invoice_issue_error(
                    record_id, payment.pk, exc
                )
                if recovered is not None:
                    return recovered
                raise

        try:
            invoice_id = int(response["id"])
            if invoice_id <= 0:
                raise ValueError
        except (KeyError, TypeError, ValueError) as exc:
            invalid_response = ApiPayAPIError(
                502,
                "invalid_apipay_response",
                "Платёжный сервис не вернул идентификатор счёта",
                response,
            )
            recovered = _save_invoice_issue_error(
                record_id, payment.pk, invalid_response
            )
            if recovered is not None:
                return recovered
            raise invalid_response from exc

        record, mapped_now = _merge_invoice_create_response(
            record_id=record_id,
            payment_id=payment.pk,
            invoice_id=invoice_id,
            response=response,
            channel=channel,
            phone=phone,
        )
        record = _apply_create_response_safely(
            record,
            response,
            hydrate_money_response=hydrate_money_response,
        )

        # Replay outside monetary row locks. Calling the public inbox consumer
        # directly avoids the Event->Order vs Order->Event signal inversion.
        if mapped_now:
            from .webhooks import replay_pending_apipay_webhooks

            replay_pending_apipay_webhooks(
                provider_invoice_id=invoice_id,
            )
            record.refresh_from_db()

        log_event(
            "payment",
            f"Счёт на оплату №{invoice_id} создан для заказа №{order_id}",
            user=locked_payment.recorded_by,
            order=order,
            payload={
                "action": "apipay_invoice_created",
                "payment_id": locked_payment.pk,
                "apipay_invoice_id": invoice_id,
                "status": record.status,
            },
        )
        return record


def _save_invoice_issue_error(
    record_id: int,
    payment_id: int,
    exc: ApiPayAPIError,
    *,
    ambiguous: bool | None = None,
) -> ApiPayInvoice | None:
    """Persist a failed attempt unless another worker already mapped it.

    A timeout/5xx is ambiguous: ApiPay may have created the invoice before the
    response was lost. Keep the Payment reservation and ``creating`` record so
    the monitor can retry the exact same idempotency key. Only definitive 4xx
    failures release the reservation.
    """
    order_id = Payment.objects.values_list("order_id", flat=True).get(
        pk=payment_id
    )
    with transaction.atomic():
        order = Order.all_objects.select_for_update().get(pk=order_id)
        payment = Payment.objects.select_for_update().get(pk=payment_id)
        record = ApiPayInvoice.objects.select_for_update().get(pk=record_id)
        if record.invoice_id is not None:
            return record
        if record.status not in MONEY_RECEIVED_INVOICE_STATUSES:
            is_ambiguous = (
                exc.status_code >= 500 if ambiguous is None else ambiguous
            )
            record.status = "creating" if is_ambiguous else "error"
            record.error_code = exc.error_code
            record.error_message = exc.message
            record.response_payload = exc.payload
            record.save(update_fields=[
                "status", "error_code", "error_message",
                "response_payload", "updated_at",
            ])
            if (
                not is_ambiguous
                and payment.status in Payment.IN_PROGRESS_STATUSES
            ):
                payment.status = "rejected"
                payment.save(update_fields=["status"])
                sync_payment_status(order)
    return None


def _merge_invoice_create_response(
    *,
    record_id: int,
    payment_id: int,
    invoice_id: int,
    response: dict[str, Any],
    channel: str,
    phone: str,
) -> tuple[ApiPayInvoice, bool]:
    """Merge a provider create response without regressing richer/newer state."""
    order_id = Payment.objects.values_list("order_id", flat=True).get(
        pk=payment_id
    )
    with transaction.atomic():
        Order.all_objects.select_for_update().get(pk=order_id)
        Payment.objects.select_for_update().get(pk=payment_id)
        record = ApiPayInvoice.objects.select_for_update().get(pk=record_id)
        if record.invoice_id is not None and record.invoice_id != invoice_id:
            raise ApiPayAPIError(
                502,
                "invoice_id_mismatch",
                "Платёжный сервис вернул другой идентификатор счёта",
                response,
            )

        mapped_now = record.invoice_id is None
        updates: dict[str, Any] = {
            "invoice_id": invoice_id,
            "channel": channel,
            "phone_number": phone,
            "error_code": "",
            "error_message": "",
            "updated_at": timezone.now(),
        }

        # Sparse duplicate-key responses must never erase a rich QR response.
        qr_token_url = str(response.get("qr_token_url") or "")
        qr_image_url = str(response.get("qr_image_url") or "")
        qr_expires_at = _parsed_datetime(response.get("qr_expires_at"))
        if qr_token_url:
            updates["qr_token_url"] = qr_token_url
        if qr_image_url:
            updates["qr_image_url"] = qr_image_url
        if qr_expires_at is not None:
            updates["qr_expires_at"] = qr_expires_at

        # QuerySet.update deliberately avoids firing the invoice-mapping signal
        # while Order/Payment/Invoice row locks are held.
        ApiPayInvoice.objects.filter(pk=record.pk).update(**updates)

    record.refresh_from_db()
    return record, mapped_now


def recover_invoice_mapping_from_payload(
    payload: dict[str, Any],
) -> ApiPayInvoice | None:
    """Map a webhook/search row to an unmapped per-payment external reference."""
    external_ref = payload.get("external_order_id")
    if not isinstance(external_ref, str) or not external_ref:
        return None
    candidate = (
        ApiPayInvoice.objects.filter(
            idempotency_key=external_ref,
            invoice_id__isnull=True,
            # A signed webhook can arrive after conservative stale-QR release.
            # It remains authoritative and may revive that exact old Payment.
            status__in=("creating", "error"),
        )
        .select_related("payment")
        .first()
    )
    if candidate is None:
        return None
    try:
        invoice_id = int(payload["id"])
        if invoice_id <= 0:
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("invoice.id is invalid") from exc

    order_id = candidate.payment.order_id
    with transaction.atomic():
        Order.all_objects.select_for_update().get(pk=order_id)
        Payment.objects.select_for_update().get(pk=candidate.payment_id)
        record = ApiPayInvoice.objects.select_for_update().get(pk=candidate.pk)
        if record.invoice_id is not None:
            return record if record.invoice_id == invoice_id else None
        if record.idempotency_key != external_ref:
            return None
        ApiPayInvoice.objects.filter(pk=record.pk).update(
            invoice_id=invoice_id,
            error_code="",
            error_message="",
            updated_at=timezone.now(),
        )
    record.refresh_from_db()
    return record


def _money_payload_has_matching_amount(
    record: ApiPayInvoice, payload: dict[str, Any]
) -> bool:
    try:
        amount = Decimal(str(payload["amount"]))
    except (KeyError, InvalidOperation, TypeError, ValueError):
        return False
    return (
        amount.is_finite()
        and amount > 0
        and amount == Decimal(str(record.payment.amount))
    )


def _hydrate_money_response(
    record: ApiPayInvoice, payload: dict[str, Any]
) -> dict[str, Any]:
    """Fetch full provider truth when a sparse sync response reports money."""
    status = str(payload.get("status") or "")
    if (
        status not in MONEY_RECEIVED_INVOICE_STATUSES
        or _money_payload_has_matching_amount(record, payload)
    ):
        return payload
    authoritative = get_invoice(int(record.invoice_id))
    try:
        provider_invoice_id = int(authoritative["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("invoice.id is invalid") from exc
    if provider_invoice_id != record.invoice_id:
        raise ValueError("invoice.id does not match local invoice")
    return authoritative


def _apply_create_response_safely(
    record: ApiPayInvoice,
    response: dict[str, Any],
    *,
    hydrate_money_response: bool = True,
) -> ApiPayInvoice:
    """Apply sync provider state without releasing a mapped payable invoice.

    Once ApiPay returned an invoice ID, an incomplete/unknown response is not a
    reason to reject the local reservation: reconciliation can still recover
    the full current object. Valid terminal responses use the same monetary
    transition engine as webhooks.
    """
    try:
        payload = (
            _hydrate_money_response(record, response)
            if hydrate_money_response
            else response
        )
        apply_invoice_status(record, payload)
    except (ApiPayAPIError, ApiPayConfigurationError, ValueError):
        log.exception(
            "ApiPay create response could not be safely applied invoice_id=%s",
            record.invoice_id,
        )
    record.refresh_from_db()
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
        return create_invoice(
            payment,
            channel=channel,
            phone_number=phone_number,
            user=user,
        )
    except (ApiPayAPIError, ApiPayConfigurationError, ValidationError):
        payment.refresh_from_db()
        unresolved = ApiPayInvoice.objects.filter(
            payment=payment,
            invoice_id__isnull=True,
            status="creating",
        ).exists()
        if payment.status in Payment.IN_PROGRESS_STATUSES and not unresolved:
            reject_payment(payment, user)
        raise


def get_invoice(invoice_id: int) -> dict[str, Any]:
    return api_request("GET", f"/invoices/{invoice_id}")


def _quarantine_invoice_issue_mapping(
    record: ApiPayInvoice,
    *,
    checked_at,
    matches: list[dict[str, Any]],
    provider_result: dict[str, Any],
) -> ApiPayInvoice | None:
    """Keep a multiply-mapped issue reserved for manual/provider resolution."""
    order_id = Payment.objects.values_list("order_id", flat=True).get(
        pk=record.payment_id
    )
    with transaction.atomic():
        Order.all_objects.select_for_update().get(pk=order_id)
        Payment.objects.select_for_update().get(pk=record.payment_id)
        locked = ApiPayInvoice.objects.select_for_update().get(pk=record.pk)
        if locked.invoice_id is not None:
            return locked
        if locked.status != "creating":
            return None
        locked.error_code = "ambiguous_apipay_invoice_mapping"
        locked.error_message = (
            "ApiPay вернул несколько счетов для одной платёжной операции"
        )
        locked.response_payload = {
            "recovery": "quarantined_multiple_matches",
            "external_order_id": locked.idempotency_key,
            "checked_at": checked_at.isoformat(),
            "matches": matches,
            "provider_result": provider_result,
        }
        locked.save(update_fields=[
            "error_code",
            "error_message",
            "response_payload",
            "updated_at",
        ])
    return None


def recover_invoice_issue_mapping(
    record: ApiPayInvoice,
    *,
    checked_at=None,
    not_found_grace: timedelta = INVOICE_RECOVERY_NOT_FOUND_GRACE,
    max_pages: int | None = None,
    hydrate_money_response: bool = True,
) -> ApiPayInvoice | None:
    """Search-only recovery after an ambiguous first phone or QR create.

    A miss only releases the local reservation after the complete paginated
    search result has been read and a conservative grace period has elapsed.
    ``created_at`` is immutable and therefore safe for that age decision;
    ``updated_at`` remains only a polling throttle.
    """
    record.refresh_from_db()
    if record.invoice_id is not None:
        return record
    if record.channel not in {"phone", "qr"}:
        raise ValueError("invoice recovery requires phone or QR channel")

    checked_at = checked_at or timezone.now()
    not_found_grace = max(not_found_grace, timedelta(0))
    per_page = 100
    page = 1
    seen = 0
    matches: list[dict[str, Any]] = []
    last_response: dict[str, Any] = {}
    expected_total: int | None = None
    while True:
        if max_pages is not None and page > max(0, int(max_pages)):
            raise ApiPayAPIError(
                429,
                "apipay_reconcile_budget_exhausted",
                "Полный поиск счёта отложен до следующего цикла сверки",
                last_response,
            )
        query = urllib.parse.urlencode({
            "search": record.idempotency_key,
            "per_page": per_page,
            "page": page,
        })
        response = api_request("GET", f"/invoices?{query}")
        if not isinstance(response, dict):
            raise ApiPayAPIError(
                502,
                "invalid_apipay_response",
                "Платёжный сервис вернул некорректный список счетов",
                {},
            )
        last_response = response
        rows = response.get("data")
        if not isinstance(rows, list):
            raise ApiPayAPIError(
                502,
                "invalid_apipay_response",
                "Платёжный сервис вернул некорректный список счетов",
                response,
            )
        current_page = response.get("current_page")
        if current_page not in (None, page, str(page)):
            raise ApiPayAPIError(
                502,
                "invalid_apipay_response",
                "Платёжный сервис вернул некорректную пагинацию счетов",
                response,
            )
        try:
            total = (
                int(response["total"])
                if response.get("total") is not None
                else None
            )
            if total is not None and total < 0:
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise ApiPayAPIError(
                502,
                "invalid_apipay_response",
                "Платёжный сервис вернул некорректную пагинацию счетов",
                response,
            ) from exc
        if total is not None:
            if expected_total is None:
                expected_total = total
            elif total != expected_total:
                raise ApiPayAPIError(
                    502,
                    "invalid_apipay_response",
                    "Список счетов изменился во время пагинации",
                    response,
                )
        matches.extend(
            row for row in rows
            if isinstance(row, dict)
            and row.get("external_order_id") == record.idempotency_key
        )
        seen += len(rows)
        # The documented total proves exhaustion. For older compatible
        # responses without total, only a short page proves end-of-list.
        if total is not None:
            if seen >= total:
                break
            if len(rows) < per_page:
                raise ApiPayAPIError(
                    502,
                    "invalid_apipay_response",
                    "Платёжный сервис вернул неполный список счетов",
                    response,
                )
        elif len(rows) < per_page:
            break
        if not rows:
            raise ApiPayAPIError(
                502,
                "invalid_apipay_response",
                "Платёжный сервис вернул неполный список счетов",
                response,
            )
        page += 1

    if not matches:
        if checked_at - record.created_at >= not_found_grace:
            # Reuse the global Order -> Payment -> Invoice lock order. If a
            # webhook mapped the invoice after our GET, it wins the recheck.
            previous_evidence = {
                "error_code": record.error_code,
                "error_message": record.error_message,
                "response_payload": record.response_payload,
            }
            recovered = _save_invoice_issue_error(
                record.pk,
                record.payment_id,
                ApiPayAPIError(
                    404,
                    f"apipay_{record.channel}_not_found",
                    (
                        "Создание счёта не подтверждено платёжным сервисом "
                        "после безопасного периода ожидания"
                    ),
                    {
                        "recovery": "not_found",
                        "external_order_id": record.idempotency_key,
                        "checked_at": checked_at.isoformat(),
                        "provider_result": last_response,
                        "previous_issue": previous_evidence,
                    },
                ),
                ambiguous=False,
            )
            if recovered is not None:
                return recovered
        else:
            ApiPayInvoice.objects.filter(
                pk=record.pk,
                invoice_id__isnull=True,
                status="creating",
            ).update(updated_at=checked_at)
        return None
    matches_by_id: dict[int, dict[str, Any]] = {}
    for match in matches:
        try:
            match_id = int(match["id"])
            if match_id <= 0:
                raise ValueError
        except (KeyError, TypeError, ValueError) as exc:
            raise ApiPayAPIError(
                502,
                "invalid_apipay_response",
                "Платёжный сервис вернул счёт без идентификатора",
                last_response,
            ) from exc
        matches_by_id.setdefault(match_id, match)
    if len(matches_by_id) != 1:
        recovered = _quarantine_invoice_issue_mapping(
            record,
            checked_at=checked_at,
            matches=list(matches_by_id.values()),
            provider_result=last_response,
        )
        if recovered is not None:
            return recovered
        raise ApiPayAPIError(
            502,
            "ambiguous_apipay_invoice_mapping",
            "Платёжный сервис вернул несколько счетов для одной операции",
            last_response,
        )
    invoice_id, payload = next(iter(matches_by_id.items()))
    record, mapped_now = _merge_invoice_create_response(
        record_id=record.pk,
        payment_id=record.payment_id,
        invoice_id=invoice_id,
        response=payload,
        channel=record.channel,
        phone=record.phone_number,
    )
    record = _apply_create_response_safely(
        record,
        payload,
        hydrate_money_response=hydrate_money_response,
    )
    if mapped_now:
        from .webhooks import replay_pending_apipay_webhooks

        replay_pending_apipay_webhooks(provider_invoice_id=invoice_id)
        record.refresh_from_db()
    return record


def recover_qr_invoice_mapping(
    record: ApiPayInvoice,
    **kwargs,
) -> ApiPayInvoice | None:
    """Compatibility wrapper around the channel-agnostic recovery."""
    record.refresh_from_db(fields=["channel"])
    if record.channel != "qr":
        raise ValueError("QR recovery requires a QR invoice record")
    return recover_invoice_issue_mapping(record, **kwargs)


def check_invoice_statuses(invoice_ids: list[int]) -> dict[str, Any]:
    return api_request(
        "POST", "/invoices/status/check", {"invoice_ids": invoice_ids}
    )


def get_invoice_refunds(record: ApiPayInvoice) -> dict[str, Any]:
    """Read the provider's authoritative refund list for one invoice.

    ApiPay documents this GET endpoint for refund status/recovery. There is no
    documented GET-by-refund-id endpoint and no documented idempotency key for
    POST /refund, so ambiguous POST outcomes must be resolved through this
    list rather than by repeating the refund request.
    """
    if record.invoice_id is None:
        raise ValidationError({
            "detail": "Счёт на оплату ещё не создан.",
            "code": "invoice_not_created",
        })
    return api_request("GET", f"/invoices/{record.invoice_id}/refunds")


def cancel_invoice(record: ApiPayInvoice, *, user) -> ApiPayInvoice:
    order_id = Payment.objects.values_list("order_id", flat=True).get(
        pk=record.payment_id
    )
    with _provider_scope_fence(order_id, user):
        locked_record = (
            ApiPayInvoice.objects.select_for_update()
            .select_related("payment")
            .get(pk=record.pk)
        )
        _cancel_invoice_locked(locked_record)

    # Preserve the existing in-memory contract for callers that retain the
    # instance supplied to this service.
    record.refresh_from_db()
    return record


def _cancel_invoice_locked(record: ApiPayInvoice) -> ApiPayInvoice:
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
    try:
        response = api_request(
            "POST", f"/invoices/{record.invoice_id}/cancel", {}
        )
    except ApiPayAPIError as exc:
        if exc.error_code not in {
            "invoice_already_paid",
            "invoice_already_cancelled",
        }:
            raise
        # ApiPay documents these as cancellation outcomes, not trustworthy
        # terminal invoice states. The shared status engine canonicalizes
        # already-paid to active/pending (until paid sync arrives), and
        # already-cancelled to the desired closed state.
        apply_invoice_status(
            record,
            {
                "id": record.invoice_id,
                "status": "error",
                "error_code": exc.error_code,
                "error_message": exc.message,
            },
        )
        record.refresh_from_db()
        return record
    invoice_payload = response.get("invoice")
    if isinstance(invoice_payload, dict):
        response_payload = invoice_payload
    else:
        response_payload = {
            "id": record.invoice_id,
            "status": "cancelling",
        }

    # Use exactly the same state machine as webhook/reconciliation. If a sparse
    # paid response cannot be verified immediately, keep the operation in the
    # conservative cancelling state so its still-payable reservation cannot be
    # rejected by the caller.
    try:
        response_payload = _hydrate_money_response(record, response_payload)
        apply_invoice_status(record, response_payload)
    except (ApiPayAPIError, ApiPayConfigurationError, ValueError):
        log.exception(
            "ApiPay cancel response could not be safely applied invoice_id=%s",
            record.invoice_id,
        )
        apply_invoice_status(
            record,
            {"id": record.invoice_id, "status": "cancelling"},
        )
    record.refresh_from_db()
    return record


def _validated_refund_amount(payment: Payment, amount: object) -> Decimal:
    raw = payment.available_for_refund if amount in (None, "") else amount
    try:
        parsed = Decimal(str(raw))
        value = parsed.quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise ValidationError({"detail": "Некорректная сумма возврата."}) from exc
    if not value.is_finite() or value <= 0:
        raise ValidationError({"detail": "Сумма возврата должна быть больше нуля."})
    if parsed != value:
        raise ValidationError({
            "detail": "Укажите сумму возврата с точностью не более двух знаков."
        })
    if value > payment.available_for_refund:
        raise ValidationError({
            "detail": (
                f"Доступно к возврату: "
                f"{payment.available_for_refund} {payment.order.currency}."
            ),
            "code": "refund_exceeds_available",
        })
    return value


def _sync_refund_totals(payment: Payment, order: Order) -> None:
    totals = payment.payment_refunds.values("status").annotate(total=Sum("amount"))
    by_status = {row["status"]: row["total"] for row in totals}
    payment.refunded_amount = by_status.get("completed", Decimal(0))
    payment.pending_refund_amount = by_status.get("pending", Decimal(0))
    payment.save(update_fields=["refunded_amount", "pending_refund_amount"])
    sync_payment_status(order)


def _fail_reserved_refund(
    *, refund_id: int, payment_id: int, order_id: int
) -> None:
    """Release a reservation after local denial or definitive provider failure."""
    with transaction.atomic():
        order = Order.all_objects.select_for_update().get(pk=order_id)
        payment = Payment.objects.select_for_update().get(pk=payment_id)
        refund = PaymentRefund.objects.select_for_update().get(pk=refund_id)
        refund.status = "failed"
        refund.save(update_fields=["status", "updated_at"])
        payment.order = order
        _sync_refund_totals(payment, order)


@transaction.atomic
def create_cash_refund(
    payment: Payment, user, *, amount: object = None, reason: str = ""
) -> PaymentRefund:
    order = Order.all_objects.select_for_update().get(pk=payment.order_id)
    assert_order_user_scope(order, user)
    payment = (
        Payment.objects.select_for_update()
        .get(pk=payment.pk)
    )
    payment.order = order
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
    _sync_refund_totals(payment, order)
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


def create_refund(
    record: ApiPayInvoice, user, *, amount: object = None, reason: str = ""
) -> ApiPayRefund:
    if record.invoice_id is None:
        raise ValidationError({
            "detail": "Счёт на оплату ещё не создан.",
            "code": "invoice_not_created",
        })
    reason = reason.strip()
    if not reason:
        raise ValidationError({
            "detail": "Укажите причину возврата.",
            "code": "refund_reason_required",
        })
    # Reserve the amount in a short local transaction before the remote call.
    # This prevents two workers from refunding the same balance and avoids
    # holding database locks while ApiPay/Kaspi responds.
    with transaction.atomic():
        order = Order.all_objects.select_for_update().get(
            pk=record.payment.order_id
        )
        assert_order_user_scope(order, user)
        payment = Payment.objects.select_for_update().get(pk=record.payment_id)
        payment.order = order
        if payment.status != "confirmed":
            raise ValidationError({
                "detail": "Вернуть можно только подтверждённую оплату.",
                "code": "payment_not_confirmed",
            })
        # ApiPay does not document an idempotency key for refund creation.
        # Keep at most one request without a provider ID in flight per payment:
        # a concurrent retry could otherwise create a second real refund and
        # two same-amount requests cannot be correlated reliably afterwards.
        if PaymentRefund.objects.select_for_update().filter(
            payment=payment,
            method="apipay",
            status="pending",
            provider_refund__isnull=True,
        ).exists():
            raise ValidationError({
                "detail": (
                    "Предыдущий возврат ещё сверяется с платёжным сервисом. "
                    "Дождитесь результата перед новым возвратом."
                ),
                "code": "refund_submission_in_progress",
            })
        value = _validated_refund_amount(payment, amount)
        generic_refund = PaymentRefund.objects.create(
            payment=payment,
            amount=value,
            method="apipay",
            status="pending",
            reason=reason[:500],
            requested_by=user,
        )
        _sync_refund_totals(payment, order)

    order_id = order.pk
    payload: dict[str, Any] = {"amount": float(value), "reason": reason[:500]}
    try:
        with _provider_scope_fence(order_id, user):
            response = api_request(
                "POST", f"/invoices/{record.invoice_id}/refund", payload
            )
    except PermissionDenied:
        _fail_reserved_refund(
            refund_id=generic_refund.pk,
            payment_id=record.payment_id,
            order_id=order_id,
        )
        raise
    except (ApiPayAPIError, ApiPayConfigurationError) as exc:
        # A 4xx response is definitive.  For a timeout/5xx the remote outcome
        # is ambiguous, so keep the amount reserved until webhook/reconciliation
        # proves success or failure; blindly retrying could refund twice.
        definitive_failure = (
            isinstance(exc, ApiPayConfigurationError)
            or exc.status_code < 500
        )
        if definitive_failure:
            _fail_reserved_refund(
                refund_id=generic_refund.pk,
                payment_id=record.payment_id,
                order_id=order_id,
            )
        raise

    refund_payload = response.get("refund") or {}
    try:
        refund_id = int(refund_payload["id"])
        if refund_id <= 0:
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        # Keep the local reservation: the provider may have accepted the
        # request despite returning a malformed gateway response.
        raise ApiPayAPIError(
            502,
            "invalid_apipay_response",
            "Платёжный сервис не вернул идентификатор возврата",
            response,
        ) from exc
    with transaction.atomic():
        order = Order.all_objects.select_for_update().get(
            pk=record.payment.order_id
        )
        payment = Payment.objects.select_for_update().get(pk=record.payment_id)
        generic_refund = PaymentRefund.objects.select_for_update().get(
            pk=generic_refund.pk
        )
        provider_status = str(refund_payload.get("status") or "")
        response_issue_code = ""
        response_issue_message = ""
        if provider_status not in {
            "pending", "processing", "completed", "failed",
        }:
            # The POST has already been accepted and returned a provider ID.
            # Preserve the reservation and let the documented GET/webhook
            # converge it instead of surfacing a retryable client error.
            provider_status = "pending"
            response_issue_code = "provider_refund_status_invalid"
            response_issue_message = (
                "ApiPay returned an unknown refund status; awaiting "
                "authoritative reconciliation"
            )
        try:
            provider_amount = Decimal(str(refund_payload["amount"])).quantize(
                Decimal("0.01")
            )
            if not provider_amount.is_finite() or provider_amount <= 0:
                raise InvalidOperation
        except (KeyError, InvalidOperation, TypeError, ValueError):
            provider_amount = value
            provider_status = "pending"
            response_issue_code = "provider_refund_amount_invalid"
            response_issue_message = (
                "ApiPay returned an invalid refund amount; awaiting "
                "authoritative reconciliation"
            )
        else:
            if provider_amount != value or provider_amount > payment.amount:
                # Preserve the returned provider mapping, but never let a
                # malformed synchronous amount change local monetary totals.
                provider_amount = value
                provider_status = "pending"
                response_issue_code = "provider_refund_amount_mismatch"
                response_issue_message = (
                    "ApiPay refund amount does not match the reserved amount; "
                    "awaiting authoritative reconciliation"
                )
        provider_refund = (
            ApiPayRefund.objects.select_for_update()
            .filter(refund_id=refund_id)
            .first()
        )
        if provider_refund is None:
            provider_refund = ApiPayRefund(refund_id=refund_id, invoice=record)
        elif provider_refund.invoice_id != record.pk:
            raise ApiPayAPIError(
                502,
                "refund_invoice_mismatch",
                "Платёжный сервис вернул возврат другого счёта",
                response,
            )
        completed_elsewhere_query = (
            PaymentRefund.objects.select_for_update()
            .filter(payment=payment, status="completed")
            .exclude(pk=generic_refund.pk)
        )
        if provider_refund.pk:
            completed_elsewhere_query = completed_elsewhere_query.exclude(
                provider_refund_id=provider_refund.pk
            )
        completed_elsewhere = (
            completed_elsewhere_query.aggregate(total=Sum("amount"))["total"]
            or Decimal(0)
        )
        if (
            provider_status == "completed"
            and completed_elsewhere + value > payment.amount
        ):
            provider_status = "pending"
            response_issue_code = "provider_refund_total_exceeds_payment"
            response_issue_message = (
                "ApiPay refund completion exceeds the payment amount; "
                "awaiting authoritative reconciliation"
            )
        if not (
            provider_refund.pk
            and provider_refund.status in {"completed", "failed"}
            and provider_status in {"pending", "processing"}
        ):
            provider_refund.invoice = record
            provider_refund.amount = provider_amount
            provider_refund.status = provider_status
            provider_refund.reason = reason[:500]
            provider_refund.kaspi_refund_id = str(
                refund_payload.get("kaspi_refund_id") or ""
            )
            provider_refund.response_payload = response
            provider_refund.requested_by = user
            provider_refund.error_code = response_issue_code
            provider_refund.error_message = response_issue_message
            provider_refund.save()
        elif provider_refund.requested_by_id is None:
            provider_refund.requested_by = user
            provider_refund.save(update_fields=["requested_by", "updated_at"])
        provider_status = provider_refund.status
        status = (
            "pending"
            if provider_status in {"pending", "processing"}
            else provider_status
        )
        already_linked = (
            PaymentRefund.objects.select_for_update()
            .filter(provider_refund=provider_refund)
            .first()
        )
        if already_linked is not None and already_linked.pk != generic_refund.pk:
            # A webhook may win the race with the POST response. Never attach
            # one provider refund to two local reservations.
            generic_refund.status = "failed"
            generic_refund.save(update_fields=["status", "updated_at"])
            generic_refund = already_linked
        else:
            generic_refund.provider_refund = provider_refund
        if generic_refund.requested_by_id is None:
            generic_refund.requested_by = user
        generic_refund.amount = provider_refund.amount
        generic_refund.status = status
        generic_refund.completed_at = (
            generic_refund.completed_at or timezone.now()
            if status == "completed"
            else None
        )
        generic_refund.save(update_fields=[
            "provider_refund", "requested_by", "amount", "status",
            "completed_at", "updated_at",
        ])
        payment.order = order
        _sync_refund_totals(payment, order)
    try:
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
    except Exception:
        # The external refund and its local mapping are already committed.
        # An auxiliary audit-log failure must not turn that success into a
        # client-visible error which could prompt a duplicate, non-idempotent
        # refund submission.
        log.exception(
            "Could not log ApiPay refund creation refund_id=%s",
            refund_id,
        )
    return provider_refund


def _select_unlinked_local_refund(
    payment: Payment,
    payload: dict[str, Any],
    amount: Decimal,
) -> PaymentRefund | None:
    """Correlate a webhook when it races the refund POST response.

    The provider offers no client correlation/idempotency key for refunds.
    Amount plus the echoed reason is the strongest documented tuple; provider
    created_at breaks a legacy same-value tie. New submissions are serialized
    above, so normally this sees exactly one candidate.
    """
    candidates = list(
        PaymentRefund.objects.select_for_update()
        .filter(
            payment=payment,
            provider_refund__isnull=True,
            method="apipay",
            status="pending",
            amount=amount,
        )
        .order_by("created_at", "pk")
    )
    if not candidates:
        return None
    provider_reason = str(payload.get("reason") or "").strip()
    if provider_reason:
        exact = [
            candidate
            for candidate in candidates
            if candidate.reason.strip() == provider_reason
        ]
        if exact:
            candidates = exact
    if len(candidates) == 1:
        return candidates[0]
    provider_created_at = _parsed_datetime(payload.get("created_at"))
    if provider_created_at is None:
        return None
    ranked = sorted(
        candidates,
        key=lambda candidate: (
            abs((candidate.created_at - provider_created_at).total_seconds()),
            candidate.pk,
        ),
    )
    if len(ranked) > 1:
        first_delta = abs(
            (ranked[0].created_at - provider_created_at).total_seconds()
        )
        second_delta = abs(
            (ranked[1].created_at - provider_created_at).total_seconds()
        )
        if first_delta == second_delta:
            return None
    return ranked[0]


@transaction.atomic
def apply_refund_status(
    record: ApiPayInvoice,
    payload: dict[str, Any],
    *,
    generic_refund_id: int | None = None,
    allow_automatic_link: bool = True,
) -> bool:
    order = Order.all_objects.select_for_update().get(
        pk=record.payment.order_id
    )
    payment = Payment.objects.select_for_update().get(pk=record.payment_id)
    try:
        refund_id = int(payload["id"])
        if refund_id <= 0:
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("refund.id is invalid") from exc
    try:
        incoming_amount = Decimal(str(payload["amount"])).quantize(
            Decimal("0.01")
        )
        if not incoming_amount.is_finite() or incoming_amount <= 0:
            raise InvalidOperation
    except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("refund.amount is invalid") from exc
    if incoming_amount > payment.amount:
        raise ValueError("refund.amount exceeds payment amount")
    incoming_status = str(payload.get("status") or "pending")
    if incoming_status not in {"pending", "processing", "completed", "failed"}:
        raise ValueError("refund.status is invalid")
    refund = (
        ApiPayRefund.objects.select_for_update()
        .filter(refund_id=refund_id)
        .first()
    )
    previous_provider_status = refund.status if refund is not None else None
    if refund is None:
        refund = ApiPayRefund(refund_id=refund_id, invoice=record)
    elif refund.invoice_id != record.pk:
        raise ValueError("refund belongs to another invoice")
    freezes_terminal = (
        refund.pk
        and refund.status in {"completed", "failed"}
        and incoming_status != refund.status
    )
    effective_amount = refund.amount if freezes_terminal else incoming_amount
    effective_status = refund.status if freezes_terminal else incoming_status
    completed_other = sum(
        record.refunds.filter(status="completed")
        .exclude(pk=refund.pk)
        .values_list("amount", flat=True),
        Decimal(0),
    )
    completed_cash = (
        PaymentRefund.objects.filter(
            payment=payment,
            method="cash",
            status="completed",
        ).aggregate(total=Sum("amount"))["total"]
        or Decimal(0)
    )
    if (
        effective_status == "completed"
        and completed_other + completed_cash + effective_amount
        > payment.amount
    ):
        raise ValueError("completed refunds exceed payment amount")
    if not freezes_terminal:
        refund.invoice = record
        refund.amount = incoming_amount
        refund.status = incoming_status
        refund.reason = str(payload.get("reason") or "")[:500]
        refund.kaspi_refund_id = str(
            payload.get("kaspi_refund_id") or ""
        )[:100]
        refund.error_code = str(payload.get("error_code") or "")[:100]
        refund.error_message = str(payload.get("error_message") or "")
        refund.response_payload = payload
        refund.save()
    generic_status = (
        "pending" if refund.status in {"pending", "processing"} else refund.status
    )
    generic_refund = PaymentRefund.objects.filter(
        provider_refund=refund
    ).first()
    previous_generic_status = (
        generic_refund.status if generic_refund is not None else None
    )
    linked_now = False
    if generic_refund is None and generic_refund_id is not None:
        generic_refund = (
            PaymentRefund.objects.select_for_update()
            .filter(
                pk=generic_refund_id,
                payment=payment,
                provider_refund__isnull=True,
                method="apipay",
                status="pending",
                amount=refund.amount,
            )
            .first()
        )
        if generic_refund is None:
            raise ValueError("local refund is not available for correlation")
    if generic_refund is None:
        generic_refund = (
            _select_unlinked_local_refund(payment, payload, refund.amount)
            if allow_automatic_link
            else None
        )
    unlinked_same_amount_exists = PaymentRefund.objects.select_for_update().filter(
        payment=payment,
        provider_refund__isnull=True,
        method="apipay",
        status="pending",
        amount=refund.amount,
    ).exists()
    if generic_refund is None and not unlinked_same_amount_exists:
        generic_refund = PaymentRefund(
            payment=payment,
            method="apipay",
            requested_by=refund.requested_by,
            amount=refund.amount,
            reason=refund.reason,
        )
    if generic_refund is not None:
        linked_now = generic_refund.provider_refund_id is None
        generic_refund.provider_refund = refund
        generic_refund.amount = refund.amount
        generic_refund.status = generic_status
        generic_refund.reason = refund.reason or generic_refund.reason
        if generic_status == "completed":
            generic_refund.completed_at = (
                generic_refund.completed_at or timezone.now()
            )
        else:
            generic_refund.completed_at = None
        generic_refund.save()
    payment.order = order
    _sync_refund_totals(payment, order)
    record.total_refunded = (
        record.refunds.filter(status="completed").aggregate(total=Sum("amount"))[
            "total"
        ]
        or Decimal(0)
    )
    record.save(update_fields=["total_refunded", "updated_at"])
    changed = (
        previous_provider_status != refund.status
        or (
            generic_refund is not None
            and previous_generic_status != generic_refund.status
        )
        or linked_now
    )
    if changed:
        log_event(
            "payment",
            (
                f"Статус возврата по счёту: {generic_status}"
                if generic_refund is not None
                else "Возврат по счёту ожидает безопасной корреляции"
            ),
            user=None,
            order=payment.order,
            payload={
                "action": "apipay_refund_status",
                "payment_id": payment.pk,
                "refund_id": (
                    generic_refund.pk if generic_refund is not None else None
                ),
                "provider_refund_id": refund.refund_id,
                "status": generic_status,
                "amount": str(refund.amount),
                "correlation_pending": generic_refund is None,
            },
        )
    return changed


def _parsed_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    parsed = parse_datetime(value)
    if parsed is None:
        return None
    return parsed if timezone.is_aware(parsed) else timezone.make_aware(parsed)


def _normalized_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if timezone.is_aware(value) else timezone.make_aware(value)
    return _parsed_datetime(value)


def _invoice_payload_observed_at(
    payload: dict[str, Any], explicit: datetime | None
) -> datetime | None:
    if explicit is not None:
        return _normalized_datetime(explicit)
    status = str(payload.get("status") or "")
    status_time_field = {
        "paid": "paid_at",
        "cancelled": "cancelled_at",
        "expired": "expired_at",
        "error": "errored_at",
    }.get(status)
    return (
        _parsed_datetime(payload.get("updated_at"))
        or (
            _parsed_datetime(payload.get(status_time_field))
            if status_time_field
            else None
        )
    )


@transaction.atomic
def apply_invoice_status(
    record: ApiPayInvoice,
    invoice_payload: dict[str, Any],
    *,
    observed_at: datetime | None = None,
) -> bool:
    """Apply validated provider truth. Returns True only when state changed."""
    order_id = Payment.objects.values_list("order_id", flat=True).get(
        pk=record.payment_id
    )
    order = Order.all_objects.select_for_update().get(pk=order_id)
    payment = Payment.objects.select_for_update().get(pk=record.payment_id)
    record = ApiPayInvoice.objects.select_for_update().get(pk=record.pk)

    provider_status = str(invoice_payload.get("status") or "")
    if provider_status not in PROVIDER_INVOICE_STATUSES:
        raise ValueError("invoice.status is invalid")
    provider_error_code = str(invoice_payload.get("error_code") or "")
    status = provider_status
    if provider_status == "error":
        if provider_error_code == "invoice_already_paid":
            # Cancellation lost a race with money receipt. Do not free the
            # reservation until the documented paid synchronization arrives.
            status = "pending"
        elif provider_error_code == "invoice_already_cancelled":
            status = "cancelled"
    payload_invoice_id = invoice_payload.get("id")
    if payload_invoice_id is not None:
        try:
            provider_invoice_id = int(payload_invoice_id)
            if provider_invoice_id <= 0:
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise ValueError("invoice.id is invalid") from exc
        if (
            record.invoice_id is not None
            and provider_invoice_id != record.invoice_id
        ):
            raise ValueError("invoice.id does not match local invoice")

    amount_raw = invoice_payload.get("amount")
    if amount_raw is None and status in MONEY_RECEIVED_INVOICE_STATUSES:
        raise ValueError("invoice.amount is required for money-received status")
    if amount_raw is not None:
        try:
            webhook_amount = Decimal(str(amount_raw))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("invoice.amount is invalid") from exc
        if not webhook_amount.is_finite() or webhook_amount <= 0:
            raise ValueError("invoice.amount is invalid")
        if webhook_amount != payment.amount:
            raise ValueError("invoice.amount does not match payment")
    currency_raw = invoice_payload.get("currency")
    if currency_raw is not None and (
        not isinstance(currency_raw, str)
        or currency_raw.strip().upper() != order.currency
    ):
        raise ValueError("invoice.currency does not match order")
    if (
        status in MONEY_RECEIVED_INVOICE_STATUSES
        and order.currency != "KZT"
    ):
        raise ValueError("ApiPay money status is only valid for KZT orders")

    incoming_observed_at = _invoice_payload_observed_at(
        invoice_payload, observed_at
    )
    if (
        incoming_observed_at is not None
        and record.provider_status_at is not None
        and incoming_observed_at < record.provider_status_at
    ):
        return False
    if not _invoice_transition_allowed(record.status, status):
        return False

    previous_invoice_status = record.status
    previous_payment_status = payment.status
    record.status = status
    if incoming_observed_at is not None:
        record.provider_status_at = incoming_observed_at
    record.error_code = provider_error_code
    record.error_message = str(invoice_payload.get("error_message") or "")
    record.response_payload = invoice_payload
    if status in MONEY_RECEIVED_INVOICE_STATUSES:
        record.paid_at = (
            _parsed_datetime(invoice_payload.get("paid_at"))
            or record.paid_at
            or incoming_observed_at
            or timezone.now()
        )
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
                Decimal(0),
            )
            capacity = max(Decimal(0), order.total_amount - confirmed_total)
            pending = list(
                Payment.objects.select_for_update(of=("self",))
                .select_related("apipay_invoice")
                .filter(order=order, status__in=Payment.IN_PROGRESS_STATUSES)
                .exclude(pk=payment.pk)
                .order_by("-paid_at")
            )
            reserved = sum((row.amount for row in pending), Decimal(0))
            for pending_payment in pending:
                if reserved <= capacity:
                    break
                replacement_invoice = getattr(
                    pending_payment, "apipay_invoice", None
                )
                if (
                    replacement_invoice is not None
                    and replacement_invoice.status
                    not in ("cancelled", "expired", "error", "superseded")
                ):
                    # This invoice is still externally payable. Keep its
                    # reservation and visibility; reconciliation may cancel a
                    # phone invoice, while a QR must reach its own terminal.
                    continue
                if (
                    pending_payment.status == "received"
                    and replacement_invoice is None
                ):
                    # A manager already accepted these cash/card funds. Never
                    # make that evidence disappear automatically when an old QR
                    # is paid late; accounting must resolve the visible conflict.
                    continue
                pending_payment.status = "rejected"
                pending_payment.save(update_fields=["status"])
                reserved -= pending_payment.amount
            sync_payment_status(order)
    elif (
        status == "pending"
        and previous_invoice_status == "error"
        and payment.status == "rejected"
    ):
        # ApiPay documents error -> pending as a valid reconciliation. Restore
        # the reservation only when it still fits; otherwise keep the payment
        # visible as rejected and let a later paid event record real money.
        confirmed_total = sum(
            (
                row.net_amount
                for row in Payment.objects.select_for_update().filter(
                    order=order, status="confirmed"
                )
            ),
            Decimal(0),
        )
        reserved_total = sum(
            (
                row.amount
                for row in Payment.objects.select_for_update().filter(
                    order=order, status__in=Payment.IN_PROGRESS_STATUSES
                )
            ),
            Decimal(0),
        )
        available = max(
            Decimal(0),
            order.total_amount - confirmed_total - reserved_total,
        )
        if payment.amount <= available:
            payment.status = "received" if payment.received_at else "requested"
            payment.save(update_fields=["status"])
    elif status in ("cancelled", "expired", "error"):
        if payment.status in Payment.IN_PROGRESS_STATUSES:
            payment.status = "rejected"
            payment.save(update_fields=["status"])

    if (
        previous_payment_status != payment.status
        and status not in MONEY_RECEIVED_INVOICE_STATUSES
    ):
        sync_payment_status(order)
    record.save(update_fields=[
        "status", "error_code", "error_message", "response_payload",
        "paid_at", "provider_status_at", "updated_at",
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
