"""Перенос акта сверки 1С в историю клиента (решения владельца 13.10.2026).

Дебет акта (Асыл отпустил клиенту) — отгруженный заказ задним числом на
скрытый товар «Реализация по акту сверки (1С)», 1 × сумма документа; склад не
трогается (:func:`orders.services.create_staff_order` с ``backdate``). Кредит
(клиент закрыл: деньги или зачёт по акту) гасит долги клиента от старого заказа
к новому по правилам «Внести оплату»
(:func:`orders.debt_payments.plan_debt_payment`), каждая доля — обычная оплата
заказа датой кредита. Что больше долга — предоплата: её гасят следующие
отгрузки, а в кассе она остаётся деньгами дня поступления. Когда строк не
осталось, неразнесённый остаток ложится переплатой на последний отгруженный
заказ клиента в валюте — касса видит его в «К возврату», карточка клиента — как
«Мы должны клиенту». Аванса у клиента нет.

Повтор документа исключает реестр :class:`ClientActDocument`. Он же делает
импорт порциями продолжаемым: gunicorn и nginx режут запрос на 60 с, а акт за
год — сотни заказов, и одно поступление гасит десятки из них.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.catalog.models import Product
from apps.clients.services import lock_client_orders, lock_scoped_client
from apps.common.money import ZERO, money_string, money_text
from apps.common.text import match_key
from apps.eventlog.services import log_event
from apps.orders.backdate import log_payment_backdated, payment_moment
from apps.orders.debt import DEBT_STATUS, available_to_pay, financial_orders, oldest_debt_first, order_overpaid
from apps.orders.debt_payments import debt_candidates, plan_debt_payment
from apps.orders.models import Order, Payment
from apps.orders.services import create_staff_order, record_staff_payment, sync_payment_status
from apps.sales.access import assigned_department_id
from apps.sales.models import Department

from .models import ClientActDocument
from .parser import OFFSET_METHOD, ActRow, ParsedAct

IMPORT_PERMS = ("orders.edit", "orders.confirm", "payments.create")
# Записей (заказов и оплат) за запрос: каждая — десятки запросов к базе, а
# одно поступление за год гасит сотни отгрузок. Бюджет держит запрос далеко
# от 60 с gunicorn/nginx.
CHUNK_OPS = 30
# В акте только суммы: строка заказа — скрытый товар (не в каталоге и не в
# выборе товаров), 1 × сумма документа. 2 кг — чтобы тоннаж отгрузок почти
# не врал.
ACT_PRODUCT = {"name": "Реализация по акту сверки (1С)", "color": "Blue", "weight_kg": Decimal("2")}


@dataclass(frozen=True)
class _Plan:
    start: date
    default_start: date
    rows: list[ActRow]
    opening_order: Decimal | None
    imported: int
    before_start: int
    overlap: dict
    balance_after: Decimal
    problems: list[str]


def assert_can_import(user) -> None:
    if not all(user.has_perm_code(code) for code in IMPORT_PERMS):
        raise PermissionDenied(
            "Импорт акта сверки доступен с правами изменения и подтверждения заказов и приёма оплат"
        )


def _note(act_number: str, document: str) -> str:
    return f"Акт сверки № {act_number}: {document}" if act_number else f"Акт сверки: {document}"


def _opening_document(act: ParsedAct) -> str:
    return f"Сальдо на начало {act.period_start:%d.%m.%Y}"


def _side(balance: Decimal, currency: str) -> str:
    """«клиент должен 500 USD» / «мы должны клиенту 75 USD» / «0 USD»."""
    if balance > 0:
        return f"клиент должен {money_text(balance, currency)}"
    if balance < 0:
        return f"мы должны клиенту {money_text(-balance, currency)}"
    return money_text(ZERO, currency)


def _day(moment) -> date:
    return timezone.localdate(moment)


def _client_orders(client, currency: str) -> list:
    return list(
        Order.objects.filter(client=client, currency=currency)
        .select_related("shipment")
        .prefetch_related("items", "payments__payment_refunds")
    )


def _refunds(payment) -> list:
    """Возвраты оплаты, вычитаемые из её денег (как ``Payment.refunded_amount``)."""
    return [refund for refund in payment.payment_refunds.all() if refund.status == "completed"]


def _balance(orders, before=None) -> Decimal:
    """Сколько клиент должен в валюте заказов (минус — мы ему), как сальдо акта 1С.

    Дебет — только отгруженное: подтверждённого, но не отгруженного заказа в 1С
    ещё нет. Кредит — подтверждённые деньги по заказам в обороте (и предоплата)
    за вычетом возвратов. ``before`` — состояние на этот момент: продажи, деньги
    и возвраты раньше него, а не сегодняшние суммы.
    """
    balance = ZERO
    for order in financial_orders(orders):
        if order.status == DEBT_STATUS and (before is None or order.sale_at < before):
            balance += order.total_amount
        for payment in order.payments.all():
            if payment.status != "confirmed":
                continue
            if before is None:
                balance -= payment.net_amount
            elif payment.recognized_at < before:
                refunded = sum((r.amount for r in _refunds(payment) if r.recognized_at < before), ZERO)
                balance -= payment.amount - refunded
    return balance


def _crm_overlap(orders, act: ParsedAct, imported_orders: set, imported_payments: set) -> dict:
    """Свои записи CRM (не из актов) в валюте акта за его период.

    Отгрузки и деньги, внесённые в CRM, и возвраты кассы (в 1С это дебет
    расходным ордером) — в том числе по оплатам из актов: переплату акта касса
    отдаёт клиенту «К возврату».
    """
    within = lambda moment: act.period_start <= _day(moment) <= act.period_end  # noqa: E731
    financial = financial_orders(orders)
    own = [
        o for o in financial
        if o.status == DEBT_STATUS and o.pk not in imported_orders and within(o.sale_at)
    ]
    confirmed = [payment for order in financial for payment in order.payments.all() if payment.status == "confirmed"]
    payments = [
        payment
        for payment in confirmed
        if payment.method not in Payment.NON_MONEY_METHODS
        and payment.pk not in imported_payments
        and within(payment.recognized_at)
    ]
    refunds = [refund for payment in confirmed for refund in _refunds(payment) if within(refund.recognized_at)]
    days = (
        [_day(o.sale_at) for o in own]
        + [_day(p.recognized_at) for p in payments]
        + [_day(r.recognized_at) for r in refunds]
    )
    return {
        "orders": len(own),
        "orders_total": sum((o.total_amount for o in own), ZERO),
        "payments": len(payments),
        "payments_total": sum((p.net_amount for p in payments), ZERO),
        "refunds": len(refunds),
        "refunds_total": sum((r.amount for r in refunds), ZERO),
        "last_day": max(days) if days else None,
    }


def _plan(client, act: ParsedAct, start: date | None) -> _Plan:
    """Что импорт сделает сейчас: строки к переносу, сальдо на начало, проблемы."""
    orders = _client_orders(client, act.currency)
    registry = ClientActDocument.objects.filter(client=client, currency=act.currency)
    documents = set(registry.values_list("document", flat=True))
    # Кредит прошлых порций, ещё не разнесённый по заказам (импорт не закончен):
    # это тоже деньги клиента — без них сальдо CRM между порциями врёт.
    waiting = list(registry.filter(kind="payment", unapplied__gt=0).values_list("day", "unapplied"))
    overlap = _crm_overlap(
        orders,
        act,
        set(registry.exclude(order=None).values_list("order_id", flat=True)),
        set(registry.values_list("payments", flat=True)) - {None},
    )
    default_start = overlap["last_day"] + timedelta(days=1) if overlap["last_day"] else act.period_start
    start = max(start or default_start, act.period_start)

    rows, imported, before_start = [], 0, 0
    for row in sorted(act.rows, key=lambda row: row.day):
        if row.document in documents:
            imported += 1
        elif row.day < start:
            before_start += 1
        else:
            rows.append(row)

    problems = []
    future = [row for row in rows if row.day > timezone.localdate()]
    if future:
        problems.append(f"Строка {future[0].line}: дата {future[0].day:%d.%m.%Y} ещё не наступила.")
    opening_order = None
    if start == act.period_start and act.opening and _opening_document(act) not in documents:
        period_start = timezone.make_aware(datetime.combine(act.period_start, time.min))
        in_crm = _balance(orders, before=period_start) - sum(
            (amount for day, amount in waiting if day < act.period_start), ZERO,
        )
        if in_crm == 0 and act.opening > 0:
            opening_order = act.opening
        elif in_crm != act.opening:
            problems.append(
                f"Сальдо на начало {act.period_start:%d.%m.%Y}: по акту {_side(act.opening, act.currency)}, "
                f"в CRM {_side(in_crm, act.currency)}. Сверьте прежние долги клиента или загрузите акт "
                "с более ранней даты."
            )

    sales = sum((row.amount for row in rows if row.kind == "sale"), ZERO)
    paid = sum((row.amount for row in rows if row.kind == "payment"), ZERO)
    unapplied = sum((amount for _day_, amount in waiting), ZERO)
    balance_after = _balance(orders) - unapplied + (opening_order or ZERO) + sales - paid
    # Кредит гасит только отгруженное, остаток ложится переплатой на отгрузку
    # (:func:`_settle_leftover`): без дебета в акте и отгрузок у клиента деньгам
    # некуда лечь — даже если есть подтверждённые, но не отгруженные заказы.
    credit = paid + unapplied
    holder = sales or opening_order or any(order.status == DEBT_STATUS for order in orders)
    if credit > 0 and not holder:
        problems.append(
            f"Переплату {money_text(credit, act.currency)} некуда записать: "
            f"у клиента нет отгруженных заказов в {act.currency}, а в акте нет дебета."
        )
    return _Plan(
        start=start, default_start=default_start, rows=rows, opening_order=opening_order,
        imported=imported, before_start=before_start, overlap=overlap,
        balance_after=balance_after, problems=problems,
    )


def _same_name(client, counterparty: str) -> bool:
    """Контрагент акта похож на клиента: «ОсОО "Дан Агро Групп"» ~ «Дан Агро Групп»."""
    theirs = match_key(counterparty)
    if not theirs:
        return True
    return any(ours and (ours in theirs or theirs in ours) for ours in (
        match_key(client.display_name), match_key(client.name),
    ))


def _count(rows, kind: str, *, method: str | None = None) -> dict:
    chosen = [row for row in rows if row.kind == kind and (method is None or row.method == method)]
    return {"count": len(chosen), "total": money_string(sum((row.amount for row in chosen), ZERO))}


def preview_act(client, act: ParsedAct, user, *, start: date | None = None) -> dict:
    """Предпросмотр импорта — ничего не пишет."""
    assert_can_import(user)
    plan = _plan(client, act, start)
    overlap = plan.overlap
    return {
        "act": {
            "number": act.number,
            "period_start": act.period_start.isoformat(),
            "period_end": act.period_end.isoformat(),
            "counterparty": act.counterparty,
            "currency": act.currency,
            "opening": money_string(act.opening),
            "closing": money_string(act.closing),
            "rows": len(act.rows),
        },
        "name_matches": _same_name(client, act.counterparty),
        "start": plan.start.isoformat(),
        "default_start": plan.default_start.isoformat(),
        "sales": _count(plan.rows, "sale"),
        "payments": _count(plan.rows, "payment"),
        # Из кредита — не деньги (возвраты товара, встречные поставки): «зачёт по акту».
        "offsets": _count(plan.rows, "payment", method=OFFSET_METHOD),
        "imported": plan.imported,
        "before_start": plan.before_start,
        "opening_order": money_string(plan.opening_order) if plan.opening_order else None,
        # Деньги актов, которые прерванный импорт ещё не разнёс по заказам.
        "pending": money_string(_pending_money(client, act.currency)),
        "crm": {
            **overlap,
            "orders_total": money_string(overlap["orders_total"]),
            "payments_total": money_string(overlap["payments_total"]),
            "refunds_total": money_string(overlap["refunds_total"]),
            "last_day": overlap["last_day"].isoformat() if overlap["last_day"] else None,
        },
        "balance_after": money_string(plan.balance_after),
        "problems": plan.problems,
    }


def _act_product() -> Product:
    product, _ = Product.objects.get_or_create(**ACT_PRODUCT, defaults={"is_active": False})
    return product


def _department(client, user) -> str:
    """Отдел заказа: отдел клиента, иначе отдел сотрудника, иначе отдел по умолчанию."""
    if client.department_id is not None:
        return client.department.code
    department_id = assigned_department_id(user)
    if department_id is not None:
        return Department.objects.get(pk=department_id).code
    return Order._meta.get_field("department").get_default()


class _Budget:
    """Сколько записей (заказов и оплат) ещё влезает в этот запрос."""

    def __init__(self, size: int):
        self.left = size
        self.payments = 0
        self.reclaimed = ZERO

    @property
    def spent(self) -> bool:
        return self.left <= 0

    def spend(self) -> None:
        self.left -= 1


def _pay_from(document: ClientActDocument, order, amount: Decimal, user, *, overpay: bool = False) -> None:
    """Часть поступления ``document`` — оплатой ``order`` датой поступления."""
    paid_at = payment_moment(document.day, [order])
    payment = record_staff_payment(
        order, amount, user, method=document.method, note=_note(document.act_number, document.document),
        paid_at=paid_at, allow_overpayment=overpay, check_store_window=False,
    )
    if paid_at is not None:
        log_payment_backdated(payment, document.day, user)
    document.payments.add(payment)
    document.unapplied -= amount
    document.save(update_fields=["unapplied"])


def _unapplied(client, currency: str) -> list[ClientActDocument]:
    """Неразнесённые поступления клиента в валюте — от раннего к позднему."""
    return list(
        ClientActDocument.objects.select_for_update()
        .filter(client=client, currency=currency, kind="payment", unapplied__gt=0)
        .order_by("day", "pk")
    )


def _drain(client, currency: str, user, budget: _Budget) -> bool:
    """Разнести неразнесённые поступления по долгам клиента от старого к новому.

    Правила распределения — как у «Внести оплату»
    (:func:`orders.debt_payments.plan_debt_payment`), только без графика оплат
    магазина: акт — уже случившаяся история. Отгрузки позже поступления тоже
    гасятся: это предоплата. ``False`` — кончился бюджет запроса, следующий
    запрос продолжит.
    """
    for document in _unapplied(client, currency):
        plan = plan_debt_payment(
            debt_candidates(client.pk, currency), document.unapplied, today=document.day, check_store_window=False,
        )
        for share in plan["slices"]:
            if budget.spent:
                return False
            _pay_from(document, share["order"], share["amount"], user)
            budget.spend()
            budget.payments += 1
    return True


def _waiting(client, currency: str) -> Decimal:
    """Кредит актов, ещё не разнесённый по заказам (импорт не закончен)."""
    return sum(
        ClientActDocument.objects.filter(client=client, currency=currency, kind="payment")
        .values_list("unapplied", flat=True),
        ZERO,
    )


def _act_overpayments(client, currency: str) -> list:
    """Переплата из актов на отгрузках клиента: (заказ, оплата, сколько можно забрать).

    Только подтверждённые оплаты из актов и не больше переплаты заказа —
    возвращённое кассой клиенту не в счёт. Новые оплаты — первыми.
    """
    holders = (
        Order.objects.filter(
            client=client, currency=currency, status=DEBT_STATUS, payments__act_documents__isnull=False,
        )
        .distinct()
        .select_related("shipment")
        .prefetch_related("items", "payments__act_documents")
    )
    takeable = []
    for order in sorted(holders, key=oldest_debt_first, reverse=True):
        extra = order_overpaid(order)
        act_payments = sorted(
            (p for p in order.payments.all() if p.status == "confirmed" and p.act_documents.all()),
            key=lambda payment: payment.pk,
            reverse=True,
        )
        for payment in act_payments:
            cap = min(extra, payment.available_for_refund)
            if cap > 0:
                takeable.append((order, payment, cap))
                extra -= cap
    return takeable


def _pending_money(client, currency: str) -> Decimal:
    """Сколько денег актов импорт ещё разнесёт: ждущий кредит и переплата под новые долги."""
    waiting = _waiting(client, currency)
    free = sum((available_to_pay(order) for order in debt_candidates(client.pk, currency)), ZERO)
    reclaimable = sum((cap for _order, _payment, cap in _act_overpayments(client, currency)), ZERO)
    return waiting + max(ZERO, min(reclaimable, free - waiting))


def _reclaim_overpayments(client, currency: str, user, budget: _Budget) -> bool:
    """Переплату прошлых актов — обратно в неразнесённое, если её есть чем погасить.

    Итог акта в пользу клиента лежит переплатой последней отгрузки
    (:func:`_settle_leftover`). Следующий акт добавляет отгрузки — эти деньги
    гасят их датой своего поступления (:func:`_drain`), а не висят «К возврату»
    рядом с новым долгом. Уменьшается только оплата из акта и не больше
    переплаты заказа: возвращённое кассой клиенту не трогаем. Оплата, ушедшая
    целиком, удаляется — её деньги теперь в долях на другие заказы, касса по
    дням не меняется. Забирается не больше, чем этот запрос успеет разнести
    (``budget``), каждый перенос — событие журнала на заказе-держателе.
    ``False`` — переплату ещё есть чем погасить, но бюджет кончился: следующий
    запрос продолжит. Вызывать под блокировкой заказов клиента.
    """
    takeable = _act_overpayments(client, currency)
    available = sum((cap for _order, _payment, cap in takeable), ZERO)
    if available <= 0:
        return True
    waiting = _waiting(client, currency)
    # Окно — только долги, которые примут деньги: заказ с оплатой в работе
    # (счёт, QR) _drain пропускает, и места в бюджете он не займёт.
    frees = [free for free in (available_to_pay(order) for order in debt_candidates(client.pk, currency)) if free > 0]
    need_all = sum(frees, ZERO) - waiting
    if need_all <= 0:
        return True
    if budget.spent:
        return False
    need = min(need_all, sum(frees[: budget.left], ZERO) - waiting)
    reclaimed = ZERO
    touched = set()
    for order, payment, cap in takeable:
        share = min(need - reclaimed, cap)
        if share <= 0:
            break
        document = payment.act_documents.all()[0]
        left = payment.amount - share
        log_event(
            "client_act_reclaim",
            f"Переплата по акту сверки ушла на новые отгрузки: оплата №{payment.pk} "
            f"{money_text(payment.amount, currency)} → {money_text(left, currency)}",
            user=user,
            order=order,
            payload={
                "payment_id": payment.pk,
                "amount_before": money_string(payment.amount),
                "amount_after": money_string(left),
                "document_id": document.pk,
                "document": document.document,
            },
        )
        if left == 0:
            payment.delete()
        else:
            Payment.objects.filter(pk=payment.pk).update(amount=F("amount") - share)
        ClientActDocument.objects.filter(pk=document.pk).update(unapplied=F("unapplied") + share)
        reclaimed += share
        touched.add(order)
    for order in touched:
        sync_payment_status(order)
    budget.reclaimed += reclaimed
    return reclaimed >= min(need_all, available)


def _import_sale(client, act: ParsedAct, user, *, kind: str, document: str, day: date, amount: Decimal) -> None:
    product = _act_product()
    order = create_staff_order(
        user,
        {
            "client": client,
            "currency": act.currency,
            "department": _department(client, user),
            "notes": _note(act.number, document),
        },
        [{"product": product, "quantity": 1}],
        prices={str(product.pk): amount},
        backdate={"date": day, "status": "shipped"},
    )
    ClientActDocument.objects.create(
        client=client, currency=act.currency, kind=kind, document=document, day=day, amount=amount,
        act_number=act.number, order=order, imported_by=user,
    )


def _import_payment(client, act: ParsedAct, row: ActRow, user) -> None:
    """Поступление ждёт в реестре целиком — по долгам его разносит :func:`_drain`."""
    ClientActDocument.objects.create(
        client=client, currency=act.currency, kind="payment", document=row.document, day=row.day,
        amount=row.amount, method=row.method, unapplied=row.amount, act_number=act.number, imported_by=user,
    )


def _settle_leftover(client, currency: str, user) -> Decimal:
    """Долгов больше нет, а деньги остались — переплатой последней отгрузки клиента."""
    leftover = _unapplied(client, currency)
    if not leftover:
        return ZERO
    shipped = list(
        Order.objects.filter(client=client, currency=currency, status=DEBT_STATUS).select_related("shipment")
    )
    if not shipped:
        raise ValidationError({
            "detail": f"Переплату некуда записать: у клиента нет отгруженных заказов в {currency}",
            "code": "act_overpayment_without_order",
        })
    holder = max(shipped, key=oldest_debt_first)
    total = ZERO
    for document in leftover:
        total += document.unapplied
        _pay_from(document, holder, document.unapplied, user, overpay=True)
    return total


@transaction.atomic
def import_act_chunk(client, act: ParsedAct, user, *, start: date | None = None, budget: int = CHUNK_OPS) -> dict:
    """Следующая порция акта: не больше ``budget`` заказов и оплат за запрос.

    Сначала доразносятся деньги прошлой порции, потом строки по дням. Строки,
    уже перенесённые раньше (реестр), пропускаются — окно просто повторяет
    запрос, пока не придёт ``finished``. Последняя порция записывает остаток
    денег переплатой.
    """
    assert_can_import(user)
    lock_client_orders(client.pk)
    client = lock_scoped_client(client.pk, user)
    plan = _plan(client, act, start)
    if plan.problems:
        raise ValidationError({"detail": " ".join(plan.problems), "code": "act_import_blocked"})

    budget = _Budget(budget)
    opened = False
    _reclaim_overpayments(client, act.currency, user, budget)
    drained = _drain(client, act.currency, user, budget)
    if drained and plan.opening_order:
        _import_sale(
            client, act, user, kind="opening", document=_opening_document(act),
            day=act.period_start, amount=plan.opening_order,
        )
        budget.spend()
        opened = True
        drained = _drain(client, act.currency, user, budget)
    done = 0
    while drained and not budget.spent and done < len(plan.rows):
        row = plan.rows[done]
        if row.kind == "sale":
            _import_sale(client, act, user, kind="sale", document=row.document, day=row.day, amount=row.amount)
            budget.spend()
        else:
            _import_payment(client, act, row, user)
        done += 1
        drained = _drain(client, act.currency, user, budget)
    processed = done + opened
    remaining = len(plan.rows) - done + (bool(plan.opening_order) and not opened)
    if drained and remaining == 0:
        # Новые отгрузки этой порции — их тоже гасит переплата прошлых актов.
        reclaimed_all = _reclaim_overpayments(client, act.currency, user, budget)
        drained = _drain(client, act.currency, user, budget) and reclaimed_all
    finished = drained and remaining == 0
    overpaid = _settle_leftover(client, act.currency, user) if finished else ZERO
    if finished and (processed or budget.payments or budget.reclaimed or overpaid):
        log_event(
            "client_act_import",
            f"Импорт акта сверки № {act.number or '—'} клиента «{client.display_name}» "
            f"за {act.period_start:%d.%m.%Y}–{act.period_end:%d.%m.%Y} ({act.currency}) завершён"
            + (f", переплата {money_text(overpaid, act.currency)}" if overpaid else ""),
            user=user,
            payload={
                "client_id": client.pk,
                "act_number": act.number,
                "currency": act.currency,
                "period_start": act.period_start.isoformat(),
                "period_end": act.period_end.isoformat(),
                "start": plan.start.isoformat(),
                "overpaid": money_string(overpaid),
            },
        )
    return {
        "processed": processed,
        "payments": budget.payments,
        "remaining": remaining,
        "finished": finished,
        "overpaid": money_string(overpaid) if finished else None,
    }
