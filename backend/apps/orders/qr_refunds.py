"""Возврат по Kaspi QR через ссылку «Возврат ApiPay».

Kaspi не возвращает оплату по QR одним запросом магазина: ``POST
/invoices/{id}/refund`` отвечает ``refund_requires_buyer_confirmation``.
Поток ApiPay:

1. ``POST /qr-refunds/links`` — одноразовая ссылка для покупателя (денег не двигает);
2. покупатель открывает её и подтверждает в Kaspi — сессия ``customer_identified``
   (вебхук ``qr_refund.identified``), окно на возврат после этого короткое;
3. ``GET /qr-refunds/{id}/operations`` — покупки покупателя, выбираем оплату заказа;
4. ``POST /qr-refunds/{id}/execute`` — ЕДИНСТВЕННЫЙ денежный запрос. Повтор = второй
   возврат живых денег, поэтому отметка ``execute_requested_at`` коммитится до сети.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.common.crypto import SecretDecryptError, decrypt_secret, encrypt_secret
from apps.eventlog.services import log_event

from . import apipay as apipay_client
from .models import ApiPayInvoice, ApiPayQrRefund, Order, Payment, PaymentRefund
from .services import lock_live_order

log = logging.getLogger(__name__)

# Kaspi выполняет возврат синхронно — ждём дольше обычного запроса.
EXECUTE_TIMEOUT_SECONDS = 45
OPERATION_PAGES_LIMIT = 5
# Оплата заказа среди покупок покупателя: та же сумма и время рядом с оплатой счёта.
OPERATION_MATCH_WINDOW = timedelta(minutes=30)
# Снимок «покупатель подтвердил» после нашего execute не доказывает отказ, пока
# Kaspi мог ещё обрабатывать запрос.
EXECUTE_SETTLE_DELAY = timedelta(minutes=2)
ISSUING_STALE_AFTER = timedelta(minutes=10)
# Недоказанный исход разбирает поддержка ApiPay — проверяем его редко, но до конца.
UNCERTAIN_RECHECK_AFTER = timedelta(minutes=10)

# Отказы execute, которые ApiPay документирует как «деньги не отправлялись».
PRE_MONEY_EXECUTE_ERRORS = frozenset({
    "qr_refund_not_identified",
    "qr_refund_expired",
    "operation_not_returnable",
    "refund_amount_exceeds_available",
    "partial_refund_requires_return_items",
    "refund_insufficient_funds",
    "kaspi_error",
    "qr_refund_execution_disabled",
    "qr_refund_execution_context_unavailable",
    "kaspi_session_invalid",
    "kaspi_session_unavailable",
    "tariff_inactive",
    "qr_refund_actor_no_longer_authorized",
    "qr_refund_execution_attempts_exhausted",
    "organization_not_verified",
    "not_found",
})

ERROR_MESSAGES = {
    "refund_insufficient_funds": "На счёте Kaspi Pay не хватает денег на возврат. Пополните счёт и выпустите новую ссылку.",
    "qr_refund_expired": "Покупатель подтвердил слишком давно — срок возврата истёк. Выпустите новую ссылку.",
    "qr_refund_link_revoked": "Ссылка отозвана.",
    "qr_return_identity_timeout": "Покупатель не успел подтвердить возврат в Kaspi. Выпустите новую ссылку.",
    "qr_return_scan_timeout": "Покупатель не успел отсканировать возвратный QR. Выпустите новую ссылку.",
    "operation_not_returnable": "Kaspi не разрешает вернуть эту покупку.",
    "qr_refund_operation_choice": "Выберите покупку, по которой вернуть деньги.",
    "qr_refund_operation_not_found": "У покупателя нет подходящей оплаты. Проверьте, что ссылку открыл тот, кто платил.",
    "qr_refund_execution_uncertain": (
        "Kaspi не подтвердил исход возврата — деньги могли уйти. Не повторяйте возврат: "
        "проверьте его в Kaspi Pay и напишите в поддержку ApiPay."
    ),
}


def _money(value: object) -> Decimal | None:
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return amount if amount.is_finite() else None


def _message(code: str, fallback: str = "") -> str:
    return ERROR_MESSAGES.get(code) or fallback or "Возврат не выполнен. Выпустите новую ссылку."


def customer_url(session: ApiPayQrRefund) -> str:
    try:
        return decrypt_secret(session.customer_url_encrypted)
    except SecretDecryptError:
        return ""


def _settle_locked(session: ApiPayQrRefund, *, refund_status: str, amount: Decimal | None = None) -> None:
    """Перенести исход сессии на резерв возврата и пересчитать суммы оплаты."""
    refund = PaymentRefund.objects.select_for_update().get(pk=session.refund_id)
    payment = Payment.objects.select_for_update().get(pk=refund.payment_id)
    order = Order.all_objects.select_for_update().get(pk=payment.order_id)
    refund.status = refund_status
    if amount is not None and amount > 0:
        refund.amount = min(amount, payment.amount)
    refund.completed_at = timezone.now() if refund_status == "completed" else None
    refund.save(update_fields=["status", "amount", "completed_at", "updated_at"])
    payment.order = order
    apipay_client._sync_refund_totals(payment, order)


def _finish_locked(session: ApiPayQrRefund, status: str, error_code: str = "", error_message: str = "") -> None:
    session.status = status
    session.error_code = error_code[:100]
    session.error_message = _message(error_code, error_message) if error_code else error_message
    session.operations = []
    session.save()
    if status == "completed":
        _settle_locked(session, refund_status="completed", amount=session.refunded_amount)
        session.completed_at = session.completed_at or timezone.now()
        session.save(update_fields=["completed_at", "updated_at"])
    elif status in ("failed", "expired"):
        _settle_locked(session, refund_status="failed")
    # execution_uncertain: резерв остаётся «в обработке» — деньги могли уйти.


def start_qr_refund(invoice: ApiPayInvoice, user, *, amount: object = None, reason: str = "") -> tuple[ApiPayQrRefund, str]:
    """Зарезервировать сумму и выпустить ссылку покупателю. Возвращает сессию и ссылку."""
    if invoice.channel != "qr" or invoice.invoice_id is None:
        raise ValidationError({"detail": "Возврат по ссылке доступен только для оплаты по Kaspi QR.", "code": "qr_refund_unavailable"})
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"detail": "Укажите причину возврата.", "code": "refund_reason_required"})

    with transaction.atomic():
        order = lock_live_order(invoice.payment.order_id, user)
        payment = Payment.objects.select_for_update().get(pk=invoice.payment_id)
        payment.order = order
        if payment.status != "confirmed":
            raise ValidationError({"detail": "Вернуть можно только подтверждённую оплату.", "code": "payment_not_confirmed"})
        if ApiPayQrRefund.objects.filter(
            invoice__payment=payment, status__in=ApiPayQrRefund.ACTIVE_STATUSES
        ).exists():
            raise ValidationError({
                "detail": "Ссылка на возврат уже выпущена. Дождитесь покупателя или отзовите её.",
                "code": "qr_refund_in_progress",
            })
        value = apipay_client._validated_refund_amount(payment, amount)
        credentials = apipay_client.credentials_for_order(order)
        refund = PaymentRefund.objects.create(
            payment=payment, amount=value, method="apipay_qr", status="pending",
            reason=reason[:500], requested_by=user,
        )
        session = ApiPayQrRefund.objects.create(refund=refund, invoice=invoice)
        apipay_client._sync_refund_totals(payment, order)

    try:
        with apipay_client._provider_scope_fence(order.pk, user, require_live=True):
            response = apipay_client.api_request("POST", "/qr-refunds/links", {}, credentials=credentials)
    except (
        apipay_client.ApiPayAPIError, apipay_client.ApiPayConfigurationError, PermissionDenied, ValidationError,
    ) as exc:
        # Выпуск ссылки денег не двигает: любой сбой — просто неудавшийся возврат.
        code = getattr(exc, "error_code", "") or "qr_refund_link_failed"
        with transaction.atomic():
            session = ApiPayQrRefund.objects.select_for_update().get(pk=session.pk)
            _finish_locked(session, "failed", code, getattr(exc, "message", "") or "Ссылка на возврат не выпущена.")
        raise

    link = str(response.get("customer_url") or "")
    try:
        session_id = int(response["id"])
    except (KeyError, TypeError, ValueError):
        session_id = 0
    with transaction.atomic():
        session = ApiPayQrRefund.objects.select_for_update().get(pk=session.pk)
        if session_id <= 0 or not link:
            _finish_locked(session, "failed", "invalid_apipay_response", "Платёжный сервис не выдал ссылку на возврат.")
            raise ValidationError({"detail": session.error_message, "code": "invalid_apipay_response"})
        session.session_id = session_id
        session.status = str(response.get("status") or "awaiting_customer")[:32]
        session.link_expires_at = apipay_client._parsed_datetime(response.get("link_expires_at"))
        session.customer_url_encrypted = encrypt_secret(link)
        session.checked_at = timezone.now()
        session.save()
    log_event(
        "payment",
        f"Выпущена ссылка на возврат по Kaspi QR: {value} {order.currency}",
        user=user,
        order=order,
        payload={
            "action": "apipay_qr_refund_link_issued",
            "payment_id": payment.pk,
            "refund_id": refund.pk,
            "qr_refund_session_id": session_id,
            "amount": str(value),
            "reason": reason[:500],
        },
    )
    return session, link


def apply_qr_refund_snapshot(session_pk: int, snapshot: dict[str, Any], *, source: str) -> bool:
    """Применить снимок сессии ApiPay. True — покупатель подтвердил и нужно выполнить возврат."""
    status = snapshot.get("status")
    if not isinstance(status, str) or not status:
        raise ValueError("qr_refund_status_required")
    with transaction.atomic():
        session = ApiPayQrRefund.objects.select_for_update().get(pk=session_pk)
        session.checked_at = timezone.now()
        uncertain_resolved = session.status == "execution_uncertain" and status in ("completed", "failed", "expired")
        if session.status not in ApiPayQrRefund.ACTIVE_STATUSES and not uncertain_resolved:
            session.save(update_fields=["checked_at", "updated_at"])
            return False
        session.snapshot = {key: value for key, value in snapshot.items() if key not in ("qr_token_url", "qr_image_url")}
        if snapshot.get("client_name"):
            session.client_name = str(snapshot["client_name"])[:120]
        expires = apipay_client._parsed_datetime(snapshot.get("link_expires_at"))
        if expires:
            session.link_expires_at = expires
        error_code = str(snapshot.get("error_code") or "")
        error_message = str(snapshot.get("error_message") or "")

        if status == "completed":
            session.refunded_amount = _money(snapshot.get("refunded_amount")) or session.refunded_amount
            session.receipt_url = str(snapshot.get("receipt_url") or session.receipt_url)[:1000]
            _finish_locked(session, "completed")
        elif status in ("expired", "failed"):
            _finish_locked(session, status, error_code or "qr_refund_expired", error_message)
        elif status == "execution_uncertain":
            _finish_locked(session, "execution_uncertain", "qr_refund_execution_uncertain")
        elif status == "customer_identified" and session.execute_requested_at is not None:
            # Покупатель всё ещё «подтвердил», хотя execute отправлен: деньги не ушли,
            # но только когда Kaspi точно успел ответить. Повторять execute нельзя.
            if timezone.now() - session.execute_requested_at >= EXECUTE_SETTLE_DELAY:
                _finish_locked(session, "failed", error_code or "qr_refund_not_executed", "Возврат не выполнен. Выпустите новую ссылку.")
            else:
                session.save()
        else:
            session.status = status[:32]
            session.save()
            return status == "customer_identified" and session.execute_requested_at is None and not session.operations
    log.info("ApiPay QR refund session=%s status=%s source=%s", session.session_id, status, source)
    return False


def _fetch_operations(session: ApiPayQrRefund, credentials) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    cursor = ""
    for _ in range(OPERATION_PAGES_LIMIT):
        path = f"/qr-refunds/{session.session_id}/operations"
        if cursor:
            path += f"?cursor={apipay_client.urllib.parse.quote(cursor)}"
        page = apipay_client.api_request("GET", path, credentials=credentials)
        operations.extend(op for op in page.get("operations") or [] if isinstance(op, dict))
        cursor = str(page.get("next_cursor") or "")
        if not page.get("has_more") or not cursor:
            break
    return operations


def _returnable_choices(operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "ref": str(op.get("ref")),
            "amount": str(_money(op.get("amount")) or ""),
            "date": op.get("date"),
            "returnable": op.get("returnable"),
            "client_name": op.get("client_name"),
        }
        for op in operations
        if op.get("ref") and op.get("returnable") in ("full", "partial")
    ][:50]


def _matching_operation(session: ApiPayQrRefund, choices: list[dict[str, Any]]) -> dict[str, Any] | None:
    invoice = session.invoice
    payment = invoice.payment
    paid_at = invoice.paid_at or payment.paid_at
    same_amount = [op for op in choices if _money(op["amount"]) == payment.amount]
    if not paid_at:
        return same_amount[0] if len(same_amount) == 1 else None
    near = [
        op for op in same_amount
        if (moment := apipay_client._parsed_datetime(op.get("date"))) and abs(moment - paid_at) <= OPERATION_MATCH_WINDOW
    ]
    return near[0] if len(near) == 1 else None


def auto_execute_qr_refund(session_pk: int) -> None:
    """Покупатель подтвердил: найти оплату заказа и выполнить возврат, если выбор однозначен."""
    session = ApiPayQrRefund.objects.select_related("invoice__payment").get(pk=session_pk)
    if session.status != "customer_identified" or session.execute_requested_at is not None:
        return
    credentials = apipay_client.credentials_for_invoice(session.invoice)
    try:
        choices = _returnable_choices(_fetch_operations(session, credentials))
    except apipay_client.ApiPayAPIError as exc:
        # Список не получили — денег не двигали; сверка попробует снова, пока окно открыто.
        log.warning("ApiPay QR refund operations session=%s failed: %s", session.session_id, exc.error_code)
        return
    match = _matching_operation(session, choices)
    if match is None:
        with transaction.atomic():
            session = ApiPayQrRefund.objects.select_for_update().get(pk=session_pk)
            if session.execute_requested_at is None and session.status == "customer_identified":
                session.operations = choices
                code = "qr_refund_operation_choice" if choices else "qr_refund_operation_not_found"
                session.error_code = code
                session.error_message = _message(code)
                session.save()
        return
    execute_qr_refund(session_pk, match["ref"], user=None, choices=choices)


def execute_qr_refund(session_pk: int, operation_ref: str, *, user, choices: list[dict[str, Any]] | None = None) -> ApiPayQrRefund:
    """Единственный денежный запрос сессии. Второй вызов отказывает до сети."""
    with transaction.atomic():
        session = ApiPayQrRefund.objects.select_for_update().select_related("refund", "invoice__payment").get(pk=session_pk)
        if session.execute_requested_at is not None:
            raise ValidationError({"detail": "Возврат по этой ссылке уже отправлен.", "code": "qr_refund_already_executed"})
        if session.status != "customer_identified":
            raise ValidationError({"detail": "Покупатель ещё не подтвердил возврат в Kaspi.", "code": "qr_refund_not_identified"})
        known = {op["ref"]: op for op in (choices if choices is not None else session.operations)}
        operation = known.get(operation_ref)
        if operation is None:
            raise ValidationError({"detail": "Выберите покупку из списка покупателя.", "code": "operation_not_returnable"})
        session.execute_requested_at = timezone.now()
        session.operation_ref = operation_ref[:255]
        session.status = "executing"
        session.error_code = ""
        session.error_message = ""
        session.save()
        amount = session.refund.amount
        credentials = apipay_client.credentials_for_invoice(session.invoice)

    payload: dict[str, Any] = {"operation_ref": operation_ref}
    operation_amount = _money(operation.get("amount"))
    if operation_amount is None or amount < operation_amount:
        payload["amount"] = float(amount)
    try:
        response = apipay_client.api_request(
            "POST", f"/qr-refunds/{session.session_id}/execute", payload,
            credentials=credentials, timeout=EXECUTE_TIMEOUT_SECONDS,
        )
    except apipay_client.ApiPayAPIError as exc:
        with transaction.atomic():
            session = ApiPayQrRefund.objects.select_for_update().get(pk=session_pk)
            if exc.error_code in PRE_MONEY_EXECUTE_ERRORS:
                _finish_locked(session, "failed", exc.error_code, exc.message)
            else:
                # Таймаут, обрыв или неизвестный ответ: исход проверит GET в сверке.
                session.error_code = exc.error_code[:100]
                session.error_message = "Проверяем исход возврата в Kaspi…"
                session.save(update_fields=["error_code", "error_message", "updated_at"])
        _log_execution(session, user, amount)
        return session

    with transaction.atomic():
        session = ApiPayQrRefund.objects.select_for_update().get(pk=session_pk)
        if response.get("status") == "completed" and not response.get("error_code"):
            session.refunded_amount = _money(response.get("refunded_amount")) or amount
            session.receipt_url = str(response.get("receipt_url") or "")[:1000]
            _finish_locked(session, "completed")
        else:
            # 202: запрос принят, но исход не доказан. Повторять нельзя.
            _finish_locked(session, "execution_uncertain", "qr_refund_execution_uncertain")
    _log_execution(session, user, amount)
    return session


def _log_execution(session: ApiPayQrRefund, user, amount: Decimal) -> None:
    try:
        payment = session.invoice.payment
        log_event(
            "payment",
            f"Возврат по Kaspi QR {amount} {payment.order.currency}: {session.status}",
            user=user,
            order=payment.order,
            payload={
                "action": "apipay_qr_refund_executed",
                "payment_id": payment.pk,
                "refund_id": session.refund_id,
                "qr_refund_session_id": session.session_id,
                "status": session.status,
                "error_code": session.error_code,
            },
        )
    except Exception:
        # Возврат уже отправлен и сохранён: сбой журнала не должен выглядеть как ошибка денег.
        log.exception("Could not log ApiPay QR refund execution session=%s", session.session_id)


def refresh_qr_refund(session_pk: int, *, source: str) -> None:
    """Прочитать сессию у ApiPay и довести её: применить статус и, если нужно, выполнить возврат."""
    session = ApiPayQrRefund.objects.select_related("invoice").get(pk=session_pk)
    if session.session_id is None:
        if session.status == "issuing" and timezone.now() - session.created_at > ISSUING_STALE_AFTER:
            with transaction.atomic():
                locked = ApiPayQrRefund.objects.select_for_update().get(pk=session_pk)
                _finish_locked(locked, "failed", "invalid_apipay_response", "Ссылка на возврат не была выпущена.")
        return
    credentials = apipay_client.credentials_for_invoice(session.invoice)
    snapshot = apipay_client.api_request("GET", f"/qr-refunds/{session.session_id}", credentials=credentials)
    if apply_qr_refund_snapshot(session_pk, snapshot, source=source):
        auto_execute_qr_refund(session_pk)


def revoke_qr_refund(session_pk: int, user) -> ApiPayQrRefund:
    session = ApiPayQrRefund.objects.select_related("invoice__payment").get(pk=session_pk)
    if session.status not in ("issuing", "awaiting_customer"):
        raise ValidationError({"detail": "Покупатель уже открыл ссылку — её нельзя отозвать.", "code": "qr_refund_link_not_revocable"})
    with transaction.atomic():
        lock_live_order(session.invoice.payment.order_id, user)
    if session.session_id is not None:
        credentials = apipay_client.credentials_for_invoice(session.invoice)
        try:
            snapshot = apipay_client.api_request("DELETE", f"/qr-refunds/links/{session.session_id}", credentials=credentials)
        except apipay_client.ApiPayAPIError as exc:
            if exc.error_code == "qr_refund_link_not_revocable":
                refresh_qr_refund(session_pk, source="revoke")
            raise
        apply_qr_refund_snapshot(session_pk, snapshot, source="revoke")
    with transaction.atomic():
        session = ApiPayQrRefund.objects.select_for_update().get(pk=session_pk)
        if session.status in ApiPayQrRefund.ACTIVE_STATUSES:
            _finish_locked(session, "expired", "qr_refund_link_revoked")
    log_event(
        "payment",
        "Ссылка на возврат по Kaspi QR отозвана",
        user=user,
        order=session.invoice.payment.order,
        payload={"action": "apipay_qr_refund_link_revoked", "refund_id": session.refund_id, "qr_refund_session_id": session.session_id},
    )
    return session


def reconcile_qr_refunds(*, limit: int) -> dict[str, int]:
    """Сверка активных сессий: вебхук мог потеряться, а окно после подтверждения короткое."""
    stats = {"selected": 0, "checked": 0, "failed": 0}
    sessions = list(
        ApiPayQrRefund.objects.filter(
            Q(status__in=ApiPayQrRefund.ACTIVE_STATUSES)
            | Q(status="execution_uncertain", checked_at__lt=timezone.now() - UNCERTAIN_RECHECK_AFTER)
        )
        .order_by(F("checked_at").asc(nulls_first=True), "pk")
        .values_list("pk", flat=True)[:limit]
    )
    stats["selected"] = len(sessions)
    for pk in sessions:
        try:
            refresh_qr_refund(pk, source="reconciliation")
            stats["checked"] += 1
        except (apipay_client.ApiPayAPIError, apipay_client.ApiPayConfigurationError, ValueError):
            stats["failed"] += 1
            log.warning("ApiPay QR refund reconciliation failed session_pk=%s", pk, exc_info=True)
    return stats


def serialize_qr_refund(session: ApiPayQrRefund) -> dict[str, Any]:
    """Состояние для кассы. Ссылку показываем, пока покупатель её не открыл."""
    return {
        "id": session.pk,
        "status": session.status,
        "amount": str(session.refund.amount),
        "refunded_amount": str(session.refunded_amount) if session.refunded_amount is not None else None,
        "client_name": session.client_name or None,
        "customer_url": customer_url(session) if session.status == "awaiting_customer" else None,
        "link_expires_at": session.link_expires_at,
        "operations": session.operations if session.status == "customer_identified" else [],
        "receipt_url": session.receipt_url or None,
        "error_code": session.error_code or None,
        "error_message": session.error_message or None,
        "created_at": session.created_at,
    }
