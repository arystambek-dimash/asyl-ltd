"""«Возврат» по клиенту: привезённые мешки раскладываются по его отгруженным заказам.

Позиция копит ``OrderItem.returned_quantity``: отгружено остаётся как было,
сумма и долг считаются за ``sold_quantity`` (``Order.total_amount``,
``querysets.item_value_sum``), в валюте каждого заказа — итоги по валютам не
складываются. Что с деньгами, выбирают в форме: ``debt``
уменьшает долг — только заказы со свободным остатком, переплаты нет;
``cash`` — касса отдаёт деньги: только оплаченные заказы, кассовые возвраты
их оплат. Порядок — от новой отгрузки к старой. Мешки приходят на выбранный
склад движением ``client_return``. Корзина не участвует.
"""

from collections import Counter, defaultdict
from decimal import Decimal

from django.db import transaction
from django.db.models import Exists, F, OuterRef, Prefetch
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.catalog.models import Product
from apps.clients.services import lock_client_orders, lock_scoped_client
from apps.common.money import as_money_strings, money_string, money_text, sum_by_currency
from apps.common.query_params import filter_date_range, parse_date_range, parse_search_param
from apps.common.text import plural_ru
from apps.eventlog.services import log_event
from apps.sales.access import scope_by_client_department
from apps.sales.labels import department_label
from apps.sales.models import Department
from apps.warehouse.services import resolve_warehouse, return_stock

from .debt import DEBT_STATUS, available_to_pay, oldest_debt_first, order_remaining
from .labels import bonus_mark
from .models import GoodsReturn, GoodsReturnLine, Order, OrderItem
from .querysets import filter_order_scope, filter_order_search, order_department
from .refunds import create_cash_refund
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


def _refund_cash(order, amount: Decimal, user, reason: str) -> list[int]:
    """Касса отдаёт ``amount`` кассовыми возвратами оплат заказа — сначала новой оплаты."""
    refund_ids = []
    left = amount
    for payment in sorted(order.payments.all(), key=lambda payment: payment.pk, reverse=True):
        share = min(left, payment.available_for_refund)
        if share <= 0:
            continue
        refund_ids.append(create_cash_refund(payment, user, amount=share, reason=reason).pk)
        left -= share
        if not left:
            break
    return refund_ids


def _record(client, user, plan: dict, lines, *, settlement: str, warehouse) -> GoodsReturn:
    goods_return = GoodsReturn.objects.create(
        client=client, settlement=settlement, warehouse=warehouse, created_by=user,
    )
    reason = f"Возврат товара №{goods_return.pk}"
    for share in plan["slices"]:
        order = share["order"]
        for item, bags in share["items"]:
            GoodsReturnLine.objects.create(
                goods_return=goods_return, order_item=item, bags=bags,
                unit_price=item.unit_price, amount=item.unit_price * bags,
            )
            OrderItem.objects.filter(pk=item.pk).update(returned_quantity=F("returned_quantity") + bags)
        refund_ids = _refund_cash(order, share["amount"], user, reason) if settlement == "cash" else []
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
    """«Возврат»: разложить мешки клиента по отгруженным заказам и провести.

    ``preview`` — только раскладка, без блокировок и записи. Проведение — под
    блокировкой заказов и клиента в области отдела сотрудника (порядок как у
    «Внести оплату»), раскладка пересчитывается под ней. Больше, чем
    помещается, не принимаем: «Максимум N мешков …».
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
        raise ValidationError({
            "detail": "; ".join(
                f"Максимум {_bags_text(fits)} «{product.plain_label}» {SETTLEMENT_TEXT[settlement]}"
                if fits
                else f"«{product.plain_label}»: {_NO_ROOM_TEXT[settlement]}"
                for product, fits in plan["short"].items()
            ),
            "code": "goods_return_exceeds",
        })
    payload = _payload(plan, settlement=settlement)
    if not preview:
        payload["return_id"] = _record(
            client, user, plan, parsed, settlement=settlement, warehouse=warehouse,
        ).pk
    return payload


# «Заказы → Возвраты»: список проведённых возвратов.
_ORDER = "order_item__order__"


def _visible_lines(user, params):
    """Строки возвратов, заказы которых сотрудник видит в списке «Заказов».

    Корзина = удалённое: путь ``order_item__order`` обходит ``LiveOrderManager``,
    поэтому живой заказ — явным фильтром. Область отдела и ``?department=`` —
    те же правила, что у списка заказов.
    """
    lines = GoodsReturnLine.objects.filter(**{f"{_ORDER}deleted_at__isnull": True})
    lines = scope_by_client_department(lines, user, client_path=f"{_ORDER}client")
    return filter_order_scope(lines, params, prefix=_ORDER)


def goods_returns_list(user, params):
    """Возвраты, у которых есть видимые строки, — новые сверху.

    Период ``date_from``/``date_to`` — по дню проведения возврата. Поиск — как
    в списке заказов (клиент, № заказа, номер машины) по заказам видимых строк;
    «#6055» — тоже номер заказа. У каждого возврата в ``visible_lines`` —
    только видимые строки, с отделом (``order_department``) и валютой заказа.
    """
    lines = _visible_lines(user, params)
    listed = lines
    search = parse_search_param(params.get("search")).removeprefix("#").strip()
    if search:
        listed = lines.filter(**{f"{_ORDER}in": filter_order_search(Order.objects.all(), search)})
    returns = GoodsReturn.objects.filter(Exists(listed.filter(goods_return=OuterRef("pk"))))
    returns = filter_date_range(returns, "created_at", *parse_date_range(params))
    shown = (
        lines.select_related("order_item__product")
        .annotate(department_code=order_department(_ORDER), currency=F(f"{_ORDER}currency"))
        .order_by(f"-{_ORDER}id", "id")
    )
    return (
        returns.select_related("client__user", "warehouse", "created_by")
        .prefetch_related(Prefetch("lines", queryset=shown, to_attr="visible_lines"))
        .order_by("-created_at", "-id")
    )


def goods_return_rows(returns) -> list[dict]:
    """Возвраты из :func:`goods_returns_list` в ответе API.

    Мешки и деньги — только по видимым строкам; деньги — в валюте заказа
    каждой строки, итог по валютам не складывается. Мука — без цвета.
    """
    departments = {row.code: row for row in Department.objects.all()}
    rows = []
    for goods_return in returns:
        lines = goods_return.visible_lines
        author = goods_return.created_by
        rows.append({
            "id": goods_return.pk,
            "created_at": timezone.localtime(goods_return.created_at).isoformat(),
            "client": goods_return.client_id,
            "client_name": goods_return.client.name,
            "settlement": goods_return.settlement,
            "settlement_label": goods_return.get_settlement_display(),
            "warehouse_name": goods_return.warehouse.name,
            "created_by_name": (author.get_full_name() or author.username) if author else None,
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
