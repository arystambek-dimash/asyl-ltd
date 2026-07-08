from datetime import date
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from apps.eventlog.services import log_event
from apps.notifications.services import notify
from apps.clients.services import is_payment_window_open
from .models import Order, Payment, StatusChangeRequest


def _validate_payment_open(order: Order) -> None:
    # Отдел 2 «Сити»: менеджер запрашивает и принимает оплату с выезда —
    # доступно с момента создания заявки, без графика магазина.
    if order.department == "field":
        if order.status in ("draft", "rejected", "cancelled"):
            raise ValidationError(
                {"detail": "Оплата недоступна для этой заявки", "code": "payment_not_open"}
            )
        return
    if order.status != "shipped":
        raise ValidationError(
            {"detail": "Оплата доступна только после отгрузки", "code": "payment_not_open"}
        )
    if order.store and not is_payment_window_open(order.store, date.today()):
        raise ValidationError(
            {"detail": f"Оплата для магазина «{order.store.name}» сегодня недоступна",
             "code": "payment_window_closed"}
        )


PAYMENT_STAGE_LABELS = {
    "requested": "запрошена", "received": "принята",
    "accountant_ok": "подтверждена бухгалтером",
    "confirmed": "подтверждена кассиром", "rejected": "отклонена",
}


def _set_payment_stage(payment: Payment, status: str, user) -> Payment:
    """Перевести оплату на следующий шаг цепочки с фиксацией автора и времени."""
    stamp = {
        "received": ("received_by", "received_at"),
        "accountant_ok": ("accountant_by", "accountant_at"),
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
    log_event("payment", f"Оплата {payment.amount} {PAYMENT_STAGE_LABELS[status]}",
              user=user, order=payment.order,
              payload={"payment_id": payment.id, "amount": str(payment.amount),
                       "payment_stage": status})
    return payment


@transaction.atomic
def add_payment(order: Order, amount, user, method="cash", stage="received") -> Payment:
    """Начало цепочки оплаты: «запрошена» (счёт выставлен) или «принята» (деньги у менеджера)."""
    _validate_payment_open(order)
    if stage not in ("requested", "received"):
        raise ValidationError({"detail": "Недопустимый шаг оплаты", "code": "bad_stage"})
    if amount is None or Decimal(str(amount)) <= 0:
        raise ValidationError(
            {"detail": "Сумма оплаты должна быть больше нуля", "code": "invalid_amount"}
        )
    payment = Payment.objects.create(
        order=order, amount=amount, method=method, status=stage, recorded_by=user,
        **({"received_by": user, "received_at": timezone.now()}
           if stage == "received" else {}))
    log_event("payment", f"Оплата {amount} ({method}) {PAYMENT_STAGE_LABELS[stage]}",
              user=user, order=order,
              payload={"amount": str(amount), "method": method, "payment_stage": stage})
    return payment


@transaction.atomic
def pay_via_bank(order: Order, user) -> Payment:
    """Оплата остатка через банк (заглушка): создаёт оплату «принята» — далее сверка и касса."""
    _validate_payment_open(order)
    remaining = order.total_amount - order.paid_total
    if remaining <= 0:
        raise ValidationError({"detail": "Заказ уже оплачен", "code": "already_paid"})
    # TODO: здесь будет реальный запрос в банк. Пока — заглушка.
    payment = Payment.objects.create(
        order=order, amount=remaining, method="card", status="received",
        recorded_by=user, received_by=user, received_at=timezone.now())
    log_event("payment", f"Банковская оплата {remaining} принята (заглушка)",
              user=user, order=order,
              payload={"amount": str(remaining), "method": "card", "channel": "bank_stub",
                       "payment_stage": "received"})
    return payment


@transaction.atomic
def create_client_payment(order: Order, method: str, user) -> Payment:
    _validate_payment_open(order)
    if method not in ("card", "kaspi"):
        raise ValidationError({"detail": "Недопустимый способ оплаты", "code": "bad_method"})
    remaining = order.total_amount - order.paid_total
    if remaining <= 0:
        raise ValidationError({"detail": "Заказ уже оплачен", "code": "already_paid"})
    # Несколько кликов «оплатил» не должны плодить дубли — одна заявка на оплату.
    payment, created = Payment.objects.update_or_create(
        order=order, status="received",
        defaults={"amount": remaining, "method": method, "recorded_by": user,
                  "received_by": user, "received_at": timezone.now()},
    )
    log_event("payment", f"Клиент {'инициировал' if created else 'обновил'} оплату {remaining} ({method})",
              user=user, order=order, payload={"amount": str(remaining), "method": method})
    return payment


# Разрешённые переходы цепочки подтверждения оплаты.
PAYMENT_TRANSITIONS = {
    "requested": "received",
    "received": "accountant_ok",
    "accountant_ok": "confirmed",
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
    """Бухгалтер сверил и подтвердил оплату по заказу."""
    return _advance_payment(payment, "received", user)


@transaction.atomic
def cashier_confirm_payment(payment: Payment, user) -> Payment:
    """Кассир подтвердил поступление денег — только теперь оплата учтена."""
    _advance_payment(payment, "accountant_ok", user)
    _apply_payment_status(payment.order, user)
    return payment


@transaction.atomic
def reject_payment(payment: Payment, user) -> Payment:
    if payment.status in ("confirmed", "rejected"):
        raise ValidationError(
            {"detail": "Оплата уже финализирована", "code": "invalid_payment_stage"})
    return _set_payment_stage(payment, "rejected", user)


@transaction.atomic
def approve_debt(order: Order, user) -> Order:
    order.debt_override = True
    order.debt_override_by = user
    order.settlement_intent = "debt"
    order.save(update_fields=["debt_override", "debt_override_by", "settlement_intent"])
    log_event("debt_override", "Долг одобрен", user=user, order=order)
    return order


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
            {"detail": f"Недопустимый переход: {order.status} → {to_status}",
             "code": "invalid_transition"})
    old = order.status
    order.status = to_status
    order.save(update_fields=["status"])
    log_event("status", message or f"Статус: {old} → {to_status}",
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
        if raw is None or Decimal(str(raw)) <= 0:
            raise ValidationError(
                {"detail": f"Укажите цену для «{item.product}»", "code": "price_required"})
        price = Decimal(str(raw))
        item.unit_price = price
        item.save(update_fields=["unit_price"])
        ClientPrice.objects.update_or_create(
            client=order.client, product=item.product,
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


@transaction.atomic
def _force_set_status(order: Order, to_status: str, user) -> Order:
    if to_status not in Order.STATUSES:
        raise ValidationError({"detail": "Неизвестный статус", "code": "bad_status"})
    old = order.status
    order.status = to_status
    order.save(update_fields=["status"])
    log_event("status_override",
              f"Статус заказа изменён вручную: {old} → {to_status}",
              user=user, order=order, payload={"from": old, "to": to_status})
    return order


@transaction.atomic
def request_status_change(order: Order, to_status: str, user) -> dict:
    """Главный оператор (orders.edit) меняет сразу; остальные создают запрос."""
    if to_status not in Order.STATUSES:
        raise ValidationError({"detail": "Неизвестный статус", "code": "bad_status"})
    if to_status == order.status:
        raise ValidationError({"detail": "Статус уже такой", "code": "no_change"})
    if _can_edit_status(user):
        _force_set_status(order, to_status, user)
        return {"applied": True, "request": None}
    req = StatusChangeRequest.objects.create(
        order=order, to_status=to_status, requested_by=user)
    log_event("status_request",
              f"Запрос ручной смены статуса: {order.status} → {to_status}",
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
