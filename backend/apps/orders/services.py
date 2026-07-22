from decimal import Decimal, InvalidOperation
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from apps.eventlog.services import log_event
from apps.notifications.services import notify
from apps.clients.services import is_payment_window_open
from .models import Order, Payment, StatusChangeRequest
from .statuses import PUBLIC_MANUAL_STATUSES, PUBLIC_STATUS_LABELS, public_status_label


MAX_MONEY = Decimal("9999999999.99")


def _positive_money(raw, *, detail: str, code: str) -> Decimal:
    """Validate public money input before it reaches a DecimalField/database."""
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError({"detail": detail, "code": code}) from exc
    if not value.is_finite() or value <= 0 or value > MAX_MONEY:
        raise ValidationError({"detail": detail, "code": code})
    return value


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
        (payment.amount for payment in locked.payments.all()
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
def create_client_payment(order: Order, method: str, user) -> Payment:
    _validate_payment_open(order)
    if method not in ("invoice", "kaspi", "cash", "card"):
        raise ValidationError({"detail": "Недопустимый способ оплаты", "code": "bad_method"})
    if method == "invoice":
        missing = []
        if not order.client.iin.strip():
            missing.append("ИИН/БИН")
        if not (order.client.company_name.strip() or order.client.name):
            missing.append("название ТОО / ИП")
        if missing:
            raise ValidationError({
                "detail": "Для счета заполните реквизиты клиента: " + ", ".join(missing),
                "code": "client_requisites_missing",
            })
    remaining = order.total_amount - order.paid_total
    if remaining <= 0:
        raise ValidationError({"detail": "Заказ уже оплачен", "code": "already_paid"})
    stage = "received" if method in ("kaspi", "card") else "requested"
    # Несколько кликов и смена способа не должны плодить параллельные заявки.
    payment = (order.payments.select_for_update()
               .filter(status__in=Payment.IN_PROGRESS_STATUSES)
               .order_by("-paid_at").first())
    created = payment is None
    if created:
        payment = Payment.objects.create(
            order=order, amount=remaining, method=method, status=stage,
            recorded_by=user,
            **({"received_by": user, "received_at": timezone.now()}
               if stage == "received" else {}),
        )
    else:
        payment.amount = remaining
        payment.method = method
        payment.status = stage
        payment.recorded_by = user
        payment.received_by = user if stage == "received" else None
        payment.received_at = timezone.now() if stage == "received" else None
        payment.save(update_fields=[
            "amount", "method", "status", "recorded_by", "received_by", "received_at",
        ])
    public_method = "invoice" if method == "card" else method
    order.payment_method = public_method
    order.settlement_intent = "instant"
    order.debt_requested = False
    order.save(update_fields=["payment_method", "settlement_intent", "debt_requested"])
    action = "инициировал" if created else "обновил"
    log_event(
        "payment", f"Клиент {action} оплату {remaining} {order.currency} ({method})",
              user=user, order=order,
              payload={"payment_id": payment.id, "amount": str(remaining), "method": method,
                       "currency": order.currency, "payment_stage": stage},
    )
    return payment


@transaction.atomic
def request_client_debt(order: Order, user) -> Order:
    """Зафиксировать выбор «В долг» без создания денежной оплаты."""
    if order.status != "shipped":
        raise ValidationError({"detail": "Долг фиксируется после отгрузки",
                               "code": "invalid_status"})
    order.payments.select_for_update().filter(
        status__in=Payment.IN_PROGRESS_STATUSES,
    ).update(status="rejected")
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
    return _advance_payment(payment, "requested", user)


@transaction.atomic
def accountant_confirm_payment(payment: Payment, user) -> Payment:
    """Бухгалтер (касса) сверил и подтвердил оплату — деньги учтены сразу."""
    payment = Payment.objects.select_for_update().select_related("order").get(pk=payment.pk)
    _advance_payment(payment, "received", user)
    _apply_payment_status(payment.order, user)
    return payment


@transaction.atomic
def reopen_confirmed_payment(payment: Payment, user) -> Payment:
    """Вернуть ошибочно подтверждённую оплату на повторное подтверждение.

    Денежный итог заказа пересчитывается сразу, а исходное подтверждение и
    отмена остаются отдельными append-only событиями в журнале.
    """
    payment = Payment.objects.select_for_update().select_related("order").get(pk=payment.pk)
    if payment.status != "confirmed":
        raise ValidationError({
            "detail": "Вернуть можно только подтверждённую оплату",
            "code": "invalid_payment_stage",
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
    _apply_payment_status(payment.order, user)
    return payment


@transaction.atomic
def reject_payment(payment: Payment, user) -> Payment:
    if payment.status in ("confirmed", "rejected"):
        raise ValidationError(
            {"detail": "Оплата уже финализирована", "code": "invalid_payment_stage"})
    return _set_payment_stage(payment, "rejected", user)


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
    if not can_set_truck_number(order, user):
        raise ValidationError(
            {"detail": "Номер КАМАЗа задан другим пользователем", "code": "forbidden"})
    order.truck_number = value
    order.truck_number_set_by = user
    order.save(update_fields=["truck_number", "truck_number_set_by"])
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
    """Окончательное удаление: только из корзины, данные стираются безвозвратно.
    Позиции, оплаты и отгрузка каскадом; события журнала остаются (order → null)."""
    if order.deleted_at is None:
        raise ValidationError(
            {"detail": "Сначала переместите заказ в корзину", "code": "not_deleted"})
    log_event("order", f"Заказ #{order.id} удалён навсегда", user=user,
              payload={"order_id": order.id, "client_id": order.client_id,
                       "total_amount": str(order.total_amount)})
    order.delete()
