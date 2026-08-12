from decimal import Decimal, InvalidOperation
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from apps.eventlog.services import log_event
from apps.notifications.services import notify
from apps.clients.services import is_payment_window_open
from .models import Order, OrderItem, Payment, StatusChangeRequest
from .statuses import PUBLIC_MANUAL_STATUSES, PUBLIC_STATUS_LABELS, public_status_label


MAX_MONEY = Decimal("9999999999.99")


def _locked_payment_order(order: Order) -> Order:
    """Serialize payment choices and derived totals through the order row."""
    return (
        Order.objects.select_for_update(of=("self",))
        .select_related("client__user", "store")
        .prefetch_related("items", "payments")
        .get(pk=order.pk)
    )


def _locked_payment_with_order(payment: Payment) -> tuple[Payment, Order]:
    """Use one lock order (Order -> Payment) across every payment transition."""
    order = Order.objects.select_for_update().get(pk=payment.order_id)
    locked_payment = Payment.objects.select_for_update().get(pk=payment.pk)
    # Reuse the row-locked instance for totals, currency and audit logging.
    locked_payment.order = order
    return locked_payment, order


def _sync_payment_instance(original: Payment, locked: Payment) -> Payment:
    """Keep the service's existing in-memory mutation contract after locking."""
    if original is not locked:
        original.refresh_from_db()
    return original


def _positive_money(raw, *, detail: str, code: str) -> Decimal:
    """Validate public money input before it reaches a DecimalField/database."""
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError({"detail": detail, "code": code}) from exc
    if not value.is_finite() or value <= 0 or value > MAX_MONEY:
        raise ValidationError({"detail": detail, "code": code})
    try:
        quantized = value.quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise ValidationError({"detail": detail, "code": code}) from exc
    if value != quantized:
        raise ValidationError({"detail": detail, "code": code})
    return quantized


def _status_message(prefix: str, old: str, new: str) -> str:
    old_label = public_status_label(old)
    new_label = public_status_label(new)
    return (f"{prefix}: {new_label}" if old_label == new_label
            else f"{prefix}: {old_label} → {new_label}")


def _validate_payment_open(order: Order) -> None:
    if order.status != "shipped":
        raise ValidationError(
            {"detail": "Оплата доступна только после отгрузки", "code": "payment_not_open"}
        )
    if order.store and not is_payment_window_open(order.store, timezone.localdate()):
        raise ValidationError(
            {"detail": f"Оплата для магазина «{order.store.name}» сегодня недоступна",
             "code": "payment_window_closed"}
        )


# Строчные формы для фразы журнала: «Оплата 100 KZT принята». Это не копия
# labels.PAYMENT_STATUS_LABELS — там подпись колонки («Получена»), здесь
# сказуемое внутри предложения, поэтому наборы намеренно разные.
PAYMENT_STAGE_LABELS = {
    "requested": "запрошена", "received": "принята",
    "confirmed": "подтверждена бухгалтером", "rejected": "отклонена",
}


def _set_payment_stage(payment: Payment, status: str, user) -> Payment:
    """Перевести оплату на следующий шаг цепочки с фиксацией автора и времени."""
    stamp = {
        "received": ("received_by", "received_at"),
        "confirmed": ("confirmed_by", "confirmed_at"),
    }.get(status)
    payment.status = status
    fields = ["status"]
    if stamp:
        by_field, at_field = stamp
        setattr(payment, by_field, user)
        setattr(payment, at_field, timezone.now())
        fields += [by_field, at_field]
    payment.save(update_fields=fields)
    log_event(
        "payment",
        f"Оплата {payment.amount} {payment.order.currency} {PAYMENT_STAGE_LABELS[status]}",
              user=user, order=payment.order,
              payload={"payment_id": payment.id, "amount": str(payment.amount),
                       "currency": payment.order.currency,
                       "payment_stage": status},
    )
    return payment


@transaction.atomic
def add_payment(order: Order, amount, user, method="cash", stage="received",
                note="") -> Payment:
    """Начало цепочки оплаты: «запрошена» (счёт выставлен) или «принята» (деньги у менеджера)."""
    order = _locked_payment_order(order)
    _validate_payment_open(order)
    if stage not in ("requested", "received"):
        raise ValidationError({"detail": "Недопустимый шаг оплаты", "code": "bad_stage"})
    amount = _positive_money(
        amount,
        detail="Сумма оплаты должна быть положительным денежным значением",
        code="invalid_amount",
    )
    if method not in Payment.CASHIER_METHODS:
        raise ValidationError(
            {"detail": "Недопустимый способ оплаты", "code": "bad_method"})
    confirmed = sum(
        (payment.net_amount for payment in order.payments.all()
         if payment.status == "confirmed"),
        Decimal("0"),
    )
    reserved = sum(
        (payment.amount for payment in order.payments.all()
         if payment.status in Payment.IN_PROGRESS_STATUSES),
        Decimal("0"),
    )
    available = max(Decimal("0"), order.total_amount - confirmed - reserved)
    if amount > available:
        raise ValidationError({
            "detail": f"Доступно к оплате: {available} {order.currency}",
            "code": "payment_exceeds_remaining",
        })
    payment = Payment.objects.create(
        order=order, amount=amount, method=method, status=stage, note=note,
        recorded_by=user,
        **({"received_by": user, "received_at": timezone.now()}
           if stage == "received" else {}))
    log_event(
        "payment",
        f"Оплата {amount} {order.currency} ({method}) {PAYMENT_STAGE_LABELS[stage]}",
              user=user, order=order,
              payload={"payment_id": payment.id, "amount": str(amount),
                       "currency": order.currency, "method": method,
                       "payment_stage": stage},
    )
    return payment


@transaction.atomic
def add_mixed_payments(order: Order, parts, user, note="") -> list[Payment]:
    """Record a split payment as one all-or-nothing cashier operation.

    A client may settle one order with several cashier methods (for example,
    part cash and part Kaspi).  Existing unconfirmed payments reserve their
    amount as well, so a second cashier cannot allocate more than the actual
    outstanding balance.
    """
    locked = (Order.objects.select_for_update()
              .prefetch_related("items", "payments")
              .get(pk=order.pk))
    _validate_payment_open(locked)
    if not isinstance(parts, list) or not parts:
        raise ValidationError({"detail": "Добавьте хотя бы один способ оплаты",
                               "code": "empty_payment_parts"})
    if len(parts) > len(Payment.CASHIER_METHODS):
        raise ValidationError({"detail": "Слишком много способов оплаты",
                               "code": "too_many_payment_parts"})

    normalized: list[tuple[str, Decimal]] = []
    seen: set[str] = set()
    for part in parts:
        if not isinstance(part, dict):
            raise ValidationError({"detail": "Некорректная часть оплаты",
                                   "code": "bad_payment_part"})
        method = part.get("method") or ""
        if method not in Payment.CASHIER_METHODS:
            raise ValidationError({"detail": "Недопустимый способ оплаты",
                                   "code": "bad_method"})
        if method in seen:
            raise ValidationError({"detail": "Каждый способ оплаты укажите один раз",
                                   "code": "duplicate_payment_method"})
        seen.add(method)
        amount = _positive_money(
            part.get("amount"),
            detail="Сумма каждой части должна быть положительным денежным значением",
            code="invalid_amount",
        )
        normalized.append((method, amount))

    confirmed = sum(
        (payment.net_amount for payment in locked.payments.all()
         if payment.status == "confirmed"), Decimal("0"))
    reserved = sum(
        (payment.amount for payment in locked.payments.all()
         if payment.status in Payment.IN_PROGRESS_STATUSES), Decimal("0"))
    available = max(Decimal("0"), locked.total_amount - confirmed - reserved)
    requested = sum((amount for _, amount in normalized), Decimal("0"))
    if requested > available:
        raise ValidationError({
            "detail": f"Доступно к распределению: {available} {locked.currency}",
            "code": "payment_exceeds_remaining",
        })
    if locked.currency != "KZT" and any(
        method in ("kaspi", "invoice") for method, _amount in normalized
    ):
        raise ValidationError({
            "detail": "QR и счёт на оплату доступны только для заказов в тенге.",
            "code": "online_payment_kzt_only",
        })

    created = [
        add_payment(locked, amount, user, method=method, stage="received", note=note)
        for method, amount in normalized
    ]
    log_event(
        "payment",
        f"Смешанная оплата {requested} {locked.currency}: {len(created)} частей",
        user=user,
        order=locked,
        payload={
            "action": "mixed_payment_created",
            "payment_ids": [payment.id for payment in created],
            "amount": str(requested),
            "currency": locked.currency,
            "parts": [
                {"method": payment.method, "amount": str(payment.amount)}
                for payment in created
            ],
        },
    )
    return created


@transaction.atomic
def create_client_payment(order: Order, method: str, user, amount=None) -> Payment:
    if method not in ("invoice", "kaspi", "cash", "card"):
        raise ValidationError({"detail": "Недопустимый способ оплаты", "code": "bad_method"})
    order = _locked_payment_order(order)
    _validate_payment_open(order)
    remaining = order.total_amount - order.paid_total
    if remaining <= 0:
        raise ValidationError({"detail": "Заказ уже оплачен", "code": "already_paid"})
    open_payments = list(
        order.payments.select_for_update()
        .filter(status__in=Payment.IN_PROGRESS_STATUSES)
        .order_by("-paid_at")
    )
    payment = None
    owned_open_payments = [
        row for row in open_payments if row.recorded_by_id == getattr(user, "pk", None)
    ]
    if amount in (None, "") and len(owned_open_payments) == 1:
        candidate = owned_open_payments[0]
        if not hasattr(candidate, "apipay_invoice"):
            payment = candidate
    reserved = sum((row.amount for row in open_payments), Decimal("0"))
    available = max(Decimal("0"), remaining - reserved)
    if payment is not None:
        # Legacy clients omit amount when switching their own open payment.
        # Only that payment's reservation is reusable; every other reservation
        # must continue to reduce the available balance.
        other_reserved = reserved - payment.amount
        requested_amount = max(Decimal("0"), remaining - other_reserved)
        if requested_amount <= 0:
            raise ValidationError({
                "detail": f"Доступно для оплаты: 0 {order.currency}",
                "code": "payment_exceeds_remaining",
            })
    elif amount in (None, "") and not open_payments:
        requested_amount = remaining
    else:
        requested_amount = _positive_money(
            available if amount in (None, "") else amount,
            detail="Сумма оплаты должна быть положительным денежным значением",
            code="invalid_amount",
        )
    if requested_amount > available and open_payments and payment is None:
        raise ValidationError({
            "detail": f"Доступно для новой оплаты: {available} {order.currency}",
            "code": "payment_exceeds_remaining",
        })
    if requested_amount > remaining:
        raise ValidationError({
            "detail": f"Остаток по заказу: {remaining} {order.currency}",
            "code": "payment_exceeds_remaining",
        })
    stage = "received" if method in ("kaspi", "card") else "requested"
    # Старые клиенты без суммы продолжают менять обычную заявку на другой
    # способ. Провайдерский счёт переиспользовать нельзя: у него уже зафиксированы
    # сумма и idempotency key.
    if payment is None:
        created = True
        payment = Payment.objects.create(
            order=order, amount=requested_amount, method=method, status=stage,
            recorded_by=user,
            **({"received_by": user, "received_at": timezone.now()}
               if stage == "received" else {}),
        )
    else:
        created = False
        payment.amount = requested_amount
        payment.method = method
        payment.status = stage
        payment.recorded_by = user
        payment.received_by = user if stage == "received" else None
        payment.received_at = timezone.now() if stage == "received" else None
        payment.save(update_fields=[
            "amount", "method", "status", "recorded_by", "received_by", "received_at",
        ])
    active_methods = {
        row.method for row in order.payments.filter(
            status__in=(*Payment.IN_PROGRESS_STATUSES, "confirmed")
        )
    }
    active_methods.add(method)
    public_method = "mixed" if len(active_methods) > 1 else (
        "invoice" if method == "card" else method
    )
    order.payment_method = public_method
    order.settlement_intent = "instant"
    order.debt_requested = False
    order.save(update_fields=["payment_method", "settlement_intent", "debt_requested"])
    action = "инициировал" if created else "обновил"
    log_event(
        "payment", f"Клиент {action} оплату {requested_amount} {order.currency} ({method})",
              user=user, order=order,
              payload={"payment_id": payment.id, "amount": str(requested_amount), "method": method,
                       "currency": order.currency, "payment_stage": stage},
    )
    return payment


@transaction.atomic
def release_client_payment(payment: Payment, user) -> Payment:
    """Освободить зарезервированную часть, не скрывая возможную позднюю оплату."""
    payment, order = _locked_payment_with_order(payment)
    invoice = getattr(payment, "apipay_invoice", None)
    if payment.status not in Payment.IN_PROGRESS_STATUSES:
        if (
            payment.status == "rejected"
            and invoice is not None
            and invoice.status in {
                "cancelled", "expired", "error", "superseded",
            }
        ):
            # A synchronous provider cancellation already rejected the
            # Payment through the shared status engine. Treat the following
            # release call as the successful, idempotent completion of that
            # same user action; never extend this to paid/confirmed money.
            return _sync_payment_instance(payment, payment)
        raise ValidationError({
            "detail": "Эта заявка уже завершена.",
            "code": "payment_not_in_progress",
        })
    if (
        invoice is not None
        and invoice.channel == "qr"
        and invoice.invoice_id is None
        and invoice.status == "creating"
    ):
        raise ValidationError({
            "detail": (
                "Создание QR ещё сверяется с платёжным сервисом. "
                "Нельзя освобождать сумму до завершения сверки."
            ),
            "code": "qr_issue_recovery_pending",
        })
    payment.status = "rejected"
    payment.save(update_fields=["status"])
    if hasattr(payment, "apipay_invoice"):
        invoice = payment.apipay_invoice
        invoice.status = "superseded"
        invoice.save(update_fields=["status", "updated_at"])
    log_event(
        "payment",
        f"Клиент выбрал другой способ вместо оплаты {payment.amount} {order.currency}",
        user=user,
        order=order,
        payload={
            "action": "client_payment_released",
            "payment_id": payment.id,
            "provider_invoice_may_still_be_payable": hasattr(payment, "apipay_invoice"),
        },
    )
    return _sync_payment_instance(payment, payment)


@transaction.atomic
def request_client_debt(order: Order, user) -> Order:
    """Зафиксировать выбор «В долг» без создания денежной оплаты."""
    order = _locked_payment_order(order)
    if order.status != "shipped":
        raise ValidationError({"detail": "Долг фиксируется после отгрузки",
                               "code": "invalid_status"})
    if order.payments.select_for_update().filter(
        status__in=Payment.IN_PROGRESS_STATUSES,
    ).exists():
        raise ValidationError({
            "detail": (
                "Сначала завершите текущую оплату или выберите «Другой способ» "
                "у своей заявки."
            ),
            "code": "payment_in_progress",
        })
    order.payment_method = "debt"
    order.settlement_intent = "debt"
    order.debt_requested = True
    order.save(update_fields=["payment_method", "settlement_intent", "debt_requested"])
    log_event("debt_override", "Клиент запросил долг", user=user, order=order,
              payload={"payment_method": "debt"})
    return order


# Разрешённые переходы цепочки подтверждения оплаты.
# Бухгалтер (он же кассир) сверяет и сразу финализирует: received → confirmed.
PAYMENT_TRANSITIONS = {
    "requested": "received",
    "received": "confirmed",
}


def _advance_payment(payment: Payment, expected_from: str, user) -> Payment:
    if payment.status != expected_from:
        raise ValidationError(
            {"detail": f"Оплата сейчас в статусе «{PAYMENT_STAGE_LABELS.get(payment.status, payment.status)}»",
             "code": "invalid_payment_stage"})
    return _set_payment_stage(payment, PAYMENT_TRANSITIONS[expected_from], user)


@transaction.atomic
def receive_payment(payment: Payment, user) -> Payment:
    """Менеджер/оператор отметил: деньги получены от клиента."""
    original = payment
    payment, _order = _locked_payment_with_order(payment)
    _advance_payment(payment, "requested", user)
    return _sync_payment_instance(original, payment)


@transaction.atomic
def accountant_confirm_payment(payment: Payment, user) -> Payment:
    """Бухгалтер (касса) сверил и подтвердил оплату — деньги учтены сразу."""
    original = payment
    payment, order = _locked_payment_with_order(payment)
    provider = getattr(payment, "apipay_invoice", None)
    if provider is not None and provider.status != "paid":
        raise ValidationError({
            "detail": (
                "Онлайн-оплата подтверждается автоматически после поступления "
                "уведомления от платёжного сервиса."
            ),
            "code": "provider_payment_auto_confirmation",
        })
    confirmed_total = sum(
        (
            row.net_amount
            for row in order.payments.select_for_update().filter(
                status="confirmed"
            )
        ),
        Decimal("0"),
    )
    available = max(Decimal("0"), order.total_amount - confirmed_total)
    if payment.amount > available:
        raise ValidationError({
            "detail": (
                "Подтверждение создаст переплату. "
                f"Текущий остаток заказа: {available} {order.currency}."
            ),
            "code": "payment_confirmation_overpayment",
        })
    _advance_payment(payment, "received", user)
    _apply_payment_status(order, user)
    return _sync_payment_instance(original, payment)


@transaction.atomic
def reopen_confirmed_payment(payment: Payment, user) -> Payment:
    """Вернуть ошибочно подтверждённую оплату на повторное подтверждение.

    Денежный итог заказа пересчитывается сразу, а исходное подтверждение и
    отмена остаются отдельными append-only событиями в журнале.
    """
    original = payment
    payment, order = _locked_payment_with_order(payment)
    if payment.status != "confirmed":
        raise ValidationError({
            "detail": "Вернуть можно только подтверждённую оплату",
            "code": "invalid_payment_stage",
        })
    if hasattr(payment, "apipay_invoice"):
        raise ValidationError({
            "detail": (
                "Онлайн-оплату нельзя вернуть в очередь без возврата денег. "
                "Используйте действие «Оформить возврат»."
            ),
            "code": "provider_payment_requires_refund",
        })
    previous_confirmed_by = payment.confirmed_by_id
    previous_confirmed_at = payment.confirmed_at
    payment.status = "received"
    payment.confirmed_by = None
    payment.confirmed_at = None
    payment.save(update_fields=["status", "confirmed_by", "confirmed_at"])
    log_event(
        "payment",
        f"Оплата {payment.amount} {payment.order.currency} возвращена на подтверждение",
        user=user,
        order=payment.order,
        payload={
            "payment_id": payment.id,
            "amount": str(payment.amount),
            "currency": payment.order.currency,
            "method": payment.method,
            "payment_stage": "received",
            "action": "reopened",
            "previous_confirmed_by": previous_confirmed_by,
            "previous_confirmed_at": (
                previous_confirmed_at.isoformat() if previous_confirmed_at else None
            ),
        },
    )
    _apply_payment_status(order, user)
    return _sync_payment_instance(original, payment)


@transaction.atomic
def reject_payment(payment: Payment, user) -> Payment:
    original = payment
    payment, _order = _locked_payment_with_order(payment)
    if payment.status in ("confirmed", "rejected"):
        raise ValidationError(
            {"detail": "Оплата уже финализирована", "code": "invalid_payment_stage"})
    previous_stage = payment.status
    payment.status = "rejected"
    payment.save(update_fields=["status"])
    log_event(
        "payment",
        f"Оплата {payment.amount} {payment.order.currency} отклонена",
        user=user,
        order=payment.order,
        payload={
            "payment_id": payment.id,
            "amount": str(payment.amount),
            "currency": payment.order.currency,
            "method": payment.method,
            "payment_stage": "rejected",
            "previous_payment_stage": previous_stage,
            "action": "rejected",
        },
    )
    return _sync_payment_instance(original, payment)


@transaction.atomic
def restore_rejected_payment(payment: Payment, user) -> Payment:
    """Вернуть ошибочно отклонённую кассовую оплату в рабочую очередь."""
    original = payment
    payment, order = _locked_payment_with_order(payment)
    if payment.status != "rejected":
        raise ValidationError({
            "detail": "Восстановить можно только отклонённую оплату",
            "code": "invalid_payment_stage",
        })
    invoice = getattr(payment, "apipay_invoice", None)
    if (
        invoice
        and invoice.invoice_id is None
        and invoice.status != "creating"
    ):
        raise ValidationError({
            "detail": (
                "Прежний ключ счёта закрыт. Создайте новую платёжную "
                "операцию с новым ключом."
            ),
            "code": "provider_issue_key_retired",
        })
    if (
        invoice
        and invoice.invoice_id is not None
        and invoice.status in ("cancelled", "expired", "error", "superseded")
    ):
        raise ValidationError({
            "detail": "Отменённый счёт восстановить нельзя — создайте новый счёт на оплату.",
            "code": "provider_invoice_closed",
        })
    confirmed = sum(
        (
            row.net_amount
            for row in order.payments.select_for_update().filter(status="confirmed")
        ),
        Decimal("0"),
    )
    reserved = sum(
        (
            row.amount
            for row in order.payments.select_for_update().filter(
                status__in=Payment.IN_PROGRESS_STATUSES
            )
        ),
        Decimal("0"),
    )
    available = max(Decimal("0"), order.total_amount - confirmed - reserved)
    if payment.amount > available:
        raise ValidationError({
            "detail": (
                f"Восстановить нельзя: свободный остаток заказа "
                f"{available} {order.currency}."
            ),
            "code": "payment_exceeds_remaining",
        })
    restored_stage = "received" if payment.received_at else "requested"
    payment.status = restored_stage
    payment.save(update_fields=["status"])
    log_event(
        "payment",
        f"Оплата {payment.amount} {order.currency} восстановлена",
        user=user,
        order=order,
        payload={
            "payment_id": payment.id,
            "amount": str(payment.amount),
            "currency": order.currency,
            "method": payment.method,
            "payment_stage": restored_stage,
            "previous_payment_stage": "rejected",
            "action": "restored",
        },
    )
    return _sync_payment_instance(original, payment)


def _payment_status_for(order: Order) -> str:
    paid = order.paid_total
    if paid <= 0:
        return "unpaid"
    if paid >= order.total_amount and order.total_amount > 0:
        return "settled"
    return "partial"


def sync_payment_status(order: Order) -> str:
    """Привести payment_status в соответствие с фактическими оплатами. Идемпотентно.

    Без пользователя — используется и при оплате, и для бэкфилла легаси-данных.
    """
    order.refresh_from_db()
    new = _payment_status_for(order)
    if new != order.payment_status:
        order.payment_status = new
        order.save(update_fields=["payment_status"])
    return new


def _apply_payment_status(order: Order, user) -> None:
    old = order.payment_status
    new = sync_payment_status(order)
    if new != old:
        log_event("payment", f"Статус оплаты: {new}", user=user, order=order,
                  payload={"payment_status": new})


# Логистика: подтверждение → въезд → загрузка → отгрузка. Оплата — отдельно, после shipped.
ALLOWED_TRANSITIONS = {
    "draft": {"pending", "confirmed", "cancelled"},
    "pending": {"confirmed", "rejected", "cancelled"},
    "confirmed": {"arrived", "cancelled"},
    "arrived": {"loading", "cancelled"},
    "loading": {"loaded", "cancelled"},
    "loaded": {"shipped", "cancelled"},
}


@transaction.atomic
def transition(order: Order, to_status: str, user, message: str | None = None) -> Order:
    allowed = ALLOWED_TRANSITIONS.get(order.status, set())
    if to_status not in allowed:
        raise ValidationError(
            {"detail": _status_message("Недопустимый переход", order.status, to_status),
             "code": "invalid_transition"})
    old = order.status
    order.status = to_status
    order.save(update_fields=["status"])
    log_event("status", message or _status_message("Статус", old, to_status),
              user=user, order=order, payload={"from": old, "to": to_status})
    return order


@transaction.atomic
def confirm_order(order: Order, user, prices: dict | None = None) -> Order:
    if order.status not in ("draft", "pending"):
        raise ValidationError(
            {"detail": "Подтвердить можно только новый заказ", "code": "invalid_status"})
    _apply_prices(order, prices or {}, user)
    return transition(order, "confirmed", user, "Заказ подтверждён")


@transaction.atomic
def repeat_order(source: Order, user) -> Order:
    """Создать независимый заказ из старого документа с сегодняшней датой."""
    from apps.warehouse.services import ensure_products_available

    source = (
        Order.objects.select_for_update(of=("self",))
        .select_related("client__user", "store")
        .prefetch_related("items__product")
        .get(pk=source.pk)
    )
    items = list(source.items.all())
    if not items:
        raise ValidationError({
            "detail": "В исходном заказе нет позиций",
            "code": "repeat_empty_order",
        })
    unavailable = [item.product_label for item in items
                   if item.product_id is None or not item.product.is_active]
    if unavailable:
        raise ValidationError({
            "detail": "Нельзя повторить заказ: товар удалён или находится в архиве — "
                      + ", ".join(unavailable),
            "code": "repeat_product_unavailable",
        })
    ensure_products_available(item.product for item in items)

    has_complete_prices = all(
        item.unit_price is not None and item.unit_price > 0 for item in items
    )
    repeated = Order.objects.create(
        client=source.client,
        currency=source.currency,
        department=source.department,
        transport_type=source.transport_type,
        store=source.store,
        settlement_intent=source.settlement_intent,
        payment_method=source.payment_method,
        truck_number=source.truck_number,
        truck_number_set_by=user if source.truck_number else None,
        arrival_date=timezone.localdate(),
        notes=source.notes,
        status="pending" if has_complete_prices else "draft",
        created_by=user,
        repeated_from=source,
    )
    copies = [
        OrderItem.objects.create(
            order=repeated,
            product=item.product,
            quantity=item.quantity,
            unit_price=item.unit_price,
        )
        for item in items
    ]
    # Цены — исторический снимок шаблона. Если сотрудник может подтверждать,
    # новый заказ сразу попадает в «Ожидание въезда», иначе остаётся заявкой.
    if has_complete_prices and user.has_perm_code("orders.confirm"):
        prices = {item.pk: item.unit_price for item in copies}
        confirm_order(repeated, user, prices=prices)
    log_event(
        "order_repeat",
        f"Создан повтор заказа #{source.pk} → #{repeated.pk}",
        user=user,
        order=repeated,
        payload={"source_order_id": source.pk, "new_order_id": repeated.pk},
    )
    return repeated


def _apply_prices(order: Order, prices: dict, user) -> None:
    """Зафиксировать договорную цену по каждой позиции и запомнить её для клиента.

    prices: {order_item_id: цена за мешок}. Позиция без новой цены сохраняет уже
    зафиксированную unit_price (заявки Отдела 2 приходят с ценами менеджера) —
    цена обязана быть > 0 из того или иного источника.
    """
    from apps.catalog.models import ClientPrice
    items = list(order.items.select_related("product").all())
    for item in items:
        raw = prices.get(item.id, prices.get(str(item.id)))
        if raw is None and item.unit_price is not None and item.unit_price > 0:
            continue
        price = _positive_money(
            raw,
            detail=f"Укажите корректную цену для «{item.product_label}»",
            code="price_required",
        )
        item.unit_price = price
        item.save(update_fields=["unit_price"])
        # Цена заказа фиксируется всегда, а личный прайс меняет только сотрудник
        # с отдельным правом на закрепление цен.
        if user.has_perm_code("clients.set_price") and item.product_id is not None:
            ClientPrice.objects.update_or_create(
                client=order.client, product=item.product,
                currency=order.currency,
                defaults={"price": price, "updated_by": user})


def apply_item_prices(order: Order, prices: dict, user) -> None:
    """Публичная обёртка: зафиксировать цены заявки без смены статуса.

    Используется, когда заявку с ценами создаёт менеджер Отдела 2 —
    подтверждает её бухгалтер на своём табло.
    """
    _apply_prices(order, prices, user)


@transaction.atomic
def correct_order_prices(
    order: Order,
    user,
    *,
    total_amount=None,
    prices: dict | None = None,
) -> Order:
    """Correct contractual prices in an order at any workflow stage.

    This is deliberately separate from editing order contents: after shipment,
    changing products or bag counts would also require a warehouse reversal.
    The correction updates only item prices, then recomputes the payment state;
    confirmed cash movements remain immutable accounting facts.
    """
    locked = Order.objects.select_for_update().get(pk=order.pk)
    items = list(
        OrderItem.objects.select_for_update()
        .filter(order=locked)
        .order_by("id")
    )
    if not items:
        raise ValidationError({
            "detail": "В заказе нет позиций для корректировки",
            "code": "items_empty",
        })
    if (total_amount is None) == (prices is None):
        raise ValidationError({
            "detail": "Укажите либо общую сумму, либо цены по позициям",
            "code": "correction_mode_required",
        })

    old_total = locked.total_amount
    old_prices = {item.id: item.unit_price for item in items}

    if total_amount is not None:
        requested_total = _positive_money(
            total_amount,
            detail="Укажите корректную общую сумму заказа",
            code="invalid_total_amount",
        )
        bags = sum(item.quantity for item in items)
        unit_price = (requested_total / Decimal(bags)).quantize(Decimal("0.01"))
        if unit_price * bags != requested_total:
            raise ValidationError({
                "detail": (
                    f"Сумма {requested_total} не делится без остатка на {bags} мешков. "
                    "Укажите цены отдельно по позициям."
                ),
                "code": "total_not_divisible",
            })
        new_prices = {item.id: unit_price for item in items}
        mode = "total"
    else:
        if not isinstance(prices, dict):
            raise ValidationError({
                "detail": "Передайте цены по позициям заказа",
                "code": "invalid_prices",
            })
        item_ids = {item.id for item in items}
        supplied_ids = set()
        new_prices = {}
        for raw_id, raw_price in prices.items():
            try:
                item_id = int(raw_id)
            except (TypeError, ValueError) as exc:
                raise ValidationError({
                    "detail": "Некорректная позиция заказа",
                    "code": "invalid_item",
                }) from exc
            supplied_ids.add(item_id)
            new_prices[item_id] = _positive_money(
                raw_price,
                detail="Цена за мешок должна быть положительной суммой",
                code="invalid_price",
            )
        if supplied_ids != item_ids:
            raise ValidationError({
                "detail": "Укажите новую цену для каждой позиции заказа",
                "code": "prices_incomplete",
            })
        mode = "per_item"

    new_total = sum(
        (item.quantity * new_prices[item.id] for item in items),
        Decimal("0"),
    )
    payments = list(
        Payment.objects.select_for_update().filter(order=locked)
    )
    confirmed = sum(
        (payment.net_amount for payment in payments if payment.status == "confirmed"),
        Decimal("0"),
    )
    reserved = sum(
        (payment.amount for payment in payments
         if payment.status in Payment.IN_PROGRESS_STATUSES),
        Decimal("0"),
    )
    if reserved > 0 and confirmed + reserved > new_total:
        raise ValidationError({
            "detail": (
                "Новая сумма меньше уже оплаченной суммы и активных заявок на оплату. "
                "Сначала отмените незавершённые оплаты в кассе."
            ),
            "code": "active_payments_exceed_total",
        })

    for item in items:
        item.unit_price = new_prices[item.id]
        item.save(update_fields=["unit_price"])

    _apply_payment_status(locked, user)
    log_event(
        "order_price_correction",
        f"Стоимость заказа скорректирована: {old_total} → {new_total} {locked.currency}",
        user=user,
        order=locked,
        payload={
            "action": "price_correction",
            "mode": mode,
            "old_total": str(old_total),
            "new_total": str(new_total),
            "currency": locked.currency,
            "items": [
                {
                    "item_id": item.id,
                    "product": item.product_id,
                    "quantity": item.quantity,
                    "old_unit_price": (
                        str(old_prices[item.id]) if old_prices[item.id] is not None else None
                    ),
                    "new_unit_price": str(new_prices[item.id]),
                }
                for item in items
            ],
        },
    )
    locked.refresh_from_db()
    return locked


# Состав заказа можно менять, пока машина не начала грузиться
# (включая «ожидает загрузки»: машина въехала, но погрузка не стартовала).
ITEMS_EDITABLE_STATUSES = ("draft", "pending", "confirmed", "arrived")


@transaction.atomic
def replace_items(order: Order, items_data: list, prices: dict | None, user) -> Order:
    """Заменить позиции заказа (редактирование).

    prices приходит по товару: {product_id: цена за мешок}. После подтверждения
    каждая позиция обязана получить цену — иначе сумма «поплывёт» на базовый
    прайс и испортит долги.
    """
    from .models import OrderItem
    from apps.warehouse.services import ensure_products_available
    # Позиции только по товару в наличии — как и при создании заказа.
    ensure_products_available(item["product"] for item in items_data)
    # Блокируем строку заказа: правка не должна гоняться со стартом загрузки
    # (склад переводит arrived → loading в этот же момент).
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.status not in ITEMS_EDITABLE_STATUSES:
        raise ValidationError(
            {"detail": "Позиции можно менять только до начала загрузки",
             "code": "items_locked"})
    if not items_data:
        raise ValidationError(
            {"detail": "В заказе должна остаться хотя бы одна позиция",
             "code": "items_empty"})
    order.items.all().delete()
    created = [OrderItem.objects.create(order=order, **item) for item in items_data]
    prices = prices or {}
    prices_by_item = {
        it.id: prices.get(str(it.product_id), prices.get(it.product_id))
        for it in created
    }
    if (any(v is not None for v in prices_by_item.values())
            or order.status in ("confirmed", "arrived")):
        _apply_prices(order, prices_by_item, user)
    # Сумма могла измениться — сохранённый статус оплаты приводим к факту.
    _apply_payment_status(order, user)
    log_event("order_edit",
              f"Позиции заказа обновлены ({len(created)} шт.)",
              user=user, order=order,
              payload={"items": [
                  {"product": it.product_id, "quantity": it.quantity,
                   "unit_price": str(it.unit_price) if it.unit_price is not None else None}
                  for it in created
              ]})
    return order


@transaction.atomic
def reject_order(order: Order, user) -> Order:
    if order.status != "pending":
        raise ValidationError(
            {"detail": "Отклонить можно только заказ на рассмотрении", "code": "invalid_status"})
    return transition(order, "rejected", user, "Заказ отклонён")


def can_set_truck_number(order: Order, user) -> bool:
    setter = order.truck_number_set_by
    if setter is None or not order.truck_number:
        return True
    if setter.id == user.id:
        return True
    # number owned by a client → only that client may change it
    if setter.is_client:
        return False
    # number set by staff → any staff may change it
    return not user.is_client


@transaction.atomic
def set_truck_number(order: Order, value: str, user) -> Order:
    # Serialize the number with scale capture/loading. A stale serializer or a
    # second portal tab must not retag an already weighed truck.
    caller_order = order
    # Match the scale-capture lock order (AI reservation, then order) so a
    # number correction cannot deadlock a simultaneous Monoblock start.
    from apps.cameras.models import AiCountingSession

    open_ai_session = (
        AiCountingSession.objects.select_for_update()
        .filter(order_id=order.pk, status__in=AiCountingSession.OPEN_STATUSES)
        .first()
    )
    order = Order.objects.select_for_update().get(pk=order.pk)
    if not can_set_truck_number(order, user):
        raise ValidationError(
            {"detail": "Номер КАМАЗа задан другим пользователем", "code": "forbidden"})
    if value != order.truck_number:
        from apps.shipments.models import Shipment

        shipment = Shipment.objects.select_for_update().filter(order_id=order.pk).only(
            "weigh_in_source", "weigh_in_kg"
        ).first()
        has_scale_entry = bool(
            shipment
            and shipment.weigh_in_source == Shipment.WeightSource.SCALE
            and shipment.weigh_in_kg is not None
        )
        if (
            order.status in ("arrived", "loading", "loaded", "shipped")
            or open_ai_session is not None
        ):
            raise ValidationError({
                "detail": (
                    "Номер КАМАЗа нельзя изменить после въездного взвешивания "
                    "или начала погрузки"
                ),
                "code": "truck_number_locked",
            })
        if has_scale_entry:
            # A deterministic AI-start failure closes its provisional session
            # but deliberately leaves the physical entry sample for retry.
            # Before loading starts, correcting a typo is still recoverable:
            # invalidate that sample atomically so the new number must be
            # weighed again and can never inherit the old truck's weight.
            log_event(
                "weigh_in_invalidated",
                f"Входной вес машины {order.truck_number} сброшен при смене номера",
                user=user,
                order=order,
                payload={
                    "old_truck_number": order.truck_number,
                    "new_truck_number": value,
                    "invalidated_weight_kg": str(shipment.weigh_in_kg),
                    "source": shipment.weigh_in_source,
                },
            )
            shipment.delete()
    order.truck_number = value
    order.truck_number_set_by = user
    order.save(update_fields=["truck_number", "truck_number_set_by"])
    # Preserve the existing service contract: callers historically observed
    # their passed instance updated without having to use the return value.
    caller_order.truck_number = value
    caller_order.truck_number_set_by = user
    caller_order.truck_number_set_by_id = user.pk
    log_event("status", f"Номер КАМАЗа: {value}", user=user, order=order,
              payload={"truck_number": value})
    notify(order.client, f"Ваш КАМАЗ {value} отправляется")
    return order


def _can_edit_status(user) -> bool:
    return bool(user) and not getattr(user, "is_client", False) and user.has_perm_code("orders.edit")


def _validate_manual_status(to_status: str, user) -> None:
    if to_status not in Order.STATUSES:
        raise ValidationError({"detail": "Неизвестный статус", "code": "bad_status"})
    if not getattr(user, "is_superuser", False) and to_status not in PUBLIC_MANUAL_STATUSES:
        raise ValidationError({
            "detail": "Доступны статусы: " + ", ".join(PUBLIC_STATUS_LABELS.values()),
            "code": "status_not_available",
        })


@transaction.atomic
def _force_set_status(order: Order, to_status: str, user,
                      bags_loaded: int | None = None) -> Order:
    _validate_manual_status(to_status, user)
    old = order.status

    # Завершённый заказ уже списал склад и создал финансовый след. Обратный
    # переход без отдельной операции возврата исказил бы остатки и долги.
    if old == "shipped":
        raise ValidationError({
            "detail": "Отгруженный заказ возвращается отдельной операцией с обязательной причиной",
            "code": "shipped_is_final",
        })

    if to_status == "shipped":
        from apps.shipments.services import manual_complete_order
        manual_complete_order(order, bags_loaded, user)
        return order

    # Внутренние стадии могут содержать Shipment, счёт и камеру. Сбрасываем их
    # одной доменной операцией; голая смена status оставила бы занятый слот.
    if old in ("arrived", "loading", "loaded") and to_status in (
        "pending", "confirmed", "cancelled",
    ):
        from apps.shipments.services import rewind_loading
        return rewind_loading(order, user, target_status=to_status)

    order.status = to_status
    if to_status == "cancelled":
        order.loading_camera = ""
        update_fields = ["status", "loading_camera"]
    else:
        update_fields = ["status"]
    order.save(update_fields=update_fields)
    log_event("status_override",
              _status_message("Статус заказа изменён вручную", old, to_status),
              user=user, order=order, payload={"from": old, "to": to_status})
    return order


@transaction.atomic
def request_status_change(order: Order, to_status: str, user,
                          bags_loaded: int | None = None) -> dict:
    """Сотрудник с orders.edit меняет сразу; остальные создают запрос.

    Обычным сотрудникам доступны четыре публичных состояния, суперпользователь
    может вручную выбрать любой внутренний этап.
    """
    _validate_manual_status(to_status, user)
    if to_status == order.status:
        raise ValidationError({"detail": "Статус уже такой", "code": "no_change"})
    if _can_edit_status(user):
        _force_set_status(order, to_status, user, bags_loaded=bags_loaded)
        return {"applied": True, "request": None}
    req = StatusChangeRequest.objects.create(
        order=order, to_status=to_status, requested_by=user)
    log_event("status_request",
              _status_message("Запрос ручной смены статуса", order.status, to_status),
              user=user, order=order,
              payload={"request_id": req.id, "from": order.status, "to": to_status})
    return {"applied": False, "request": req}


@transaction.atomic
def approve_status_change(req: StatusChangeRequest, user) -> StatusChangeRequest:
    if req.status != "pending":
        raise ValidationError({"detail": "Запрос уже обработан", "code": "already_decided"})
    _force_set_status(req.order, req.to_status, user)
    req.status = "approved"
    req.decided_by = user
    req.decided_at = timezone.now()
    req.save(update_fields=["status", "decided_by", "decided_at"])
    log_event("status_request", "Запрос смены статуса одобрен", user=user, order=req.order,
              payload={"request_id": req.id})
    return req


@transaction.atomic
def reject_status_change(req: StatusChangeRequest, user) -> StatusChangeRequest:
    if req.status != "pending":
        raise ValidationError({"detail": "Запрос уже обработан", "code": "already_decided"})
    req.status = "rejected"
    req.decided_by = user
    req.decided_at = timezone.now()
    req.save(update_fields=["status", "decided_by", "decided_at"])
    log_event("status_request", "Запрос смены статуса отклонён", user=user, order=req.order,
              payload={"request_id": req.id})
    return req


@transaction.atomic
def soft_delete_order(order: Order, user) -> Order:
    """Мягкое удаление: заказ уезжает в «Корзину». Из всех отчётов и списков
    исчезает (default-manager его не видит), но данные сохраняются и заказ
    можно восстановить."""
    try:
        order = Order.all_objects.select_for_update().get(pk=order.pk)
    except Order.DoesNotExist as exc:
        raise ValidationError(
            {"detail": "Заказ не найден", "code": "not_found"}
        ) from exc
    if order.purged_at is not None:
        raise ValidationError({
            "detail": "Заказ удалён безвозвратно",
            "code": "already_purged",
        })
    if order.deleted_at is not None:
        raise ValidationError({"detail": "Заказ уже в корзине", "code": "already_deleted"})
    order.deleted_at = timezone.now()
    order.deleted_by = user
    order.save(update_fields=["deleted_at", "deleted_by"])
    log_event("order", "Заказ удалён в корзину", user=user, order=order,
              payload={"order_id": order.id})
    return order


@transaction.atomic
def restore_order(order: Order, user) -> Order:
    """Восстановить заказ из корзины — снова участвует в отчётах и списках."""
    try:
        order = Order.all_objects.select_for_update().get(pk=order.pk)
    except Order.DoesNotExist as exc:
        raise ValidationError(
            {"detail": "Заказ не найден", "code": "not_found"}
        ) from exc
    if order.purged_at is not None:
        raise ValidationError({
            "detail": "Заказ удалён безвозвратно и не может быть восстановлен",
            "code": "already_purged",
        })
    if order.deleted_at is None:
        raise ValidationError({"detail": "Заказ не в корзине", "code": "not_deleted"})
    order.deleted_at = None
    order.deleted_by = None
    order.save(update_fields=["deleted_at", "deleted_by"])
    log_event("order", "Заказ восстановлен из корзины", user=user, order=order,
              payload={"order_id": order.id})
    return order


@transaction.atomic
def purge_order(order: Order, user) -> None:
    """Permanently remove an order from the user-facing system.

    Only a pristine draft can be physically deleted. Any document that has
    left the draft state or acquired payment, shipment, AI or approval history
    is tombstoned instead: it disappears from the recycle bin, while its row
    and related accounting records remain available to reconciliation code via
    ``Order.all_objects``. The row lock serializes purge with restore/delete so
    a stale view object cannot delete a live or already-purged row.
    """
    try:
        order = Order.all_objects.select_for_update().get(pk=order.pk)
    except Order.DoesNotExist as exc:
        raise ValidationError(
            {"detail": "Заказ не найден", "code": "not_found"}
        ) from exc
    if order.purged_at is not None:
        raise ValidationError({
            "detail": "Заказ уже удалён безвозвратно",
            "code": "already_purged",
        })
    if order.deleted_at is None:
        raise ValidationError(
            {"detail": "Сначала переместите заказ в корзину", "code": "not_deleted"})
    must_retain_history = (
        order.status != "draft"
        or order.payments.exists()
        or hasattr(order, "shipment")
        or order.ai_counting_sessions.exists()
        or order.status_requests.exists()
    )
    if must_retain_history:
        order.purged_at = timezone.now()
        order.purged_by = user
        order.save(update_fields=["purged_at", "purged_by"])
        log_event(
            "order",
            f"Заказ #{order.id} удалён из архива с сохранением учётной истории",
            user=user,
            order=order,
            payload={
                "order_id": order.id,
                "client_id": order.client_id,
                "total_amount": str(order.total_amount),
                "retained_for_audit": True,
            },
        )
        return
    log_event("order", f"Заказ #{order.id} удалён навсегда", user=user,
              payload={"order_id": order.id, "client_id": order.client_id,
                       "total_amount": str(order.total_amount)})
    try:
        order.delete()
    except ProtectedError as exc:
        # Future audit/history relations using PROTECT must also fail with a
        # stable public error instead of leaking an ORM exception as a 500.
        raise ValidationError({
            "detail": "Заказ с проведёнными операциями нельзя удалить безвозвратно",
            "code": "financial_record_protected",
        }) from exc
