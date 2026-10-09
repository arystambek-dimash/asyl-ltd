"""«Возврат» по клиенту: менеджер создаёт заявку, кладовщик принимает мешки на склад.

Возврат не связан с заказами и деньги не трогает. Менеджер выбирает клиента,
склад и любую муку каталога с мешками (:func:`create_goods_return`) — возврат
«Ждёт приёмки». Кладовщик подтверждает каждую муку
(:func:`confirm_goods_return_item`; привезли меньше — вводит сколько) и
закрывает возврат (:func:`close_goods_return`): принятые мешки приходят на склад
возврата движением ``client_return``. Долг, оплаты, касса и заказы не меняются.
Пока возврат ждёт приёмки, менеджер или кладовщик может его отменить
(:func:`cancel_goods_return`). Ошибку в закрытом возврате кладовщик исправляет
(:func:`reopen_goods_return`): принятые мешки уходят со склада
(``client_return_undo``), возврат снова ждёт приёмки с прежними числами.

Возвраты, принятые по старым правилам (``settlement`` — «В счёт долга» или «Из
кассы»), разложили мешки по отгруженным заказам: строки :class:`GoodsReturnLine`,
``OrderItem.returned_quantity``, долг или кассовые возвраты оплат. Они остаются
в истории как были и не исправляются. Старый возврат, который ещё ждёт приёмки,
закрывается уже по новым правилам — только склад.
"""

from collections import Counter

from django.db import transaction
from django.db.models import Exists, F, OuterRef, Prefetch, Q
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from apps.catalog.models import Product
from apps.clients.services import lock_scoped_client
from apps.common.money import as_money_strings, money_string, sum_by_currency
from apps.common.query_params import filter_date_range, parse_date_range, parse_search_param
from apps.common.text import plural_ru
from apps.eventlog.services import log_event
from apps.sales.access import scope_by_client_department
from apps.sales.labels import UNASSIGNED_CODE, department_label
from apps.sales.models import Department
from apps.warehouse.services import deduct_stock, resolve_warehouse, return_stock

from .labels import bonus_mark
from .models import GoodsReturn, GoodsReturnItem, GoodsReturnLine, Order, OrderItem
from .querysets import client_search_q, filter_order_scope, filter_order_search, order_department

# Мешков в строке — не больше, чем вмещает колонка (как у количества позиции заказа).
_MAX_BAGS = 2_147_483_647


def _bags_text(count: int) -> str:
    return f"{count} {plural_ru(count, 'мешок', 'мешка', 'мешков')}"


def assert_no_returns(order) -> None:
    """Заказ с возвратом по старым правилам не откатывают и не перекраивают: строки возврата держат его позиции."""
    if OrderItem.objects.filter(order=order, returned_quantity__gt=0).exists():
        raise ValidationError({
            "detail": "У заказа есть возврат товара — откат и правка состава недоступны",
            "code": "order_has_returns",
        })


# Принят по старым правилам: мешки легли строками на заказы, деньги двигались.
_LEGACY_STATUSES = ("full", "partial")
_LEGACY = Q(status__in=_LEGACY_STATUSES) & ~Q(settlement="")


def _is_legacy(goods_return) -> bool:
    """Принят по старым правилам (:data:`_LEGACY`) — исправлять нельзя, в истории остаётся как был.

    Новые возвраты ``settlement`` не пишут. Старый, который ещё ждёт приёмки,
    закрывается и отменяется уже по новым правилам (:func:`_finish` очищает
    ``settlement``); отменённый старый ничего не провёл — он как новый.
    """
    return bool(goods_return.settlement) and goods_return.status in _LEGACY_STATUSES


def _parsed_lines(raw) -> list[tuple[Product, int]]:
    """Строки формы ``[{product, bags}]`` → ``[(товар, мешков)]``; одинаковые товары складываются.

    Мука — любой товар каталога, кроме архивного.
    """
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
    if any(count > _MAX_BAGS for count in bags.values()):
        raise ValidationError({"detail": "Слишком много мешков — проверьте число", "code": "goods_return_bad_line"})
    products = Product.objects.in_bulk(list(bags))
    if len(products) != len(bags):
        raise ValidationError({"detail": "Товар не найден", "code": "product_not_found"})
    archived = [product.plain_label for product in products.values() if not product.is_active]
    if archived:
        raise ValidationError({
            "detail": f"«{archived[0]}» в архиве — выберите другой товар",
            "code": "product_archived",
        })
    return [(products[pk], count) for pk, count in bags.items()]


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
    """Событие возврата: создан, закрыт кладовщиком, отменён, возвращён на приёмку.

    ``items`` — мука и принятые мешки на момент события: по событиям видно,
    что было и что стало после исправления. Заказа у события нет: владелец —
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
            "items": _items_snapshot(goods_return),
            **payload,
        },
    )


@transaction.atomic
def create_goods_return(client, user, *, warehouse, lines) -> GoodsReturn:
    """«Возврат»: заявка «Ждёт приёмки» — мука и мешки, как указал менеджер.

    Клиент — под блокировкой в области отдела сотрудника. Склад пополнит
    закрытие кладовщиком (:func:`close_goods_return`); деньги возврат не трогает.
    """
    parsed = _parsed_lines(lines)
    warehouse = resolve_warehouse(warehouse or None)
    client = lock_scoped_client(client.pk, user)
    goods_return = GoodsReturn.objects.create(client=client, warehouse=warehouse, created_by=user)
    GoodsReturnItem.objects.bulk_create([
        GoodsReturnItem(
            goods_return=goods_return, product=product, product_label_snapshot=product.plain_label, bags=bags,
        )
        for product, bags in parsed
    ])
    bags = sum(count for _product, count in parsed)
    _log_status(
        goods_return, user, f"создан: {_bags_text(bags)} — ждёт приёмки на складе «{warehouse.name}»", bags=bags,
    )
    return goods_return


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
    """Вывести возврат из «Ждёт приёмки»: кто и когда закрыл или отменил, кладовщик ли это.

    Закрытый или отменённый сейчас возврат — по новым правилам, без денег, даже
    если его создали до них: ``settlement`` очищается.
    """
    goods_return.status = status
    goods_return.settlement = ""
    goods_return.accepted_by = user
    goods_return.accepted_at = timezone.now()
    goods_return.closed_by_storekeeper = by_storekeeper
    goods_return.save(update_fields=["status", "settlement", "accepted_by", "accepted_at", "closed_by_storekeeper"])


@transaction.atomic
def confirm_goods_return_item(goods_return, item_id, accepted_bags) -> GoodsReturn:
    """Кладовщик проверил муку: сколько мешков принято — от 0 до указанного менеджером.

    До закрытия возврата число можно поменять. Склад не меняется.
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


@transaction.atomic
def close_goods_return(goods_return, user) -> GoodsReturn:
    """Кладовщик закрывает возврат: принятые мешки — на склад возврата, деньги не меняются.

    Клиент — под блокировкой в области отдела кладовщика, затем сам возврат.
    Каждая мука должна быть проверена. Всё принято — «Полностью возвращено»;
    ничего — «Отменён», склад не меняется; иначе «Частично возвращено».
    """
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
        raise ValidationError({
            "detail": f"{', '.join(f'«{label}»' for label in gone)} удалена из каталога — на склад её не принять:"
                      " поставьте 0 мешков или отмените возврат",
            "code": "goods_return_product_deleted",
        })
    if accepted:
        warehouse = resolve_warehouse(goods_return.warehouse_id)
        note = f"Возврат товара №{goods_return.pk}, клиент «{client.display_name}»"
        for item in accepted:
            return_stock(item.product, item.accepted_bags, user, warehouse, note=note)
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

    На склад ничего не приходило — откатывать нечего: и у нового возврата, и у
    возвращённого на приёмку (:func:`reopen_goods_return` уже забрал мешки).
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


@transaction.atomic
def reopen_goods_return(goods_return, user) -> GoodsReturn:
    """«Исправить» закрытый возврат: он снова «Ждёт приёмки» с прежними числами.

    Блокировки — как у закрытия: клиент в области отдела кладовщика → сам
    возврат; статус читается под блокировкой строки, поэтому второе «Исправить»
    или исправление во время закрытия — ошибка. Принятые мешки уходят со склада,
    куда пришли, — даже в минус, если их уже отгрузили. Мука сохраняет принятые
    числа и время проверки: кладовщик меняет только ошибочное и закрывает снова
    или отменяет возврат. Старый возврат (с деньгами) не исправляется. Отмену
    менеджера кладовщик не исправляет: менеджер решил, что возврата не будет.
    """
    client = lock_scoped_client(goods_return.client_id, user)
    goods_return = _locked_closed(goods_return)
    if _is_legacy(goods_return):
        raise ValidationError({
            "detail": "Возврат по старым правилам (с деньгами) — исправить нельзя, обратитесь к руководителю",
            "code": "goods_return_legacy",
        })
    if goods_return.status == "cancelled" and not goods_return.closed_by_storekeeper:
        raise _cannot_reopen(goods_return, "его отменил менеджер — если возврат всё же нужен, менеджер создаст новый")
    items = list(goods_return.items.select_related("product").order_by("id"))
    # «Отменён» ничего на склад не клал — даже если до отмены муку успели проверить.
    stocked = [item for item in items if item.accepted_bags] if goods_return.status != "cancelled" else []
    if any(item.product_id is None for item in stocked):
        raise _cannot_reopen(goods_return, "мука удалена из каталога")
    before = {
        "status": goods_return.status,
        "accepted_by": _person(goods_return.accepted_by),
        "accepted_at": _local_iso(goods_return.accepted_at),
    }
    was = goods_return.get_status_display()
    warehouse = resolve_warehouse(goods_return.warehouse_id, require_active=False)
    note = f"Исправление возврата товара №{goods_return.pk}, клиент «{client.display_name}»"
    for item in stocked:
        deduct_stock(
            item.product, item.accepted_bags, user, warehouse, require_active=False,
            note=note, reason="client_return_undo",
        )
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
    """Строки старых возвратов, заказы которых сотрудник видит в списке «Заказов».

    Корзина = удалённое: путь ``order_item__order`` обходит ``LiveOrderManager``,
    поэтому живой заказ — явным фильтром. Область отдела и ``?department=`` —
    те же правила, что у списка заказов.
    """
    lines = GoodsReturnLine.objects.filter(**{f"{_ORDER}deleted_at__isnull": True})
    lines = scope_by_client_department(lines, user, client_path=f"{_ORDER}client")
    return filter_order_scope(lines, params, prefix=_ORDER)


def _returns_without_lines(user, params):
    """Возвраты без строк по заказам (все, кроме старых принятых): область и ``?department=`` — по отделу клиента."""
    returns = visible_goods_returns(user).exclude(_LEGACY)
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

    Новый возврат (и старый, который ничего не провёл) строк по заказам не
    имеет — он виден в области отдела клиента, поиск — по клиенту. Старый
    проведённый виден, если у него есть видимые строки; в ``visible_lines`` —
    только они, с отделом (``order_department``) и валютой заказа; поиск — как
    в списке заказов (клиент, № заказа, номер машины), «#6055» — тоже номер
    заказа. Период ``date_from``/``date_to`` — по дню создания возврата.
    """
    lines = _visible_lines(user, params)
    listed = lines
    without_lines = _returns_without_lines(user, params)
    search = parse_search_param(params.get("search")).removeprefix("#").strip()
    if search:
        listed = lines.filter(**{f"{_ORDER}in": filter_order_search(Order.objects.all(), search)})
        without_lines = without_lines.filter(client_search_q(search))
    returns = GoodsReturn.objects.filter(
        Exists(listed.filter(goods_return=OuterRef("pk"))) | Q(pk__in=without_lines.values("pk")),
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

    У нового возврата денег нет: ``settlement_label`` — ``None``, ``amounts`` —
    ``{}``, ``lines`` — ``[]``. У старого проведённого — что с деньгами, мешки и
    деньги по видимым строкам; деньги — в валюте заказа каждой строки, итог по
    валютам не складывается. Мука — без цвета.
    """
    departments = {row.code: row for row in Department.objects.all()}
    rows = []
    for goods_return in returns:
        lines = goods_return.visible_lines
        rows.append({
            **_common_row(goods_return),
            "settlement_label": goods_return.get_settlement_display() if _is_legacy(goods_return) else None,
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


def goods_return_row(user, goods_return) -> dict:
    """Строка одного возврата из «Заказы → Возвраты» — ответ создания и отмены менеджером."""
    return goods_return_rows(goods_returns_list(user, {}).filter(pk=goods_return.pk))[0]


def storekeeper_rows(returns) -> list[dict]:
    """Возвраты для кладовщика: мука и мешки, денег нет вовсе.

    ``bags`` — сколько указал менеджер, ``accepted_bags`` — сумма принятого по
    проверенной муке (``None``, пока не проверена ни одна). ``can_reopen`` —
    закрытый возврат можно «Исправить» (:func:`reopen_goods_return`): старый
    (с деньгами) и отмену менеджера — нет.
    """
    rows = []
    for goods_return in returns:
        row = _common_row(goods_return)
        checked = [item["accepted_bags"] for item in row["items"] if item["accepted_bags"] is not None]
        row["bags"] = sum(item["bags"] for item in row["items"])
        row["accepted_bags"] = sum(checked) if checked else None
        row["can_reopen"] = (
            goods_return.status in GoodsReturn.CLOSED_STATUSES
            and not _is_legacy(goods_return)
            and (goods_return.status != "cancelled" or goods_return.closed_by_storekeeper)
        )
        rows.append(row)
    return rows
