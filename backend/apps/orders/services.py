from collections import Counter
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.clients.assignment import assign_client_department
from apps.clients.models import Client
from apps.clients.services import is_payment_window_open
from apps.common.money import money_string
from apps.eventlog.services import log_event
from apps.notifications.services import notify
from apps.sales.access import assigned_department_id
from apps.sales.models import Department
from apps.shipments.services import ROLLBACK_TARGET_STATUSES, assert_no_open_ai_session, has_open_ai_session

from .debt import (
    available_to_pay,
    confirmed_and_reserved,
    order_payment_status,
    order_remaining,
)
from .fixation import assert_can_fixate, fixate_order
from .models import Order, OrderItem, Payment, StatusChangeRequest
from .statuses import (
    AWAITING_SHIPMENT_STATUSES,
    CLOSED_STATUSES,
    ENTERED_POST_STATUSES,
    ON_POST_STATUSES,
    PUBLIC_MANUAL_STATUSES,
    PUBLIC_STATUS_LABELS,
    REVIEWABLE_STATUSES,
    is_financial,
    is_payment_method_allowed,
    is_payment_open,
    payment_open_method,
    public_status_label,
)

MAX_MONEY = Decimal("9999999999.99")


def assert_order_user_scope(order: Order, user, *, any_department=False) -> None:
    """Lock and verify the client owner after the caller locks ``order``.

    Department reassignment locks the Client row. Taking the same row lock
    here, strictly after the Order lock, prevents an operation authorized
    against stale ownership from racing a client transfer.

    ``any_department`` — операция общей очереди подтверждения кассы («Заявки и
    оплаты»): разбор заявки или оплаты в очереди доступен сотруднику любого
    отдела. Всё остальное — только отделу клиента.
    """
    if user is None or any_department:
        return
    department_id = assigned_department_id(user)
    if department_id is None:
        return
    client = (
        Client.objects.select_for_update()
        .only("department_id")
        .get(pk=order.client_id)
    )
    if client.department_id != department_id:
        raise PermissionDenied("Заказ передан в другой отдел")


def lock_live_order(order: Order | int, user=None, *, any_department=False) -> Order:
    """Lock an order and reject stale operations against archived rows.

    Callers must already be inside ``transaction.atomic()``. Using the
    all-objects manager lets us distinguish an archived/tombstoned order from
    a live row instead of leaking ``DoesNotExist`` as a 500 after a concurrent
    archive or purge.
    """
    order_id = order.pk if isinstance(order, Order) else order
    try:
        locked = Order.all_objects.select_for_update().get(pk=order_id)
    except Order.DoesNotExist as exc:
        raise ValidationError({
            "detail": "Заказ больше недоступен",
            "code": "order_not_active",
        }) from exc
    if locked.deleted_at is not None or locked.purged_at is not None:
        raise ValidationError({
            "detail": "Заказ находится в архиве",
            "code": "order_not_active",
        })
    assert_order_user_scope(locked, user, any_department=any_department)
    return locked


def _locked_payment_order(order: Order, user=None) -> Order:
    """Serialize payment choices and derived totals through the order row."""
    locked = lock_live_order(order, user)
    return (
        Order.objects
        .select_related("client__user", "store")
        .prefetch_related("items", "payments")
        .get(pk=locked.pk)
    )


def _locked_payment_with_order(
    payment: Payment, user=None, *, any_department=False,
) -> tuple[Payment, Order]:
    """Use one lock order (Order -> Payment) across every payment transition."""
    order = lock_live_order(payment.order_id, user, any_department=any_department)
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


def payment_status_open_error(order: Order, *, method: str | None, by_client=False) -> dict | None:
    """Почему статус заказа не допускает деньги этим способом (None — допускает).

    Правило — :func:`statuses.is_payment_open`; ошибка в форме ValidationError.
    """
    if is_payment_open(order.status, method=method, by_client=by_client):
        return None
    # Окно оплаты показывает эту причину — она должна совпадать со статусом.
    if by_client:
        detail = "Оплата доступна только после отгрузки"
    elif order.status in CLOSED_STATUSES:
        detail = "Заказ отменён — оплата не принимается"
    elif order.status in REVIEWABLE_STATUSES:
        detail = "Оплата доступна после подтверждения заказа"
    else:
        detail = "До отгрузки принимаются только наличные, Kaspi-терминал и удалённая оплата"
    return {"detail": detail, "code": "payment_not_open"}


def assert_payment_status_open(order: Order, *, method: str | None, by_client=False) -> None:
    """Статус заказа допускает деньги этим способом (:func:`statuses.is_payment_open`).

    Общая проверка для всего, что вводит деньги в работу: новая оплата,
    восстановление отклонённой и новый счёт провайдеру для уже созданной.
    """
    if error := payment_status_open_error(order, method=method, by_client=by_client):
        raise ValidationError(error)


def _validate_payment_open(order: Order, *, method: str | None, by_client=False) -> None:
    """Приём новой оплаты: статус заказа и окно оплаты магазина.

    Окно оплаты магазина — график погашения долга, поэтому оно действует только
    на отгруженный заказ: предоплату магазин вносит в любой день.
    """
    assert_payment_status_open(order, method=method, by_client=by_client)
    if (
        order.status == "shipped"
        and order.store
        and not is_payment_window_open(order.store, timezone.localdate())
    ):
        raise ValidationError(
            {"detail": f"Оплата для магазина «{order.store.name}» сегодня недоступна",
             "code": "payment_window_closed"}
        )


def assert_order_has_no_money(order: Order) -> None:
    """Заказ с деньгами нельзя отменить, вернуть в заявку, отклонить или удалить.

    Переход в нефинансовый статус (:func:`statuses.is_financial`) вывел бы деньги
    из оборота. С предоплатой деньги бывают и у неотгруженного заказа. Подтверждённые
    (за вычетом возвратов) остались бы без документа, а незавершённая оплата
    могла бы ещё дойти. Сначала возврат или отклонение в кассе. Вызывать под
    блокировкой заказа (оплаты меняются только под ней) со свежим ``order``.
    """
    paid = order.paid_total
    if paid > 0:
        raise ValidationError({
            "detail": (
                f"По заказу принято {money_string(paid)} {order.currency} — "
                "сначала оформите возврат"
            ),
            "code": "order_has_payments",
        })
    if order.payments.filter(status__in=Payment.IN_PROGRESS_STATUSES).exists():
        raise ValidationError({
            "detail": "По заказу есть незавершённая оплата — сначала отклоните её в кассе",
            "code": "order_has_payments",
        })


def assert_money_allows_status(order: Order, to_status: str) -> None:
    """Переход в ``to_status`` не выводит деньги из оборота.

    В финансовый статус (ожидание отгрузки, отгрузка) деньги идут вместе с
    заказом — предоплатой; в нефинансовый (заявка, отмена, отказ) — только
    без денег (:func:`assert_order_has_no_money`). Единая проверка для всех
    путей смены статуса: прямой, по запросу, отката погрузки и отгрузки.
    """
    if not is_financial(to_status):
        assert_order_has_no_money(order)


# Строчные формы для фразы журнала: «Оплата 100 KZT принята». Это не копия
# labels.PAYMENT_STATUS_LABELS — там подпись статуса («В кассе»), здесь
# сказуемое внутри предложения, поэтому наборы намеренно разные.
PAYMENT_STAGE_LABELS = {
    "requested": "запрошена", "received": "принята",
    "confirmed": "подтверждена бухгалтером", "rejected": "отклонена",
}


def _log_payment(payment: Payment, message: str, user, *, stage: str, **extra) -> None:
    """Событие журнала по оплате: сумма, валюта, способ и шаг цепочки."""
    log_event(
        "payment",
        message,
        user=user,
        order=payment.order,
        payload={
            "payment_id": payment.id,
            "amount": str(payment.amount),
            "currency": payment.order.currency,
            "method": payment.method,
            "payment_stage": stage,
            **extra,
        },
    )


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
    _log_payment(
        payment,
        f"Оплата {payment.amount} {payment.order.currency} {PAYMENT_STAGE_LABELS[status]}",
        user,
        stage=status,
    )
    return payment


@transaction.atomic
def add_payment(order: Order, amount, user, method="cash", stage="received",
                note="") -> Payment:
    """Начало цепочки оплаты: «запрошена» (счёт выставлен) или «принята» (деньги у менеджера)."""
    order = _locked_payment_order(order, user)
    _validate_payment_open(order, method=payment_open_method(method, stage))
    if not is_payment_method_allowed(order.currency, method):
        raise ValidationError({
            "detail": "Kaspi и удалённая оплата принимаются только в тенге",
            "code": "payment_kzt_only",
        })
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
    available = available_to_pay(order)
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
    _log_payment(
        payment,
        f"Оплата {amount} {order.currency} ({method}) {PAYMENT_STAGE_LABELS[stage]}",
        user,
        stage=stage,
    )
    return payment


@transaction.atomic
def create_client_payment(order: Order, method: str, user, amount=None) -> Payment:
    if method not in ("invoice", "kaspi", "cash", "card"):
        raise ValidationError({"detail": "Недопустимый способ оплаты", "code": "bad_method"})
    order = _locked_payment_order(order, user)
    _validate_payment_open(order, method=method, by_client=True)
    remaining = order_remaining(order)
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
    _log_payment(
        payment,
        f"Клиент {action} оплату {requested_amount} {order.currency} ({method})",
        user,
        stage=stage,
    )
    return payment


def client_release_invoice_error(invoice) -> dict | None:
    """Почему счёт ApiPay не даёт клиенту освободить заявку (None — даёт).

    Одно правило для :func:`release_client_payment`, портала (до отмены счёта
    по номеру) и флага ``can_release``: полученные деньги и ещё не сверенное
    создание QR сумму не отпускают.
    """
    from .apipay import MONEY_RECEIVED_INVOICE_STATUSES

    if invoice is None:
        return None
    if invoice.status in MONEY_RECEIVED_INVOICE_STATUSES:
        return {
            "detail": "Платёж уже получен и обрабатывается. Обновите страницу.",
            "code": "payment_already_paid",
        }
    if (
        invoice.channel == "qr"
        and invoice.invoice_id is None
        and invoice.status == "creating"
    ):
        return {
            "detail": (
                "Создание QR ещё сверяется с платёжным сервисом. "
                "Нельзя освобождать сумму до завершения сверки."
            ),
            "code": "qr_issue_recovery_pending",
        }
    return None


@transaction.atomic
def release_client_payment(payment: Payment, user) -> Payment:
    """Освободить зарезервированную часть, не скрывая возможную позднюю оплату."""
    from .apipay import CLOSED_INVOICE_STATUSES

    payment, order = _locked_payment_with_order(payment, user)
    invoice = getattr(payment, "apipay_invoice", None)
    if payment.status not in Payment.IN_PROGRESS_STATUSES:
        if (
            payment.status == "rejected"
            and invoice is not None
            and invoice.status in CLOSED_INVOICE_STATUSES
        ):
            # A synchronous provider cancellation already rejected the
            # Payment through the shared status engine. Treat the following
            # release call as the successful, idempotent completion of that
            # same user action; never extend this to paid/confirmed money.
            return payment
        raise ValidationError({
            "detail": "Эта заявка уже завершена.",
            "code": "payment_not_in_progress",
        })
    if error := client_release_invoice_error(invoice):
        raise ValidationError(error)
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
    return payment


def _lock_shipped_order_for_debt(order: Order, user, *, in_progress_detail: str) -> Order:
    """Долг фиксируется только у отгруженного заказа без незавершённой оплаты."""
    order = _locked_payment_order(order, user)
    if order.status != "shipped":
        raise ValidationError({"detail": "Долг фиксируется после отгрузки",
                               "code": "invalid_status"})
    if order.payments.select_for_update().filter(
        status__in=Payment.IN_PROGRESS_STATUSES,
    ).exists():
        raise ValidationError({"detail": in_progress_detail, "code": "payment_in_progress"})
    return order


@transaction.atomic
def request_client_debt(order: Order, user) -> Order:
    """Зафиксировать выбор «В долг» без создания денежной оплаты."""
    order = _lock_shipped_order_for_debt(
        order,
        user,
        in_progress_detail=(
            "Сначала завершите текущую оплату или выберите «Другой способ» "
            "у своей заявки."
        ),
    )
    order.payment_method = "debt"
    order.settlement_intent = "debt"
    order.debt_requested = True
    order.save(update_fields=["payment_method", "settlement_intent", "debt_requested"])
    log_event("debt_override", "Клиент запросил долг", user=user, order=order,
              payload={"payment_method": "debt"})
    return order


@transaction.atomic
def move_order_to_debt(order: Order, user) -> Order:
    """Касса согласует долг: заказ уходит из «Ждут оплаты», остаток остаётся долгом клиента."""
    order = _lock_shipped_order_for_debt(
        order,
        user,
        in_progress_detail="По заказу есть незавершённая оплата — сначала подтвердите или отклоните её.",
    )
    if order.settlement_intent == "debt":
        raise ValidationError({"detail": "Долг по заказу уже согласован", "code": "already_debt"})
    remaining = order.remaining_amount
    if remaining <= 0:
        raise ValidationError({"detail": "Заказ уже оплачен", "code": "nothing_to_pay"})
    order.payment_method = "debt"
    order.settlement_intent = "debt"
    order.save(update_fields=["payment_method", "settlement_intent"])
    log_event("debt_override", "Касса согласовала долг по заказу", user=user, order=order,
              payload={"payment_method": "debt", "amount": str(remaining),
                       "currency": order.currency})
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
def receive_payment(payment: Payment, user, *, any_department=False) -> Payment:
    """Менеджер/оператор отметил: деньги получены от клиента."""
    original = payment
    payment, _order = _locked_payment_with_order(payment, user, any_department=any_department)
    _advance_payment(payment, "requested", user)
    return _sync_payment_instance(original, payment)


@transaction.atomic
def accountant_confirm_payment(payment: Payment, user, *, any_department=False) -> Payment:
    """Бухгалтер (касса) сверил и подтвердил оплату — деньги учтены сразу."""
    original = payment
    payment, order = _locked_payment_with_order(payment, user, any_department=any_department)
    provider = getattr(payment, "apipay_invoice", None)
    if provider is not None and provider.status != "paid":
        raise ValidationError({
            "detail": (
                "Онлайн-оплата подтверждается автоматически после поступления "
                "уведомления от платёжного сервиса."
            ),
            "code": "provider_payment_auto_confirmation",
        })
    # Строка заказа уже заблокирована: все изменения оплат берут её первой.
    available = order_remaining(order)
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
def record_staff_payment(
    order: Order,
    amount,
    user,
    *,
    method="cash",
    note="",
) -> Payment:
    """Record money from the CRM and settle funds already received by staff.

    The CRM endpoint itself is protected by ``payments.create`` and cannot be
    called by portal users.  Consequently the source boundary, rather than a
    second ``payments.confirm`` permission, determines whether received cash
    or a till-side Kaspi payment can be finalized immediately.  The same holds
    for ``remote``: it only records that the client already paid remotely.

    Invoices deliberately remain requests: issuing a PDF or provider invoice
    is not evidence that money arrived.
    """
    payment = add_payment(
        order,
        amount,
        user,
        method=method,
        # The CRM action is source-authoritative: choosing cash/Kaspi means
        # the employee is recording money already received at the till.
        # Invoices are the inverse: issuing one never proves that the client paid.
        stage="requested" if method == "invoice" else "received",
        note=note,
    )
    if payment.status == "received" and payment.method in Payment.SETTLED_ON_RECORD:
        # Подтверждение перечитывает этот же экземпляр после блокировки.
        accountant_confirm_payment(payment, user)
    return payment


@transaction.atomic
def receive_and_confirm_payment(payment: Payment, user, *, any_department=False) -> Payment:
    """Accept a providerless request and finalize it as one staff decision.

    The outer transaction is intentional: if confirmation fails, the request
    must remain ``requested`` instead of getting stranded at ``received`` and
    requiring an unexplained second action.
    """
    if hasattr(payment, "apipay_invoice"):
        raise ValidationError({
            "detail": (
                "Онлайн-оплата подтверждается автоматически после поступления "
                "уведомления от платёжного сервиса."
            ),
            "code": "provider_payment_auto_confirmation",
        })
    # Оба шага перечитывают переданный экземпляр после блокировки.
    receive_payment(payment, user, any_department=any_department)
    return accountant_confirm_payment(payment, user, any_department=any_department)


def reopen_confirmed_payment_error(payment: Payment) -> dict | None:
    """Почему подтверждённую оплату нельзя вернуть на проверку (None — можно).

    Одно правило для :func:`reopen_confirmed_payment` и флагов ``can_reopen``
    в API: онлайн-оплату и оплату с возвратом так не откатывают — приход и
    возврат остаются в истории. Возвраты читаются через ``.all()``, чтобы
    списки брали их из prefetch.
    """
    if payment.status != "confirmed":
        return {
            "detail": "Вернуть можно только подтверждённую оплату",
            "code": "invalid_payment_stage",
        }
    if hasattr(payment, "apipay_invoice"):
        return {
            "detail": (
                "Онлайн-оплату нельзя вернуть в очередь без возврата денег. "
                "Используйте действие «Оформить возврат»."
            ),
            "code": "provider_payment_requires_refund",
        }
    if (payment.refunded_amount > 0 or payment.pending_refund_amount > 0
            or any(refund.status in ("pending", "completed")
                   for refund in payment.payment_refunds.all())):
        return {
            "detail": "Оплату с возвратом нельзя вернуть на подтверждение: приход и возврат должны остаться в истории.",
            "code": "payment_has_refunds",
        }
    return None


@transaction.atomic
def reopen_confirmed_payment(payment: Payment, user) -> Payment:
    """Вернуть ошибочно подтверждённую оплату на повторное подтверждение.

    Денежный итог заказа пересчитывается сразу, а исходное подтверждение и
    отмена остаются отдельными append-only событиями в журнале.
    """
    original = payment
    payment, order = _locked_payment_with_order(payment, user)
    if error := reopen_confirmed_payment_error(payment):
        raise ValidationError(error)
    previous_confirmed_by = payment.confirmed_by_id
    previous_confirmed_at = payment.confirmed_at
    payment.status = "received"
    payment.confirmed_by = None
    payment.confirmed_at = None
    payment.save(update_fields=["status", "confirmed_by", "confirmed_at"])
    _log_payment(
        payment,
        f"Оплата {payment.amount} {payment.order.currency} возвращена на подтверждение",
        user,
        stage="received",
        action="reopened",
        previous_confirmed_by=previous_confirmed_by,
        previous_confirmed_at=(
            previous_confirmed_at.isoformat() if previous_confirmed_at else None
        ),
    )
    _apply_payment_status(order, user)
    return _sync_payment_instance(original, payment)


@transaction.atomic
def reject_payment(payment: Payment, user, *, any_department=False) -> Payment:
    original = payment
    payment, _order = _locked_payment_with_order(payment, user, any_department=any_department)
    if payment.status in ("confirmed", "rejected"):
        raise ValidationError(
            {"detail": "Оплата уже финализирована", "code": "invalid_payment_stage"})
    previous_stage = payment.status
    payment.status = "rejected"
    payment.save(update_fields=["status"])
    _log_payment(
        payment,
        f"Оплата {payment.amount} {payment.order.currency} отклонена",
        user,
        stage="rejected",
        previous_payment_stage=previous_stage,
        action="rejected",
    )
    return _sync_payment_instance(original, payment)


def _restored_stage(payment: Payment) -> str:
    return "received" if payment.received_at else "requested"


def restore_rejected_payment_error(payment: Payment, *, payments=None) -> dict | None:
    """Почему отклонённую оплату нельзя восстановить (None — можно).

    Одно правило для :func:`restore_rejected_payment` и флагов ``can_restore``
    в API. ``payments`` — оплаты заказа для свободного остатка: сервис передаёт
    их под блокировкой, списки берут ``order.payments.all()`` из prefetch.
    """
    from .apipay import CLOSED_INVOICE_STATUSES

    order = payment.order
    if payment.status != "rejected":
        return {
            "detail": "Восстановить можно только отклонённую оплату",
            "code": "invalid_payment_stage",
        }
    # Восстановленная оплата снова в работе — правило то же, что для новой:
    # иначе отклонённый счёт вернулся бы деньгами на отменённый заказ или
    # выдал бы Kaspi QR / счёт на телефон до отгрузки.
    if error := payment_status_open_error(
        order, method=payment_open_method(payment.method, _restored_stage(payment))
    ):
        return error
    invoice = getattr(payment, "apipay_invoice", None)
    if (
        invoice
        and invoice.invoice_id is None
        and invoice.status != "creating"
    ):
        return {
            "detail": (
                "Прежний ключ счёта закрыт. Создайте новую платёжную "
                "операцию с новым ключом."
            ),
            "code": "provider_issue_key_retired",
        }
    if (
        invoice
        and invoice.invoice_id is not None
        and invoice.status in CLOSED_INVOICE_STATUSES
    ):
        return {
            "detail": "Отменённый счёт восстановить нельзя — создайте новый счёт на оплату.",
            "code": "provider_invoice_closed",
        }
    available = available_to_pay(order, payments)
    if payment.amount > available:
        return {
            "detail": (
                f"Восстановить нельзя: свободный остаток заказа "
                f"{available} {order.currency}."
            ),
            "code": "payment_exceeds_remaining",
        }
    return None


@transaction.atomic
def restore_rejected_payment(payment: Payment, user) -> Payment:
    """Вернуть ошибочно отклонённую кассовую оплату в рабочую очередь."""
    original = payment
    payment, order = _locked_payment_with_order(payment, user)
    if error := restore_rejected_payment_error(
        payment, payments=order.payments.select_for_update()
    ):
        raise ValidationError(error)
    restored_stage = _restored_stage(payment)
    payment.status = restored_stage
    payment.save(update_fields=["status"])
    _log_payment(
        payment,
        f"Оплата {payment.amount} {order.currency} восстановлена",
        user,
        stage=restored_stage,
        previous_payment_stage="rejected",
        action="restored",
    )
    return _sync_payment_instance(original, payment)


def sync_payment_status(order: Order) -> str:
    """Привести payment_status в соответствие с фактическими оплатами. Идемпотентно.

    Без пользователя — используется и при оплате, и для бэкфилла легаси-данных.
    """
    order.refresh_from_db()
    new = order_payment_status(order)
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


def _apply_status(order: Order, to_status: str, user, message: str, *, payload=None) -> Order:
    """Записать итог заявки (подтверждение или отказ) и событие в журнал.

    Исходный статус под блокировкой уже проверил вызывающий сервис
    (``confirm_order``/``reject_order``); дальше заказ ведут отгрузка
    (shipments.services) и административная смена статуса. Заявка — общая
    очередь, поэтому отдел сотрудника здесь не ограничивает. Повторное чтение
    под блокировкой возвращает заказ со всеми правками подтверждения.
    """
    order = lock_live_order(order, user, any_department=True)
    assert_no_open_ai_session(order)
    old = order.status
    order.status = to_status
    order.save(update_fields=["status"])
    log_event(
        "status",
        message,
        user=user,
        order=order,
        payload={**(payload or {}), "from": old, "to": to_status},
    )
    return order


# Сколько урезанных позиций назвать в журнале и уведомлении — остальные «и ещё N».
CONFIRM_CHANGES_SHOWN = 3
# EventLog.message и Notification.text — CharField(max_length=500).
MESSAGE_MAX_LENGTH = 500


def order_items_error(items, historical_ids=frozenset()) -> str | None:
    """Общая проверка позиций заказа для сотрудников и портала.

    Архивный товар в заказ не добавляют (остаётся только уже бывший в заказе —
    ``historical_ids``), а один товар занимает одну строку.
    """
    if any(not item["product"].is_active and item["product"].pk not in historical_ids
           for item in items):
        return "Архивный товар нельзя добавлять в заказ"
    product_ids = [item["product"].pk for item in items]
    if len(product_ids) != len(set(product_ids)):
        return "Объедините повторяющиеся товары в одну строку"
    return None


def confirm_stock_context(order: Order) -> dict[str, dict[str, int]]:
    """Окно подтверждения: остаток товара на складе заказа и сколько его уже ждут.

    ``{item_id: {"on_hand": мешков на складе, "awaiting_shipment": мешков в
    других заказах этого склада, ждущих отгрузки}}``. Остаток видит каждый,
    кто подтверждает заявки, — отдельное право на склад не нужно.
    """
    from apps.warehouse.services import stock_balances

    from .querysets import awaiting_shipment_bags

    items = list(order.items.all())
    warehouse = order.warehouse
    product_ids = {item.product_id for item in items if item.product_id is not None}
    on_hand = stock_balances(warehouse, product_ids)
    awaiting = awaiting_shipment_bags(warehouse, product_ids, exclude_order_id=order.pk)
    return {
        str(item.pk): {
            "on_hand": on_hand.get(item.product_id, 0),
            "awaiting_shipment": awaiting.get(item.product_id, 0),
        }
        for item in items
    }


def _apply_confirmed_quantities(order: Order, quantities: dict) -> list[dict]:
    """Урезать позиции заявки до подтверждённого количества — на месте.

    quantities: {order_item_id: мешков, от 1 до запрошенного}. Позиции не
    пересоздаются, их id не меняются. Возвращает урезанные позиции по порядку.
    """
    if not quantities:
        return []
    given = {str(key): value for key, value in quantities.items()}
    items = list(OrderItem.objects.select_for_update().filter(order=order).order_by("id"))
    if not set(given) <= {str(item.pk) for item in items}:
        raise ValidationError({
            "detail": "Состав заявки изменился — обновите заявку",
            "code": "invalid_item",
        })
    changes = []
    for item in items:
        quantity = given.get(str(item.pk), item.quantity)
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
            raise ValidationError({
                "detail": f"Укажите количество для «{item.product_label}» — от 1 мешка",
                "code": "invalid_quantity",
            })
        if quantity > item.quantity:
            raise ValidationError({
                "detail": f"«{item.product_label}»: в заявке {item.quantity} меш., больше подтвердить нельзя",
                "code": "quantity_exceeds_request",
            })
        if quantity == item.quantity:
            continue
        changes.append({
            "item_id": item.pk,
            "product": item.product_label,
            "requested": item.quantity,
            "confirmed": quantity,
        })
        item.quantity = quantity
        item.save(update_fields=["quantity"])
    return changes


def _describe_quantity_changes(changes: list[dict], *, unit: str = "") -> str:
    """«Мука 1с — 8 из 10, Отруби — 5 из 6 и ещё 2»: первые позиции и счётчик."""
    shown = ", ".join(
        f"{change['product']} — {change['confirmed']} из {change['requested']}{unit}"
        for change in changes[:CONFIRM_CHANGES_SHOWN]
    )
    rest = len(changes) - CONFIRM_CHANGES_SHOWN
    return f"{shown} и ещё {rest}" if rest > 0 else shown


@transaction.atomic
def confirm_order(
    order: Order,
    user,
    prices: dict | None = None,
    *,
    department=None,
    quantities: dict | None = None,
    truck: str | None = None,
    trailer: str | None = None,
) -> Order:
    """Подтвердить заявку: отдел, цены, «сколько есть» и номер транспорта.

    ``quantities`` урезает позиции до подтверждённого количества (не больше
    запрошенного). ``truck``/``trailer`` — ``None``, если номер не передан.
    Клиент получает одно уведомление — только если заявку урезали.
    """
    # Freeze the order and its item set before validating/pricing. This shares
    # the parent fence with item edits and AI reservation.
    caller_order = order
    # Заявка — общая очередь кассы: подтверждает сотрудник любого отдела, а
    # заказ всё равно учитывается в отделе клиента (проверка ниже).
    order = lock_live_order(order, user, any_department=True)
    if order.status not in REVIEWABLE_STATUSES:
        raise ValidationError(
            {"detail": "Подтвердить можно только новый заказ", "code": "invalid_status"})
    client = Client.objects.select_for_update().get(pk=order.client_id)
    if client.department_id is None and department is not None:
        # Заявка клиента без отдела: отдел, выбранный при подтверждении,
        # закрепляет и клиента — иначе каждая его заявка снова «без отдела».
        assign_client_department(
            client,
            Department.objects.filter(code=department).first() if isinstance(department, str) else None,
            user,
        )
    if client.department_id:
        assigned = client.department
        if not assigned.is_active:
            raise ValidationError(
                {
                    "department": "Отдел клиента отключён. Сначала измените отдел в карточке клиента."
                }
            )
        if department is not None and department != assigned.code:
            raise ValidationError(
                {
                    "department": f"Заказ должен учитываться в отделе клиента: {assigned.name}"
                }
            )
        department = assigned.code
    if department is not None:
        if not isinstance(department, str) or not Department.objects.filter(
            code=department, is_active=True
        ).exists():
            raise ValidationError({"department": "Выберите действующий отдел продаж"})
        set_order_department(order, department, user, any_department=True)
    if not order.department:
        raise ValidationError({"department": "Перед подтверждением выберите отдел продаж"})
    changes = _apply_confirmed_quantities(order, quantities or {})
    apply_item_prices(order, prices or {}, user)
    if truck is not None or trailer is not None:
        from .transport import set_order_transport

        # Номер в заявке — не смена машины: клиенту о нём отдельно не пишем.
        set_order_transport(
            order, user, truck=truck, trailer=trailer, notify_client=False, any_department=True)
    message, payload = "Заказ подтверждён", None
    if changes:
        confirmed_bags = order.ordered_bags
        requested_bags = confirmed_bags + sum(c["requested"] - c["confirmed"] for c in changes)
        message = (
            f"Заказ подтверждён: {confirmed_bags} из {requested_bags} меш. "
            f"({_describe_quantity_changes(changes)})"
        )[:MESSAGE_MAX_LENGTH]
        payload = {"quantity_changes": changes}
    confirmed = _apply_status(order, "confirmed", user, message, payload=payload)
    if changes:
        notify(
            order.client,
            f"Заявка №{order.pk} подтверждена: {_describe_quantity_changes(changes, unit=' меш.')}"[
                :MESSAGE_MAX_LENGTH
            ],
        )
    caller_order.status = confirmed.status
    return confirmed


def apply_item_prices(order: Order, prices: dict, user) -> None:
    """Зафиксировать договорную цену по каждой позиции и запомнить её для клиента.

    prices: {order_item_id: цена за мешок}. Позиция без новой цены сохраняет уже
    зафиксированную unit_price (заявки Отдела 2 приходят с ценами менеджера) —
    цена обязана быть > 0 из того или иного источника. Статус заказа не меняет:
    подтверждение и правка позиций зовут её сами, а заявку с ценами менеджера
    Отдела 2 подтверждает бухгалтер на своём табло.
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
    locked = lock_live_order(order, user)
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
    # Сумма ниже подтверждённых денег разрешена: излишек — переплата к возврату.
    _validate_payment_exposure(locked, new_total)

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


# An order may be corrected after a physical workflow, but never while the
# physical load itself is in progress.  ``loaded`` is intentionally editable:
# the AI target/final count remain immutable evidence, while the corrected
# order rows become the inventory fact deducted on shipment.
ITEMS_LOCKED_STATUSES = ("loading",)
ITEMS_REQUIRE_PRICES_STATUSES = ("confirmed", "arrived", "loaded", "shipped")


def _edit_item_payload(item: OrderItem) -> dict:
    return {
        "product": item.product_id,
        "product_label": item.product_label,
        "quantity": item.quantity,
        "unit_price": (
            str(item.unit_price) if item.unit_price is not None else None
        ),
    }


def _shipped_edit_reason(raw) -> str:
    reason = " ".join(str(raw or "").split())
    if len(reason) < 5:
        raise ValidationError({
            "detail": "Укажите причину изменения отгруженного заказа (минимум 5 символов)",
            "code": "edit_reason_required",
        })
    if len(reason) > 500:
        raise ValidationError({
            "detail": "Причина изменения слишком длинная",
            "code": "reason_too_long",
        })
    return reason


def _validate_payment_exposure(order: Order, new_total: Decimal) -> dict:
    """Lock payments and ensure an edit cannot invalidate active requests.

    Confirmed cash is an immutable accounting fact and may therefore exceed a
    corrected order total (the excess is shown as ``debt.order_overpaid``).
    Requested/received payments can still be cancelled, so they must be
    resolved first when their reservation would overpay the corrected order.
    Shared by item edits and ``correct_order_prices``.
    """
    confirmed, reserved = confirmed_and_reserved(
        Payment.objects.select_for_update().filter(order=order)
    )
    if reserved > 0 and confirmed + reserved > new_total:
        raise ValidationError({
            "detail": (
                "Новая сумма меньше уже оплаченной суммы и активных заявок на оплату. "
                "Сначала отмените незавершённые оплаты в кассе."
            ),
            "code": "active_payments_exceed_total",
        })
    return {"confirmed": confirmed, "reserved": reserved}


def _bags_by_product(items) -> Counter:
    """Мешки позиций по товару; позиции удалённого товара склад не сверяет."""
    bags = Counter()
    for item in items:
        if item.product_id is not None:
            bags[item.product_id] += item.quantity
    return bags


def _create_items(order: Order, items_data: list, prices: dict | None) -> tuple[list[OrderItem], dict]:
    """Создать позиции и разложить цены по товару ({product_id: цена}) на id позиций."""
    created = [OrderItem.objects.create(order=order, **item) for item in items_data]
    prices = prices or {}
    return created, {item.id: prices.get(str(item.product_id)) for item in created}


@transaction.atomic
def create_staff_order(
    user,
    data: dict,
    items_data: list,
    *,
    prices: dict | None = None,
    template_order: Order | None = None,
    backdate: dict | None = None,
) -> Order:
    """Новый заказ сотрудника: склад, остаток, позиции, цены и подтверждение.

    ``data`` — поля заказа из формы. ``prices`` — {product_id: цена за мешок}:
    заказ с ценами подтверждается сразу, если у сотрудника есть право и у заказа
    есть отдел, иначе цены лишь фиксируются в позициях. ``template_order`` —
    заказ, по которому создан этот («Повторить»). ``backdate`` — параметры
    :func:`fixation.fixate_order` для заказа задним числом.
    """
    from apps.warehouse.services import ensure_products_available, resolve_warehouse

    if backdate is not None:
        # Права проверяем до записи: отказ откатывает всю транзакцию.
        assert_can_fixate(user, paid=backdate.get("paid", False))
    warehouse = resolve_warehouse(data.get("warehouse"))
    # Исторический заказ склад не списывает, поэтому и остаток на сегодня
    # для него не важен — товара могло уже не остаться.
    if backdate is None:
        ensure_products_available(
            (item["product"] for item in items_data),
            warehouse=warehouse,
        )
    fields = {**data, "warehouse": warehouse, "created_by": user}
    if fields.get("truck_number") or fields.get("trailer_number"):
        fields["truck_number_set_by"] = user
    fields.setdefault("currency", fields["client"].currency)
    if template_order is not None:
        fields["repeated_from"] = template_order
    if prices:
        fields["status"] = "pending"
    order = Order.objects.create(**fields)
    _, prices_by_item = _create_items(order, items_data, prices)
    if prices:
        if user.has_perm_code("orders.confirm") and order.department:
            confirm_order(order, user, prices=prices_by_item)
        else:
            apply_item_prices(order, prices_by_item, user)
        order.refresh_from_db()
    if template_order is not None:
        log_event(
            "order_repeat",
            f"Создан заказ #{order.pk} по шаблону заказа #{template_order.pk}",
            user=user,
            order=order,
            payload={
                "source_order_id": template_order.pk,
                "new_order_id": order.pk,
                "mode": "reviewed_template",
            },
        )
    if backdate is not None:
        order = fixate_order(order, user, set_created=True, **backdate)
    return order


@transaction.atomic
def set_order_warehouse(order: Order, warehouse, user, *, check_items: bool = True) -> Order:
    """Сменить склад отгрузки — только до подтверждения заказа.

    Отключённый склад, уже закреплённый за заказом, остаётся читаемым и
    сравнимым, но новый склад обязан быть действующим. ``check_items=False`` —
    позиции заменяются следом, и остаток на новом складе проверит ``replace_items``.
    """
    from apps.warehouse.services import ensure_products_available, resolve_warehouse

    caller_order = order
    order = lock_live_order(order, user)
    requested = resolve_warehouse(warehouse, require_active=False)
    if requested.pk == order.warehouse_id:
        return order
    if order.status in (*AWAITING_SHIPMENT_STATUSES, "shipped"):
        raise ValidationError({
            "detail": "Склад отгрузки нельзя изменить после подтверждения заказа",
            "code": "warehouse_locked",
        })
    order.warehouse = caller_order.warehouse = resolve_warehouse(requested)
    order.save(update_fields=["warehouse"])
    if check_items:
        current_items = list(order.items.select_related("product"))
        deleted = [item.product_label for item in current_items if item.product_id is None]
        if deleted:
            raise ValidationError({
                "detail": "Нельзя сменить склад: удалены товары — " + ", ".join(deleted),
                "code": "product_deleted",
            })
        ensure_products_available(
            (item.product for item in current_items),
            warehouse=order.warehouse,
        )
    return order


@transaction.atomic
def replace_items(
    order: Order,
    items_data: list,
    prices: dict | None,
    user,
    *,
    edit_reason: str = "",
) -> Order:
    """Заменить позиции заказа (редактирование).

    prices приходит по товару: {product_id: цена за мешок}. После подтверждения
    каждая позиция обязана получить цену — иначе сумма «поплывёт» на базовый
    прайс и испортит долги.
    """
    # Блокируем строку заказа: правка не должна гоняться со стартом загрузки
    # или выездом. Они берут ту же строку Order до снимка цели/списания склада.
    order = lock_live_order(order, user)
    # An open camera session owns the order's loading workflow. The parent
    # Order row is the shared serialization fence with counting.start, so item
    # edits cannot race the transition while camera-PC calls are in flight.
    if has_open_ai_session(order):
        raise ValidationError({
            "detail": "Состав заказа уже закреплён за AI-погрузкой",
            "code": "ai_session_active",
        })
    if order.status in ITEMS_LOCKED_STATUSES:
        raise ValidationError({
            "detail": "Состав заказа нельзя менять во время загрузки",
            "code": "items_locked",
        })
    if not items_data:
        raise ValidationError(
            {"detail": "В заказе должна остаться хотя бы одна позиция",
             "code": "items_empty"})

    is_shipped = order.status == "shipped"
    reason = _shipped_edit_reason(edit_reason) if is_shipped else ""
    warehouse = order.warehouse
    old_items = list(
        # ``product`` is nullable for historical rows. Lock only OrderItem;
        # PostgreSQL cannot apply FOR UPDATE to the nullable side of the outer
        # join introduced by select_related("product").
        OrderItem.objects.select_for_update(of=("self",))
        .select_related("product")
        .filter(order=order)
        .order_by("id")
    )
    if is_shipped and any(item.product_id is None for item in old_items):
        deleted = ", ".join(
            item.product_label for item in old_items if item.product_id is None
        )
        raise ValidationError({
            "detail": "Нельзя сверить склад: удалены товары — " + deleted,
            "code": "product_deleted",
        })

    # Before shipment, order admission keeps the existing availability rule.
    # A shipped edit instead corrects a historical inventory fact and may need
    # to deduct a product whose current balance is zero or already negative.
    if not is_shipped:
        from apps.warehouse.services import ensure_products_available

        ensure_products_available(
            (item["product"] for item in items_data),
            warehouse=warehouse,
            require_active=False,
        )

    old_total = order.total_amount
    old_payload = [_edit_item_payload(item) for item in old_items]
    old_quantities = _bags_by_product(old_items)

    OrderItem.objects.filter(order=order).delete()
    created, prices_by_item = _create_items(order, items_data, prices)
    if (any(v is not None for v in prices_by_item.values())
            or order.status in ITEMS_REQUIRE_PRICES_STATUSES):
        apply_item_prices(order, prices_by_item, user)

    new_total = order.total_amount
    payment_exposure = {"confirmed": Decimal("0"), "reserved": Decimal("0")}
    stock_changes = []
    if is_shipped:
        # Payment locks come before stock locks everywhere in this operation.
        # Payment mutations take the Order lock first, so this cannot deadlock
        # with a concurrent cashier action.
        payment_exposure = _validate_payment_exposure(order, new_total)
        new_quantities = _bags_by_product(created)
        product_ids = set(old_quantities) | set(new_quantities)
        stock_deltas = {
            product_id: old_quantities[product_id] - new_quantities[product_id]
            for product_id in product_ids
            if old_quantities[product_id] != new_quantities[product_id]
        }
        from apps.warehouse.services import reconcile_shipment_stock

        stock_changes = reconcile_shipment_stock(
            stock_deltas,
            order=order,
            user=user,
            reason=reason,
            warehouse=warehouse,
        )

    # Сумма могла измениться — сохранённый статус оплаты приводим к факту.
    _apply_payment_status(order, user)
    shipment_bags = None
    if is_shipped:
        from apps.shipments.models import Shipment

        shipment_bags = (
            Shipment.objects.filter(order=order)
            .values_list("bags_loaded", flat=True)
            .first()
        )
    new_payload = [_edit_item_payload(item) for item in created]
    log_event(
        "order_edit",
        (
            f"Состав отгруженного заказа скорректирован. Причина: {reason}"
            if is_shipped
            else f"Позиции заказа обновлены ({len(created)} шт.)"
        ),
        user=user,
        order=order,
        payload={
            "action": "shipment_correction" if is_shipped else "items_replaced",
            "reason": reason or None,
            "old_total": str(old_total),
            "new_total": str(new_total),
            "old_items": old_payload,
            "items": new_payload,
            "stock_changes": stock_changes,
            "confirmed_payments": str(payment_exposure["confirmed"]),
            "reserved_payments": str(payment_exposure["reserved"]),
            # Physical evidence is intentionally observed, never rewritten.
            "shipment_bags_loaded": shipment_bags,
        },
    )
    order.refresh_from_db()
    return order


@transaction.atomic
def reject_order(order: Order, user, *, reason: str) -> Order:
    caller_order = order
    order = lock_live_order(order, user, any_department=True)
    if order.status != "pending":
        raise ValidationError(
            {"detail": "Отклонить можно только заказ на рассмотрении", "code": "invalid_status"})
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 500:
        raise ValidationError(
            {"reason": "Укажите причину отклонения (до 500 символов)"}
        )
    assert_order_has_no_money(order)
    order.rejection_reason = reason.strip()
    order.save(update_fields=["rejection_reason"])
    rejected = _apply_status(
        order,
        "rejected",
        user,
        f"Заявка отклонена: {order.rejection_reason}",
        payload={"reason": order.rejection_reason, "action": "order_rejected"},
    )
    caller_order.rejection_reason = order.rejection_reason
    caller_order.status = rejected.status
    return rejected


def can_set_truck_number(order: Order, user) -> bool:
    """Номер транспорта меняет тот, кто его ввёл: клиент или сотрудник.

    Прицеп живёт под тем же владельцем, что и тягач, — это одна пара.
    """
    if order.truck_number_set_by_id is None or not (order.truck_number or order.trailer_number):
        return True
    if order.truck_number_set_by_id == user.id:
        return True
    # number owned by a client → only that client may change it
    if order.truck_number_set_by.is_client:
        return False
    # number set by staff → any staff may change it
    return not user.is_client


def _can_edit_status(user) -> bool:
    return bool(user) and not getattr(user, "is_client", False) and user.has_perm_code("orders.edit")


def _validate_manual_status(to_status: str) -> None:
    if to_status not in Order.STATUSES:
        raise ValidationError({"detail": "Неизвестный статус", "code": "bad_status"})
    # Внутренние физические этапы нельзя ставить сырым status override даже
    # суперпользователю: иначе можно обойти AI ownership и Shipment. Для этих
    # переходов существуют отдельные доменные действия.
    if to_status not in PUBLIC_MANUAL_STATUSES:
        raise ValidationError({
            "detail": "Доступны статусы: " + ", ".join(
                PUBLIC_STATUS_LABELS[status]
                for status in PUBLIC_MANUAL_STATUSES
            ),
            "code": "status_not_available",
        })


def _assert_not_active_loading(order: Order) -> None:
    if order.status in ON_POST_STATUSES:
        raise ValidationError({
            "detail": "Сначала завершите или верните текущую погрузку",
            "code": "active_loading",
        })


@transaction.atomic
def set_transport_type(order: Order, value: str, user) -> Order:
    """Change truck/train only before a physical workflow owns the order."""
    if value not in Order.TRANSPORT_TYPES:
        raise ValidationError({
            "detail": "Неизвестный вид транспорта",
            "code": "bad_transport_type",
        })
    caller_order = order
    order = lock_live_order(order, user)
    if value == order.transport_type:
        return order
    assert_no_open_ai_session(order)
    if order.status in ENTERED_POST_STATUSES:
        raise ValidationError({
            "detail": "Вид транспорта нельзя изменить после начала погрузки",
            "code": "transport_type_locked",
        })
    old = order.transport_type
    order.transport_type = value
    if value == "train":
        # У вагона нет полуприцепа: номер прицепа фуры с ним не уезжает.
        order.trailer_number = ""
    order.save(update_fields=["transport_type", "trailer_number"])
    caller_order.transport_type = value
    caller_order.trailer_number = order.trailer_number
    log_event(
        "status",
        "Вид транспорта изменён",
        user=user,
        order=order,
        payload={"from": old, "to": value},
    )
    return order


@transaction.atomic
def set_order_department(order: Order, value: str, user, *, any_department=False) -> Order:
    """Keep an active camera order inside the scope that can stop it."""
    caller_order = order
    order = lock_live_order(order, user, any_department=any_department)
    if not value and order.status not in REVIEWABLE_STATUSES:
        raise ValidationError({"department": "У подтверждённого заказа должен быть отдел продаж"})
    if value == order.department:
        return order
    if order.payments.exists():
        raise ValidationError(
            {
                "detail": "Отдел нельзя изменить после создания платёжной операции: он закреплён для учёта и выписок",
                "code": "department_has_payments",
            }
        )
    client = Client.objects.select_for_update().get(pk=order.client_id)
    if client.department_id and value != client.department.code:
        raise ValidationError({"department": "Выберите отдел, к которому закреплён клиент"})
    assert_no_open_ai_session(order)
    if order.status in ENTERED_POST_STATUSES:
        raise ValidationError({
            "detail": "Отдел нельзя изменить после начала погрузки",
            "code": "department_locked",
        })
    old = order.department
    order.department = value
    order.save(update_fields=["department"])
    caller_order.department = value
    log_event(
        "status",
        "Отдел заказа изменён",
        user=user,
        order=order,
        payload={"from": old, "to": value},
    )
    return order


def _assert_not_shipped(order: Order) -> None:
    """Ручная смена статуса отгруженного заказа закрыта.

    Завершённый заказ уже списал склад и создал финансовый след. Обратный
    переход без отдельной операции возврата исказил бы остатки и долги.
    """
    if order.status == "shipped":
        raise ValidationError({
            "detail": "Отгруженный заказ возвращается отдельной операцией с обязательной причиной",
            "code": "shipped_is_final",
        })


@transaction.atomic
def _force_set_status(order: Order, to_status: str, user,
                      bags_loaded: int | None = None) -> Order:
    _validate_manual_status(to_status)
    # AI start takes the same row lock before reserving a camera. Holding it
    # through the check+transition closes confirmed→cancelled/pending races.
    order = lock_live_order(order, user)
    old = order.status
    _assert_not_shipped(order)

    assert_no_open_ai_session(order)
    assert_money_allows_status(order, to_status)

    if old in REVIEWABLE_STATUSES and to_status in ("confirmed", "shipped"):
        raise ValidationError({
            "detail": "Сначала выберите отдел и подтвердите заказ в его карточке",
            "code": "order_confirmation_required",
        })

    if to_status == "shipped":
        from apps.shipments.services import manual_complete_order
        manual_complete_order(order, bags_loaded, user)
        return order

    # Внутренние стадии могут содержать Shipment, счёт и камеру. Сбрасываем их
    # одной доменной операцией; голая смена status оставила бы занятый слот.
    if old in ON_POST_STATUSES and to_status in ROLLBACK_TARGET_STATUSES:
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

    Всем сотрудникам доступны только четыре ручных состояния. Внутренние
    этапы меняются отдельными операциями погрузки/выезда.
    """
    # Establish the canonical Order -> StatusChangeRequest lock order before
    # either applying the transition or inserting an approval request.
    order = lock_live_order(order, user)
    _validate_manual_status(to_status)
    if to_status == order.status:
        raise ValidationError({"detail": "Статус уже такой", "code": "no_change"})
    if _can_edit_status(user):
        _force_set_status(order, to_status, user, bags_loaded=bags_loaded)
        return {"applied": True, "request": None}
    if bags_loaded is not None:
        # Approval requests store a status, not a physical count. Silently
        # dropping this value could deduct a different quantity on approval.
        raise ValidationError({
            "detail": "Количество мешков может указать только сотрудник с правом прямой отгрузки",
            "code": "bags_loaded_requires_direct_permission",
        })
    # Запрос, который нельзя одобрить, не создаём: сказать об этом сразу.
    # Отгруженный — раньше денег: возврат ему не поможет, откат отдельной операцией.
    _assert_not_shipped(order)
    assert_money_allows_status(order, to_status)
    req = StatusChangeRequest.objects.create(
        order=order, to_status=to_status, requested_by=user)
    log_event("status_request",
              _status_message("Запрос ручной смены статуса", order.status, to_status),
              user=user, order=order,
              payload={"request_id": req.id, "from": order.status, "to": to_status})
    return {"applied": False, "request": req}


@transaction.atomic
def approve_status_change(req: StatusChangeRequest, user) -> StatusChangeRequest:
    order = lock_live_order(req.order_id, user)
    req = (
        StatusChangeRequest.objects.select_for_update()
        .select_related("order")
        .get(pk=req.pk, order_id=order.pk)
    )
    if req.status != "pending":
        raise ValidationError({"detail": "Запрос уже обработан", "code": "already_decided"})
    _force_set_status(order, req.to_status, user)
    req.status = "approved"
    req.decided_by = user
    req.decided_at = timezone.now()
    req.save(update_fields=["status", "decided_by", "decided_at"])
    log_event("status_request", "Запрос смены статуса одобрен", user=user, order=req.order,
              payload={"request_id": req.id})
    return req


@transaction.atomic
def reject_status_change(req: StatusChangeRequest, user) -> StatusChangeRequest:
    order = lock_live_order(req.order_id, user)
    req = (
        StatusChangeRequest.objects.select_for_update()
        .select_related("order")
        .get(pk=req.pk, order_id=order.pk)
    )
    if req.status != "pending":
        raise ValidationError({"detail": "Запрос уже обработан", "code": "already_decided"})
    req.status = "rejected"
    req.decided_by = user
    req.decided_at = timezone.now()
    req.save(update_fields=["status", "decided_by", "decided_at"])
    log_event("status_request", "Запрос смены статуса отклонён", user=user, order=req.order,
              payload={"request_id": req.id})
    return req


def _lock_any_order(order: Order, *, purged_detail: str) -> Order:
    """Заблокировать заказ вместе с корзиной (all_objects); стёртый — ошибка."""
    try:
        locked = Order.all_objects.select_for_update().get(pk=order.pk)
    except Order.DoesNotExist as exc:
        raise ValidationError(
            {"detail": "Заказ не найден", "code": "not_found"}
        ) from exc
    if locked.purged_at is not None:
        raise ValidationError({"detail": purged_detail, "code": "already_purged"})
    return locked


@transaction.atomic
def soft_delete_order(order: Order, user) -> Order:
    """Мягкое удаление: заказ уезжает в «Корзину». Из списков и аналитики
    исчезает (default-manager его не видит), но данные сохраняются и заказ
    можно восстановить. Деньги отгруженного заказа остаются в журнале кассы
    и выписках (:func:`models.money_ledger_q`)."""
    order = _lock_any_order(order, purged_detail="Заказ удалён безвозвратно")
    if order.deleted_at is not None:
        raise ValidationError({"detail": "Заказ уже в корзине", "code": "already_deleted"})
    assert_order_user_scope(order, user)
    assert_no_open_ai_session(order)
    _assert_not_active_loading(order)
    # Отгруженный заказ — состоявшаяся продажа: его деньги остаются в денежной
    # ленте и из корзины. У неотгруженного они пропали бы из кассы и выписок.
    if order.status != "shipped":
        assert_order_has_no_money(order)
    order.deleted_at = timezone.now()
    order.deleted_by = user
    order.save(update_fields=["deleted_at", "deleted_by"])
    log_event("order", "Заказ удалён в корзину", user=user, order=order,
              payload={"order_id": order.id})
    return order


@transaction.atomic
def restore_order(order: Order, user) -> Order:
    """Восстановить заказ из корзины — снова участвует в отчётах и списках."""
    order = _lock_any_order(
        order, purged_detail="Заказ удалён безвозвратно и не может быть восстановлен",
    )
    if order.deleted_at is None:
        raise ValidationError({"detail": "Заказ не в корзине", "code": "not_deleted"})
    assert_order_user_scope(order, user)
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
    order = _lock_any_order(order, purged_detail="Заказ уже удалён безвозвратно")
    if order.deleted_at is None:
        raise ValidationError(
            {"detail": "Сначала переместите заказ в корзину", "code": "not_deleted"})
    assert_order_user_scope(order, user)
    assert_no_open_ai_session(order)
    _assert_not_active_loading(order)
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
