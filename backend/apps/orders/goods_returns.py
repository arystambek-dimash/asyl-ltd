"""«Возврат» по клиенту: менеджер создаёт, кладовщик принимает мешки, принятые проводятся.

Менеджер указывает муку и мешки (:func:`record_goods_return`): раскладка по
заказам проверяется так же, как при проведении, но возврат только создаётся —
«Ждёт приёмки», долг, касса и склад не меняются. Кладовщик подтверждает каждую
муку (:func:`confirm_goods_return_item`; привезли меньше — вводит сколько) и
закрывает возврат (:func:`close_goods_return`): проводятся только принятые мешки.
Пока возврат ждёт приёмки, менеджер или кладовщик может его отменить
(:func:`cancel_goods_return`). Ошибку в закрытом возврате кладовщик исправляет
(:func:`reopen_goods_return`): всё, что сделало закрытие, отменяется, и возврат
снова ждёт приёмки с прежними числами.

Проведение раскладывает мешки по отгруженным заказам клиента. Позиция копит
``OrderItem.returned_quantity``: отгружено остаётся как было, сумма и долг
считаются за ``sold_quantity`` (``Order.total_amount``,
``querysets.item_value_sum``), в валюте каждого заказа — итоги по валютам не
складываются. Что с деньгами, выбирают в форме: ``debt`` уменьшает долг —
только заказы со свободным остатком, переплаты нет; ``cash`` — касса отдаёт
деньги: только оплаченные заказы, кассовые возвраты их оплат. Порядок — от
новой отгрузки к старой. Мешки приходят на выбранный склад движением
``client_return``. Корзина не участвует.
"""

from collections import Counter, defaultdict
from decimal import Decimal

from django.db import transaction
from django.db.models import Exists, F, OuterRef, Prefetch, Q
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.catalog.models import Product
from apps.clients.services import lock_client_orders, lock_scoped_client
from apps.common.money import as_money_strings, money_string, money_text, sum_by_currency
from apps.common.query_params import filter_date_range, parse_date_range, parse_search_param
from apps.common.text import plural_ru
from apps.eventlog.models import EventLog
from apps.eventlog.services import log_event
from apps.sales.access import scope_by_client_department
from apps.sales.labels import UNASSIGNED_CODE, department_label
from apps.sales.models import Department
from apps.warehouse.services import deduct_stock, resolve_warehouse, return_stock

from .debt import DEBT_STATUS, available_to_pay, oldest_debt_first, order_remaining
from .labels import bonus_mark
from .models import GoodsReturn, GoodsReturnItem, GoodsReturnLine, Order, OrderItem, PaymentRefund
from .querysets import client_search_q, filter_order_scope, filter_order_search, order_department
from .refunds import cancel_cash_refund, create_cash_refund
from .services import sync_payment_status

SETTLEMENT_TEXT = {"debt": "в счёт долга", "cash": "из кассы"}
# Почему мука не поместилась совсем: каких заказов с ней у клиента нет.
_NO_ROOM_TEXT = {"debt": "нет заказов в долге с этой мукой", "cash": "нет оплаченных заказов с этой мукой"}
_ZERO = Decimal("0")


def _bags_text(count: int) -> str:
    return f"{count} {plural_ru(count, 'мешок', 'мешка', 'мешков')}"


def assert_no_returns(order) -> None:
    """Заказ с возвратом не откатывают и не перекраивают: строки возврата держат его позиции."""
    if OrderItem.objects.filter(order=order, returned_quantity__gt=0).exists():
        raise ValidationError({
            "detail": "У заказа есть возврат товара — откат и правка состава недоступны",
            "code": "order_has_returns",
        })


def _parsed_lines(raw) -> list[tuple[Product, int]]:
    """Строки формы ``[{product, bags}]`` → ``[(товар, мешков)]``; одинаковые товары складываются."""
    if not isinstance(raw, list) or not raw:
        raise ValidationError({
            "detail": "Укажите, какую муку и сколько мешков вернули",
            "code": "goods_return_empty",
        })
    bags: Counter = Counter()
    for row in raw:
        product_id = row.get("product") if isinstance(row, dict) else None
        count = row.get("bags") if isinstance(row, dict) else None
        whole = [value for value in (product_id, count) if isinstance(value, int) and not isinstance(value, bool)]
        if len(whole) != 2 or count <= 0:
            raise ValidationError({
                "detail": "Мешки — целое число больше нуля",
                "code": "goods_return_bad_line",
            })
        bags[product_id] += count
    products = Product.objects.in_bulk(list(bags))
    if len(products) != len(bags):
        raise ValidationError({"detail": "Товар не найден", "code": "product_not_found"})
    return [(products[pk], count) for pk, count in bags.items()]


def _candidates(client_pk: int) -> list:
    """Отгруженные заказы клиента, от новой отгрузки к старой. Корзины нет: ``Order.objects``."""
    orders = (
        Order.objects.filter(client_id=client_pk, status=DEBT_STATUS)
        .select_related("shipment")
        .prefetch_related("payments", "items__product")
    )
    return sorted(orders, key=oldest_debt_first, reverse=True)


def _room(order, settlement: str) -> Decimal:
    """Сколько денег заказ примет возвратом: свободный долг или деньги к возврату у оплаченного."""
    if settlement == "debt":
        return available_to_pay(order)
    if order_remaining(order) > 0:
        return _ZERO
    return sum((payment.available_for_refund for payment in order.payments.all()), _ZERO)


def plan_goods_return(orders, lines, settlement: str) -> dict:
    """Разложить мешки по заказам — без записи.

    ``orders`` — :func:`_candidates` (с ``items`` и ``payments``), ``lines`` —
    ``[(товар, мешков)]``. Позиция отдаёт не больше ``sold_quantity``, заказ —
    не больше ``floor(запас / цена)`` мешков; запас денег заказа общий для всех
    строк. Бонусная позиция берёт остаток после платных — без денег и без
    запаса. ``short`` — ``{товар: сколько поместилось}`` для не поместившихся.
    """
    room = {order.pk: _room(order, settlement) for order in orders}
    taken: dict[int, list] = defaultdict(list)
    short = {}
    for product, bags in lines:
        left = bags
        # Сначала платные строки — они уменьшают долг или возвращают деньги;
        # бонусные (бесплатные) мешки принимают то, что не поместилось, без денег.
        for bonus in (False, True):
            for order in orders:
                for item in order.items.all():
                    if not left:
                        break
                    if item.product_id != product.pk or item.is_bonus != bonus:
                        continue
                    if bonus:
                        fit = min(left, item.sold_quantity)
                    elif item.unit_price and item.unit_price > 0:
                        fit = min(left, item.sold_quantity, int(room[order.pk] // item.unit_price))
                    else:
                        continue
                    if fit <= 0:
                        continue
                    taken[order.pk].append((item, fit))
                    room[order.pk] -= fit * item.unit_price
                    left -= fit
        if left:
            short[product] = bags - left
    slices = [
        {
            "order": order,
            "items": taken[order.pk],
            "amount": sum((item.unit_price * bags for item, bags in taken[order.pk]), _ZERO),
        }
        for order in orders
        if taken[order.pk]
    ]
    return {"slices": slices, "short": short}


def _validated_mode(settlement) -> None:
    if settlement not in SETTLEMENT_TEXT:
        raise ValidationError({"detail": "Выберите: в счёт долга или из кассы", "code": "bad_settlement"})


def returnable_products(client) -> list[dict]:
    """Мука, которую клиент может вернуть: из его отгруженных заказов.

    По каждому товару — сколько мешков поместится, если вернуть только его:
    ``debt_bags`` в счёт долга и ``cash_bags`` из кассы (та же раскладка, что
    при проведении). Товар, который не поместится ни так, ни так, не входит.
    """
    orders = _candidates(client.pk)
    shipped: dict[int, list] = {}
    for order in orders:
        for item in order.items.all():
            if item.product_id and (item.unit_price or item.is_bonus) and item.sold_quantity > 0:
                shipped.setdefault(item.product_id, []).append(item)
    rows = []
    for items in shipped.values():
        product = items[0].product
        line = [(product, sum(item.sold_quantity for item in items))]
        fits = {
            settlement: plan_goods_return(orders, line, settlement)["short"].get(product, line[0][1])
            for settlement in SETTLEMENT_TEXT
        }
        if any(fits.values()):
            rows.append({
                "product": product.pk,
                "label": items[0].product_plain_label,
                "debt_bags": fits["debt"],
                "cash_bags": fits["cash"],
            })
    return sorted(rows, key=lambda row: row["label"])


def _short_text(short: dict, settlement: str) -> str:
    """Что не помещается из раскладки: «Максимум N мешков …» или «нет заказов …» по каждой муке."""
    return "; ".join(
        f"Максимум {_bags_text(fits)} «{product.plain_label}» {SETTLEMENT_TEXT[settlement]}"
        if fits
        else f"«{product.plain_label}»: {_NO_ROOM_TEXT[settlement]}"
        for product, fits in short.items()
    )


def _items_snapshot(goods_return) -> list[dict]:
    """Мука возврата для журнала: сколько указал менеджер и сколько принято (``None`` — не проверена)."""
    return [
        {
            "item_id": item.pk,
            "product_id": item.product_id,
            "product_label": item.product_plain_label,
            "bags": item.bags,
            "accepted_bags": item.accepted_bags,
        }
        for item in goods_return.items.select_related("product").order_by("id")
    ]


def _log_status(goods_return, user, text: str, **payload) -> None:
    """Событие самого возврата: создан, закрыт кладовщиком, отменён, возвращён на приёмку.

    ``items`` — мука и принятые мешки на момент события: по событиям видно,
    что было и что стало после исправления. Деньги и мешки по заказам —
    события ``goods_return`` у заказов. У этого события заказа нет: владелец —
    клиент (``client_id``), как у «Внесения оплаты».
    """
    client = goods_return.client
    log_event(
        "goods_return_status",
        f"Возврат товара №{goods_return.pk} клиента «{client.display_name}» {text}",
        user=user,
        payload={
            "client_id": client.pk,
            "department": client.department.code if client.department_id else None,
            "goods_return_id": goods_return.pk,
            "status": goods_return.status,
            "settlement": goods_return.settlement,
            "items": _items_snapshot(goods_return),
            **payload,
        },
    )


def _paid_bags(plan: dict) -> Counter:
    """Сколько мешков каждой муки (``product_id``) раскладка положила на платные позиции — за деньги."""
    paid: Counter = Counter()
    for share in plan["slices"]:
        for item, bags in share["items"]:
            if not item.is_bonus:
                paid[item.product_id] += bags
    return paid


def _create(client, user, lines, *, settlement: str, warehouse, paid: Counter) -> GoodsReturn:
    """Возврат «Ждёт приёмки»: мука и мешки, как указал менеджер. Деньги и склад не трогаем.

    ``paid`` — :func:`_paid_bags` раскладки, которую увидел менеджер: при
    закрытии принятые мешки не уйдут в бонус, если за них показали деньги.
    """
    goods_return = GoodsReturn.objects.create(
        client=client, settlement=settlement, warehouse=warehouse, created_by=user,
    )
    GoodsReturnItem.objects.bulk_create([
        GoodsReturnItem(
            goods_return=goods_return, product=product, product_label_snapshot=product.plain_label,
            bags=bags, paid_bags=paid[product.pk],
        )
        for product, bags in lines
    ])
    bags = sum(count for _product, count in lines)
    _log_status(
        goods_return, user,
        f"создан: {_bags_text(bags)} {SETTLEMENT_TEXT[settlement]} — ждёт приёмки на складе «{warehouse.name}»",
        bags=bags,
    )
    return goods_return


def _payload(plan: dict, *, settlement: str) -> dict:
    """Раскладка в ответе API: деньги строками в валюте заказа, итог — по валютам, подписи без цвета."""
    amount_of = {share["order"].pk: share["amount"] for share in plan["slices"]}
    orders = [share["order"] for share in plan["slices"]]
    return {
        "settlement": settlement,
        "bags": sum(bags for share in plan["slices"] for _item, bags in share["items"]),
        "amounts": as_money_strings(sum_by_currency(orders, lambda order: amount_of[order.pk])),
        "orders": [
            {
                "order_id": share["order"].pk,
                "currency": share["order"].currency,
                "shipped_at": timezone.localtime(share["order"].sale_at).isoformat(),
                "amount": money_string(share["amount"]),
                "lines": [
                    {
                        "label": bonus_mark(item.product_plain_label, item.is_bonus),
                        "bags": bags,
                        "amount": money_string(item.unit_price * bags),
                    }
                    for item, bags in share["items"]
                ],
            }
            for share in plan["slices"]
        ],
    }


@transaction.atomic
def record_goods_return(
    client, user, *, settlement, warehouse, lines, preview: bool = False,
) -> dict:
    """«Возврат»: проверить раскладку мешков клиента по заказам и создать возврат на приёмку.

    ``preview`` — только раскладка, без блокировок и записи. Создание — под
    блокировкой заказов и клиента в области отдела сотрудника (порядок как у
    «Внести оплату»), раскладка проверяется под ней. Больше, чем помещается,
    не принимаем: «Максимум N мешков …». Созданный возврат ждёт кладовщика:
    долг, касса и склад меняются только при закрытии (:func:`close_goods_return`).
    """
    _validated_mode(settlement)
    if settlement == "cash" and not user.has_perm_code("payments.confirm"):
        raise PermissionDenied("Отдать деньги из кассы может тот, кто делает возврат оплаты")
    parsed = _parsed_lines(lines)
    warehouse = resolve_warehouse(warehouse or None)
    if not preview:
        lock_client_orders(client.pk)
        client = lock_scoped_client(client.pk, user)
    plan = plan_goods_return(_candidates(client.pk), parsed, settlement)
    if plan["short"]:
        raise ValidationError({"detail": _short_text(plan["short"], settlement), "code": "goods_return_exceeds"})
    payload = _payload(plan, settlement=settlement)
    if not preview:
        goods_return = _create(
            client, user, parsed, settlement=settlement, warehouse=warehouse, paid=_paid_bags(plan),
        )
        payload.update(return_id=goods_return.pk, status=goods_return.status)
    return payload


def _locked(goods_return) -> GoodsReturn:
    """Возврат под блокировкой строки: статус читается под той же блокировкой, что и пишется."""
    locked = GoodsReturn.objects.select_for_update().filter(pk=goods_return.pk).first()
    if locked is None:
        raise NotFound("Возврат не найден")
    return locked


def _locked_pending(goods_return) -> GoodsReturn:
    """Возврат под блокировкой — только пока он ждёт приёмки.

    Повторное закрытие, отмена закрытого или правка муки после закрытия — ошибка.
    """
    locked = _locked(goods_return)
    if locked.status != "pending":
        raise ValidationError({
            "detail": f"Возврат №{locked.pk} уже не ждёт приёмки: «{locked.get_status_display()}»",
            "code": "goods_return_not_pending",
        })
    return locked


def _finish(goods_return, user, status: str, *, by_storekeeper: bool) -> None:
    """Вывести возврат из «Ждёт приёмки»: кто и когда закрыл или отменил, кладовщик ли это."""
    goods_return.status = status
    goods_return.accepted_by = user
    goods_return.accepted_at = timezone.now()
    goods_return.closed_by_storekeeper = by_storekeeper
    goods_return.save(update_fields=["status", "accepted_by", "accepted_at", "closed_by_storekeeper"])


@transaction.atomic
def confirm_goods_return_item(goods_return, item_id, accepted_bags) -> GoodsReturn:
    """Кладовщик проверил муку: сколько мешков принято — от 0 до указанного менеджером.

    До закрытия возврата число можно поменять. Долг, касса и склад не меняются.
    """
    goods_return = _locked_pending(goods_return)
    item = goods_return.items.filter(pk=item_id).first()
    if item is None:
        raise NotFound("В возврате нет такой муки")
    whole = isinstance(accepted_bags, int) and not isinstance(accepted_bags, bool)
    if not whole or not 0 <= accepted_bags <= item.bags:
        raise ValidationError({
            "detail": f"Принято — целое число мешков от 0 до {item.bags}",
            "code": "goods_return_bad_count",
        })
    item.accepted_bags = accepted_bags
    item.checked_at = timezone.now()
    item.save(update_fields=["accepted_bags", "checked_at"])
    return goods_return


def _no_longer_fits(reason: str) -> ValidationError:
    return ValidationError({
        "detail": f"Возврат больше не проводится: {reason}. Менеджер должен отменить его и создать новый",
        "code": "goods_return_no_longer_fits",
    })


def _refund_cash(goods_return, order, amount: Decimal, reason: str) -> list[int]:
    """Касса отдаёт ``amount`` кассовыми возвратами оплат заказа — сначала новой оплаты.

    Отдел клиента уже проверен у закрывающего кладовщика под блокировкой
    клиента; автор возврата денег — создавший возврат: его отдел или отдел
    клиента могли смениться после создания. Возвраты оплат привязаны к
    возврату товара — его исправление отменит ровно их.
    """
    refund_ids = []
    left = amount
    for payment in sorted(order.payments.all(), key=lambda payment: payment.pk, reverse=True):
        share = min(left, payment.available_for_refund)
        if share <= 0:
            continue
        refund = create_cash_refund(
            payment, goods_return.created_by, amount=share, reason=reason, any_department=True,
            goods_return=goods_return,
        )
        refund_ids.append(refund.pk)
        left -= share
        if not left:
            break
    return refund_ids


def _record(goods_return, client, user, items) -> None:
    """Провести принятую муку ``items`` (:class:`GoodsReturnItem`): строки по заказам, долг или касса, склад.

    Раскладка — заново, под блокировками закрытия: после создания возврата
    клиент мог заплатить, заказ — уехать в корзину. Не помещается — ничего не
    пишем. Не помещается и когда принятые мешки, за которые менеджеру показали
    деньги, ушли бы в бонус без денег. Кассовые возвраты оплат — от имени
    создавшего возврат (это он вправе отдать деньги из кассы), события и склад —
    от кладовщика.
    """
    settlement = goods_return.settlement
    lines = [(item.product, item.accepted_bags) for item in items]
    plan = plan_goods_return(_candidates(client.pk), lines, settlement)
    changed = "долг или оплаты клиента изменились после создания возврата"
    if plan["short"]:
        raise _no_longer_fits(f"{_short_text(plan['short'], settlement)} — {changed}")
    paid = _paid_bags(plan)
    # Платные позиции заполняются первыми: из принятых за деньги ждём столько же, сколько показали.
    unpaid = []
    for item in items:
        expected = min(item.accepted_bags, item.paid_bags)
        if paid[item.product_id] < expected:
            unpaid.append(
                f"за «{item.product_plain_label}» {SETTLEMENT_TEXT[settlement]} засчитывается"
                f" {paid[item.product_id]} из {expected} {plural_ru(expected, 'мешка', 'мешков', 'мешков')}"
            )
    if unpaid:
        raise _no_longer_fits(f"{'; '.join(unpaid)} — {changed}")
    warehouse = resolve_warehouse(goods_return.warehouse_id)
    reason = f"Возврат товара №{goods_return.pk}"
    for share in plan["slices"]:
        order = share["order"]
        for item, bags in share["items"]:
            GoodsReturnLine.objects.create(
                goods_return=goods_return, order_item=item, bags=bags,
                unit_price=item.unit_price, amount=item.unit_price * bags,
            )
            OrderItem.objects.filter(pk=item.pk).update(returned_quantity=F("returned_quantity") + bags)
        refund_ids = _refund_cash(goods_return, order, share["amount"], reason) if settlement == "cash" else []
        sync_payment_status(Order.objects.get(pk=order.pk))
        bags = sum(count for _item, count in share["items"])
        log_event(
            "goods_return",
            f"{reason}: {_bags_text(bags)} · {money_text(share['amount'], order.currency)} {SETTLEMENT_TEXT[settlement]}",
            user=user,
            order=order,
            payload={
                "goods_return_id": goods_return.pk,
                "settlement": settlement,
                "bags": bags,
                "amount": money_string(share["amount"]),
                "lines": [
                    {
                        "order_item_id": item.pk,
                        "product_id": item.product_id,
                        "bags": count,
                        "amount": money_string(item.unit_price * count),
                    }
                    for item, count in share["items"]
                ],
                "refund_ids": refund_ids,
            },
        )
    for product, bags in lines:
        return_stock(product, bags, user, warehouse, note=f"{reason}, клиент «{client.display_name}»")


@transaction.atomic
def close_goods_return(goods_return, user) -> GoodsReturn:
    """Кладовщик закрывает возврат: проводятся только принятые мешки.

    Блокировки — как при создании (заказы → клиент), затем сам возврат. Каждая
    мука должна быть проверена. Всё принято — «Полностью возвращено»; ничего —
    «Отменён», без последствий; иначе «Частично возвращено» — деньги и склад
    только за принятые мешки.
    """
    lock_client_orders(goods_return.client_id)
    client = lock_scoped_client(goods_return.client_id, user)
    goods_return = _locked_pending(goods_return)
    items = list(goods_return.items.select_related("product").order_by("id"))
    if any(item.accepted_bags is None for item in items):
        raise ValidationError({
            "detail": "Подтвердите каждую муку — сколько мешков принято",
            "code": "items_unchecked",
        })
    accepted = [item for item in items if item.accepted_bags]
    gone = [item.product_plain_label for item in accepted if item.product_id is None]
    if gone:
        raise _no_longer_fits(", ".join(f"«{label}» удалена из каталога" for label in gone))
    if accepted:
        _record(goods_return, client, user, accepted)
    requested = sum(item.bags for item in items)
    taken = sum(item.accepted_bags for item in accepted)
    _finish(
        goods_return, user, "cancelled" if not taken else "full" if taken == requested else "partial",
        by_storekeeper=True,
    )
    _log_status(
        goods_return, user,
        # «из N» — родительный падеж: «из 1 мешка», «из 4 мешков».
        f"закрыт: принято {taken} из {requested} {plural_ru(requested, 'мешка', 'мешков', 'мешков')}"
        f" — «{goods_return.get_status_display()}»",
        bags=requested,
        accepted_bags=taken,
    )
    return goods_return


@transaction.atomic
def cancel_goods_return(goods_return, user, *, by_storekeeper: bool = False) -> GoodsReturn:
    """Менеджер или кладовщик (``by_storekeeper``) отменяет возврат, который ждёт приёмки.

    Ничего не проводилось — откатывать нечего: и у нового возврата, и у
    возвращённого на приёмку (:func:`reopen_goods_return` уже всё откатил).
    Свою отмену кладовщик может исправить, отмену менеджера — нет.
    """
    goods_return = _locked_pending(goods_return)
    _finish(goods_return, user, "cancelled", by_storekeeper=by_storekeeper)
    _log_status(goods_return, user, "отменён до приёмки")
    return goods_return


def _locked_closed(goods_return) -> GoodsReturn:
    """Возврат под блокировкой — только закрытый: второе «Исправить» видит «Ждёт приёмки»."""
    locked = _locked(goods_return)
    if locked.status not in GoodsReturn.CLOSED_STATUSES:
        raise ValidationError({
            "detail": f"Возврат №{locked.pk} уже ждёт приёмки — исправьте числа и закройте его",
            "code": "goods_return_not_closed",
        })
    return locked


def _cannot_reopen(goods_return, reason: str) -> ValidationError:
    return ValidationError({
        "detail": f"Возврат №{goods_return.pk} нельзя вернуть на приёмку: {reason}",
        "code": "goods_return_cannot_reopen",
    })


def _orders_text(order_ids: list[int], forms=("заказ", "заказы")) -> str:
    """«заказ #5» или «заказы #5, #6»; ``forms`` — другой падеж: («заказу», «заказам»)."""
    numbers = ", ".join(f"#{pk}" for pk in order_ids)
    return f"{forms[len(order_ids) > 1]} {numbers}"


def _refuse_trashed(goods_return, orders: dict) -> None:
    """Заказ возврата в корзине — не трогаем. Восстановить его может менеджер, удалённый навсегда — никто."""
    purged = sorted(pk for pk, order in orders.items() if order.purged_at)
    if purged:
        gone = "удалены" if len(purged) > 1 else "удалён"
        raise _cannot_reopen(goods_return, f"{_orders_text(purged)} {gone} навсегда")
    trashed = sorted(pk for pk, order in orders.items() if order.deleted_at)
    if trashed:
        them = "их" if len(trashed) > 1 else "его"
        raise _cannot_reopen(
            goods_return,
            f"{_orders_text(trashed)} в корзине — попросите менеджера восстановить {them},"
            " потом нажмите «Исправить» снова",
        )


def _close_refunds(goods_return, by_order: dict) -> tuple[dict[int, list], list[int]]:
    """Кассовые возвраты оплат, которые отдало закрытие, по заказам — ровно на сумму строк заказа.

    Обычно их держит связь ``PaymentRefund.goods_return``. Закрытие старым
    релизом (откат деплоя после миграции связи) её не ставит — недостающие
    берутся из ``refund_ids`` событий закрытия ``goods_return`` у заказа (без
    ``action``: их пишет каждая версия). Не сходится и так — исправлять нельзя:
    деньги, которые касса уже отдала, не должны молча стать новым долгом.
    Второе значение — найденные по событиям, им ставится связь.
    """
    cash = goods_return.settlement == "cash"
    expected = {
        order_id: sum((line.amount for line in lines), _ZERO) if cash else _ZERO
        for order_id, lines in by_order.items()
    }
    completed = (
        PaymentRefund.objects.filter(method="cash", status="completed")
        .select_related("payment")
        .order_by("payment__order_id", "payment_id", "pk")
    )
    refunds: dict[int, list] = defaultdict(list)
    for refund in completed.filter(goods_return=goods_return):
        refunds[refund.payment.order_id].append(refund)

    def off() -> list[int]:
        return sorted(
            order_id
            for order_id in set(expected) | set(refunds)
            if sum((refund.amount for refund in refunds[order_id]), _ZERO) != expected.get(order_id, _ZERO)
        )

    unlinked = []
    missing = off()
    if missing:
        closes = EventLog.objects.filter(
            event_type="goods_return", order_id__in=missing, payload__goods_return_id=goods_return.pk,
        ).exclude(payload__has_key="action")
        ids = {pk for payload in closes.values_list("payload", flat=True) for pk in payload.get("refund_ids") or []}
        for refund in completed.filter(pk__in=ids, goods_return__isnull=True, payment__order_id__in=missing):
            refunds[refund.payment.order_id].append(refund)
            unlinked.append(refund.pk)
    wrong = off()
    if wrong:
        raise _cannot_reopen(
            goods_return,
            f"выплаты из кассы по {_orders_text(wrong, ('заказу', 'заказам'))} не сходятся с возвратом"
            " — сообщите руководителю",
        )
    return refunds, unlinked


def _unrecord(goods_return, client, user, items) -> None:
    """Отменить проведение :func:`_record` — всё возвращается туда, откуда взято.

    Позиции заказов отдают мешки возврата (``returned_quantity``), строки по
    заказам удаляются: «В счёт долга» — долг возвращается; «Из кассы» —
    кассовые возвраты оплат, которые отдало закрытие (:func:`_close_refunds`),
    отменяются, и деньги снова считаются в оплате, а не становятся новым
    долгом. Чужие возвраты оплат не трогаем. Принятые мешки уходят со склада,
    куда пришли, — даже в минус, если их уже отгрузили. Заказ в корзине не
    трогаем: сначала его восстанавливают. Проведённое не сходится с принятым —
    ничего не пишем.
    """
    settlement = goods_return.settlement
    # «Отменён» ничего не проводил — даже если до отмены муку успели проверить.
    recorded = goods_return.status != "cancelled"
    accepted = Counter({item.product_id: item.accepted_bags for item in items if recorded and item.accepted_bags})
    lines = list(goods_return.lines.select_related("order_item").order_by("id"))
    on_lines: Counter = Counter()
    by_order: dict[int, list] = defaultdict(list)
    for line in lines:
        on_lines[line.order_item.product_id] += line.bags
        by_order[line.order_item.order_id].append(line)
    if None in accepted or accepted != on_lines:
        raise _cannot_reopen(goods_return, "проведённые мешки не совпадают с принятыми или мука удалена из каталога")
    order_ids = sorted(by_order)
    orders = Order.all_objects.in_bulk(order_ids)
    _refuse_trashed(goods_return, orders)
    refunds, unlinked = _close_refunds(goods_return, by_order)
    reason = f"Исправление возврата товара №{goods_return.pk}"
    for order_id in order_ids:
        order = orders[order_id]
        order_lines = by_order[order_id]
        for line in order_lines:
            taken_back = OrderItem.objects.filter(pk=line.order_item_id, returned_quantity__gte=line.bags).update(
                returned_quantity=F("returned_quantity") - line.bags,
            )
            if not taken_back:
                raise _cannot_reopen(goods_return, f"у заказа #{order_id} возвращено меньше мешков, чем в возврате")
        cancelled = [
            cancel_cash_refund(refund.pk, user, reason=reason, any_department=True).pk for refund in refunds[order_id]
        ]
        sync_payment_status(Order.objects.get(pk=order_id))
        bags = sum(line.bags for line in order_lines)
        amount = sum((line.amount for line in order_lines), _ZERO)
        log_event(
            "goods_return",
            f"{reason}: отменено {_bags_text(bags)} · {money_text(amount, order.currency)}"
            f" {SETTLEMENT_TEXT[settlement]} — возврат снова ждёт приёмки",
            user=user,
            order=order,
            payload={
                "goods_return_id": goods_return.pk,
                "action": "reopen",
                "settlement": settlement,
                "bags": bags,
                "amount": money_string(amount),
                "lines": [
                    {
                        "order_item_id": line.order_item_id,
                        "product_id": line.order_item.product_id,
                        "bags": line.bags,
                        "amount": money_string(line.amount),
                    }
                    for line in order_lines
                ],
                "cancelled_refund_ids": cancelled,
            },
        )
    # Под блокировками отмены выше: выплаты, найденные по событиям, — тоже этого возврата.
    PaymentRefund.objects.filter(pk__in=unlinked).update(goods_return=goods_return)
    goods_return.lines.all().delete()
    warehouse = resolve_warehouse(goods_return.warehouse_id, require_active=False)
    products = {item.product_id: item.product for item in items}
    for product_id in sorted(accepted):
        deduct_stock(
            products[product_id], accepted[product_id], user, warehouse, require_active=False,
            note=f"{reason}, клиент «{client.display_name}»", reason="client_return_undo",
        )


@transaction.atomic
def reopen_goods_return(goods_return, user) -> GoodsReturn:
    """«Исправить» закрытый возврат: он снова «Ждёт приёмки» с прежними числами.

    Блокировки — как у закрытия: заказы клиента → клиент в области отдела
    кладовщика → сам возврат; статус читается под блокировкой строки, поэтому
    второе «Исправить» или исправление во время закрытия — ошибка. Всё, что
    сделало закрытие, отменяется (:func:`_unrecord`). Мука сохраняет принятые
    числа и время проверки: кладовщик меняет только ошибочное и закрывает
    снова — итог тот же, что у одного закрытия с новыми числами, — или
    отменяет возврат. Отмену менеджера кладовщик не исправляет: менеджер решил,
    что возврата не будет, — в том числе денег из кассы. Срока нет; в журнале —
    кто, когда, что было и что стало.
    """
    lock_client_orders(goods_return.client_id)
    client = lock_scoped_client(goods_return.client_id, user)
    goods_return = _locked_closed(goods_return)
    if goods_return.status == "cancelled" and not goods_return.closed_by_storekeeper:
        raise _cannot_reopen(goods_return, "его отменил менеджер — если возврат всё же нужен, менеджер создаст новый")
    items = list(goods_return.items.select_related("product").order_by("id"))
    before = {
        "status": goods_return.status,
        "accepted_by": _person(goods_return.accepted_by),
        "accepted_at": _local_iso(goods_return.accepted_at),
    }
    was = goods_return.get_status_display()
    _unrecord(goods_return, client, user, items)
    goods_return.status = "pending"
    goods_return.accepted_by = None
    goods_return.accepted_at = None
    goods_return.closed_by_storekeeper = False
    goods_return.save(update_fields=["status", "accepted_by", "accepted_at", "closed_by_storekeeper"])
    requested = sum(item.bags for item in items)
    taken = sum(item.accepted_bags or 0 for item in items)
    _log_status(
        goods_return, user,
        f"возвращён на приёмку для исправления: был «{was}», принято {taken} из {requested}"
        f" {plural_ru(requested, 'мешка', 'мешков', 'мешков')}",
        action="reopen",
        before=before,
        after={"status": goods_return.status},
        bags=requested,
        accepted_bags=taken,
    )
    return goods_return


# Списки: «Заказы → Возвраты» и страница «Кладовщик».
_ORDER = "order_item__order__"
# Строк по заказам нет: возврат ждёт приёмки или отменён.
_UNRECORDED = ("pending", "cancelled")


def visible_goods_returns(user):
    """Возвраты клиентов отдела сотрудника — для кладовщика и действий над одним возвратом."""
    return scope_by_client_department(GoodsReturn.objects.all(), user, client_path="client")


def with_goods_return_relations(returns):
    """Всё, что читают строки списков, — запросом на страницу, а не на возврат."""
    items = GoodsReturnItem.objects.select_related("product").order_by("id")
    return returns.select_related("client__user", "warehouse", "created_by", "accepted_by").prefetch_related(
        Prefetch("items", queryset=items),
    )


def _visible_lines(user, params):
    """Строки возвратов, заказы которых сотрудник видит в списке «Заказов».

    Корзина = удалённое: путь ``order_item__order`` обходит ``LiveOrderManager``,
    поэтому живой заказ — явным фильтром. Область отдела и ``?department=`` —
    те же правила, что у списка заказов.
    """
    lines = GoodsReturnLine.objects.filter(**{f"{_ORDER}deleted_at__isnull": True})
    lines = scope_by_client_department(lines, user, client_path=f"{_ORDER}client")
    return filter_order_scope(lines, params, prefix=_ORDER)


def _unrecorded_returns(user, params):
    """Возвраты без строк по заказам: область и ``?department=`` — по отделу клиента."""
    returns = visible_goods_returns(user).filter(status__in=_UNRECORDED)
    department = params.get("department")
    if department:
        returns = returns.filter(
            Q(client__department__isnull=True)
            if department == UNASSIGNED_CODE
            else Q(client__department__code=department)
        )
    return returns


def goods_returns_list(user, params):
    """Возвраты, новые сверху.

    Проведённый возврат виден, если у него есть видимые строки; в
    ``visible_lines`` — только они, с отделом (``order_department``) и валютой
    заказа. Возврат, который ждёт приёмки или отменён, строк не имеет — он
    виден в области отдела клиента. Период ``date_from``/``date_to`` — по дню
    создания возврата. Поиск — как в списке заказов (клиент, № заказа, номер
    машины) по заказам видимых строк, у возврата без строк — по клиенту;
    «#6055» — тоже номер заказа.
    """
    lines = _visible_lines(user, params)
    listed = lines
    unrecorded = _unrecorded_returns(user, params)
    search = parse_search_param(params.get("search")).removeprefix("#").strip()
    if search:
        listed = lines.filter(**{f"{_ORDER}in": filter_order_search(Order.objects.all(), search)})
        unrecorded = unrecorded.filter(client_search_q(search))
    returns = GoodsReturn.objects.filter(
        Exists(listed.filter(goods_return=OuterRef("pk"))) | Q(pk__in=unrecorded.values("pk")),
    )
    returns = filter_date_range(returns, "created_at", *parse_date_range(params))
    shown = (
        lines.select_related("order_item__product")
        .annotate(department_code=order_department(_ORDER), currency=F(f"{_ORDER}currency"))
        .order_by(f"-{_ORDER}id", "id")
    )
    return (
        with_goods_return_relations(returns)
        .prefetch_related(Prefetch("lines", queryset=shown, to_attr="visible_lines"))
        .order_by("-created_at", "-id")
    )


def storekeeper_returns(user, params):
    """«Кладовщик»: ``?state=pending`` — ждут приёмки, старые сверху; ``closed`` — история.

    История — закрытые и отменённые, последние закрытые сверху. Область —
    отдел клиента, как у грузчика. Поиск — клиент (имя, ТОО, телефон) или № возврата.
    """
    state = params.get("state") or "pending"
    if state not in ("pending", "closed"):
        raise ValidationError({"detail": "Вкладка — pending или closed", "code": "bad_state"})
    returns = visible_goods_returns(user)
    if state == "pending":
        returns = returns.filter(status="pending").order_by("created_at", "id")
    else:
        returns = returns.filter(status__in=GoodsReturn.CLOSED_STATUSES).order_by(
            F("accepted_at").desc(nulls_last=True), "-id",
        )
    search = parse_search_param(params.get("search")).removeprefix("#").strip()
    if search:
        match = client_search_q(search)
        # № возврата — точно; длинные цифры — это телефон, а не номер.
        if search.isascii() and search.isdigit() and len(search) <= 9:
            match |= Q(pk=int(search))
        returns = returns.filter(match)
    return with_goods_return_relations(returns)


def _person(user) -> str | None:
    return (user.get_full_name() or user.username) if user else None


def _local_iso(moment) -> str | None:
    return timezone.localtime(moment).isoformat() if moment else None


def _common_row(goods_return) -> dict:
    """Поля возврата, общие для «Заказов» и кладовщика: кто, когда, статус и мука — без денег.

    ``items[].accepted_bags`` — ``None``, пока кладовщик муку не проверил.
    """
    return {
        "id": goods_return.pk,
        "created_at": _local_iso(goods_return.created_at),
        "client_name": goods_return.client.name,
        "warehouse_name": goods_return.warehouse.name,
        "created_by_name": _person(goods_return.created_by),
        "status": goods_return.status,
        "status_label": goods_return.get_status_display(),
        "accepted_by_name": _person(goods_return.accepted_by),
        "accepted_at": _local_iso(goods_return.accepted_at),
        "items": [
            {
                "id": item.pk,
                "product_label": item.product_plain_label,
                "bags": item.bags,
                "accepted_bags": item.accepted_bags,
            }
            for item in goods_return.items.all()
        ],
    }


def goods_return_rows(returns) -> list[dict]:
    """Возвраты из :func:`goods_returns_list` в ответе API.

    Мешки и деньги — только по видимым строкам; деньги — в валюте заказа
    каждой строки, итог по валютам не складывается. Мука — без цвета. У
    возврата без строк (ждёт приёмки, отменён) — ``lines: []``, ``amounts: {}``.
    """
    departments = {row.code: row for row in Department.objects.all()}
    rows = []
    for goods_return in returns:
        lines = goods_return.visible_lines
        rows.append({
            **_common_row(goods_return),
            "client": goods_return.client_id,
            "settlement": goods_return.settlement,
            "settlement_label": goods_return.get_settlement_display(),
            "bags": sum(line.bags for line in lines),
            "amounts": as_money_strings(sum_by_currency(lines, lambda line: line.amount)),
            "lines": [
                {
                    "order": line.order_item.order_id,
                    "order_department": line.department_code,
                    "order_department_name": department_label(
                        line.department_code, departments.get(line.department_code),
                    )[0],
                    "product_label": bonus_mark(line.order_item.product_plain_label, line.order_item.is_bonus),
                    "bags": line.bags,
                    "unit_price": money_string(line.unit_price),
                    "amount": money_string(line.amount),
                    "currency": line.currency,
                }
                for line in lines
            ],
        })
    return rows


def storekeeper_rows(returns) -> list[dict]:
    """Возвраты для кладовщика: мука и мешки, денег нет вовсе.

    ``bags`` — сколько указал менеджер, ``accepted_bags`` — сумма принятого по
    проверенной муке (``None``, пока не проверена ни одна). ``can_reopen`` —
    закрытый возврат можно «Исправить» (:func:`reopen_goods_return`): отмену
    менеджера — нет.
    """
    rows = []
    for goods_return in returns:
        row = _common_row(goods_return)
        checked = [item["accepted_bags"] for item in row["items"] if item["accepted_bags"] is not None]
        row["bags"] = sum(item["bags"] for item in row["items"])
        row["accepted_bags"] = sum(checked) if checked else None
        row["can_reopen"] = goods_return.status in GoodsReturn.CLOSED_STATUSES and (
            goods_return.status != "cancelled" or goods_return.closed_by_storekeeper
        )
        rows.append(row)
    return rows
