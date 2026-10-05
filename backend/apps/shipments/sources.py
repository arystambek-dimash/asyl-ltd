"""Откуда взяты мешки отгрузки: склады-источники заказа (``ShipmentSource``).

Единственный владелец вопроса «с какого склада ушли мешки». Модуль
импортирует только склад, журнал, каталог и свои модели — не
``apps.orders.services`` и не ``.services``: те сами импортируют shipments
на уровне модуля. Σ мешков по складам товара = Σ ``OrderItem.quantity``
товара, поэтому деньги, долг, выписки и отчёты от источников не зависят.
"""
from collections import Counter, defaultdict

from django.db.models import Prefetch
from rest_framework.exceptions import ValidationError

from apps.catalog.models import Product
from apps.eventlog.services import log_event
from apps.warehouse.models import Warehouse
from apps.warehouse.services import (
    adjust_stock,
    deduct_stock,
    lock_stock_items,
    reconcile_shipment_stock,
    resolve_warehouse,
    stock_balances,
)

from .models import Shipment, ShipmentSource


def bags_by_product(items) -> Counter:
    """Мешки позиций по товару; позиции удалённого товара склад не сверяет."""
    bags = Counter()
    for item in items:
        if item.product_id is not None:
            bags[item.product_id] += item.quantity
    return bags


def bags_mismatch(order, lines, *, counted: str) -> str:
    """«Д1с: в заказе 20, {counted} 18» по каждому расхождению; пусто — сошлось.

    ``lines`` — всё, у чего есть ``.product`` и ``.bags``: вагоны отчёта
    (``RailWagon``, ``counted="в отчёте"``) и строки складов-источников
    (``ShipmentSource``, ``counted="выбрано"``). Считается по товару: дубли
    позиций одного товара суммируются, лишний товар — «в заказе 0».
    """
    ordered: Counter = Counter()
    labels = {}
    for item in order.items.all():
        ordered[item.product_id] += item.quantity
        labels[item.product_id] = item.product_label
    listed: Counter = Counter()
    for line in lines:
        listed[line.product.pk] += line.bags
        labels.setdefault(line.product.pk, str(line.product))
    return "; ".join(
        f"«{labels[product_id]}»: в заказе {ordered[product_id]}, {counted} {listed[product_id]}"
        for product_id in sorted(ordered.keys() | listed.keys(), key=lambda pk: labels[pk])
        if ordered[product_id] != listed[product_id]
    )


def loader_must_choose(order) -> bool:
    """Грузчик сам отвечает «С какого склада?»: фура при двух и более активных складах.

    Один предикат и для ``choose`` в GET dispatch-sources, и для обязательных
    ``sources`` в POST dispatch — окно выбора и проверка не расходятся.
    """
    return order.transport_type == "truck" and Warehouse.objects.filter(is_active=True).count() >= 2


def default_sources(order, items) -> list[ShipmentSource]:
    """«Всё со склада отгрузки»: несохранённая строка на товар — Σ мешков его позиций
    на ``order.warehouse``.

    Отгрузки без опросника (вагоны, ручной «Отгружен», одобрение запроса
    статуса — D4; фура при одном активном складе) и откат заказов, отгруженных
    до складов-источников. Позиции удалённого товара строки не получают.
    """
    items = list(items)
    products = {item.product_id: item.product for item in items if item.product_id is not None}
    return [
        ShipmentSource(product=products[product_id], warehouse=order.warehouse, bags=bags)
        for product_id, bags in bags_by_product(items).items()
        if bags
    ]


def plan_sources(order, items, raw: list[dict] | None) -> list[ShipmentSource]:
    """Ответ грузчика «С какого склада?» → несохранённые строки отгрузки.

    ``raw`` — ``[{"product": id, "warehouse": id, "bags": n}]`` из POST ``dispatch``.
    Без ответа всё идёт со «Склада отгрузки», если выбирать не из чего
    (:func:`loader_must_choose`), иначе — ``sources_required``. Сверка — по товару,
    поэтому дубли позиций одного товара суммируются. Грузчик вызывает это дважды:
    без блокировки до закрытия AI-подсчёта (кривой ответ сессию камер не
    останавливает) и под блокировкой Order перед списанием — позиции могли
    поправить, пока был открыт опросник.
    """
    if raw is None:
        if loader_must_choose(order):
            raise ValidationError({
                "detail": "Выберите, с какого склада отгрузка (если окна выбора нет — обновите страницу)",
                "code": "sources_required",
            })
        return default_sources(order, items)
    cells = [(int(row["product"]), int(row["warehouse"])) for row in raw]
    if len(set(cells)) != len(cells):
        raise ValidationError({
            "detail": "Склад товара указан дважды — ответьте заново",
            "code": "sources_duplicate",
        })
    warehouses = {}
    for _, warehouse_id in cells:
        if warehouse_id not in warehouses:
            warehouses[warehouse_id] = resolve_warehouse(warehouse_id, require_active=True)
    products = {item.product_id: item.product for item in items}
    strangers = {product_id for product_id, _ in cells} - products.keys()
    products.update(Product.objects.in_bulk(strangers))
    if strangers - products.keys():
        raise ValidationError({
            "detail": "Состав заказа изменился — ответьте заново: товар не найден",
            "code": "sources_mismatch",
        })
    plan = [
        ShipmentSource(product=products[product_id], warehouse=warehouses[warehouse_id], bags=int(row["bags"]))
        for (product_id, warehouse_id), row in zip(cells, raw, strict=True)
    ]
    mismatch = bags_mismatch(order, plan, counted="выбрано")
    if mismatch:
        raise ValidationError({
            "detail": f"Состав заказа изменился — ответьте заново: {mismatch}",
            "code": "sources_mismatch",
        })
    return plan


def _source_row(source) -> dict:
    """Строка журнала об источнике отгрузки: товар, склад (id и название), мешки.

    Одна форма для события ``shipment`` и для возврата мешков при откате.
    """
    return {
        "product": source.product_id,
        "warehouse": source.warehouse_id,
        "warehouse_name": source.warehouse.name,
        "bags": source.bags,
    }


def write_off(order, shipment, sources, user) -> list[dict]:
    """Списать мешки отгрузки со складов-источников и записать строки ``ShipmentSource``.

    Вызывается из ``_do_ship`` под блокировкой строки Order. Порядок блокировок
    (спека §2.7): Order FOR UPDATE → Shipment → Product FOR UPDATE (id↑) →
    StockItem по (товар, склад)↑, недостающая карточка создаётся с 0 → INSERT
    проводок и строк → UPDATE Shipment. Строки Warehouse не блокируются: INSERT
    берёт KEY SHARE, совместимый с NO KEY UPDATE у ``transfer_stock``.
    Карточка товара на «Складе отгрузки» блокируется (и создаётся), даже когда
    всё взяли с другого склада: на ней держится триггер
    ``orders_item_requires_stock_card`` при правке позиций после отгрузки.
    Остаток может уйти в минус — это факт отгрузки (``stock_negative`` с заказом).
    Возвращает строки для журнала (:func:`_source_row`).
    """
    cells = {}
    for source in sources:
        cells[(source.product_id, source.warehouse_id)] = (source.product, source.warehouse)
        cells.setdefault((source.product_id, order.warehouse_id), (source.product, order.warehouse))
    lock_stock_items(cells.values())
    rows = sorted(sources, key=lambda source: (source.product_id, source.warehouse_id))
    note = f"Отгрузка заказа #{order.pk}"
    for source in rows:
        deduct_stock(
            source.product,
            source.bags,
            user,
            warehouse=source.warehouse,
            require_active=False,
            note=note,
            order=order,
        )
        source.shipment = shipment
    ShipmentSource.objects.bulk_create(rows)
    return [_source_row(source) for source in rows]


def absorb_delta(rows: dict[int, int], delta: int, *, anchor_id: int) -> dict[int, int]:
    """Правило D5: куда ложится изменение мешков одного товара отгруженного заказа.

    ``rows`` — {склад: мешков} товара, ``anchor_id`` — «Склад отгрузки» заказа.
    Порядок складов: якорь (если он есть в строках), затем больше мешков,
    затем меньший id. Прибавка целиком ложится на первый склад (товара не
    было — на якорь). Убавка снимается по порядку, ни одна строка не уходит
    ниже нуля, опустевшие строки выпадают. Прибавка и такая же убавка — точная
    отмена; одноисточниковая строка остаётся одноисточниковой. ``rows`` не
    меняется. Вернуть больше, чем отгружено, — ``ValueError``: вызывающий
    обязан сначала сверить строки с заказом.
    """
    result = {warehouse_id: bags for warehouse_id, bags in rows.items() if bags > 0}
    ranked = sorted(
        result,
        key=lambda warehouse_id: (warehouse_id != anchor_id, -result[warehouse_id], warehouse_id),
    )
    if delta > 0:
        first = ranked[0] if ranked else anchor_id
        result[first] = result.get(first, 0) + delta
        return result
    need = -delta
    for warehouse_id in ranked:
        taken = min(result[warehouse_id], need)
        result[warehouse_id] -= taken
        need -= taken
    if need:
        raise ValueError(f"Вернуть {need} мешков некуда: строки складов меньше отгруженного")
    return {warehouse_id: bags for warehouse_id, bags in result.items() if bags > 0}


def line_sources(sources, items) -> dict[int, list[tuple[str, int]]]:
    """Раскладка складов-источников по позициям заказа — только для накладной.

    Строки товара (склады по имени, затем id) жадно наливаются в его позиции
    по возрастанию id: при дублях позиций одного товара у каждой своя доля.
    Позиция удалённого товара остаётся без источника.
    """
    pools: dict[int, list[list]] = defaultdict(list)
    for source in sorted(sources, key=lambda row: (row.warehouse.name, row.warehouse.pk)):
        if source.product_id is not None:
            pools[source.product_id].append([source.warehouse.name, source.bags])
    parts: dict[int, list[tuple[str, int]]] = {}
    for item in sorted(items, key=lambda line: line.pk):
        pool = pools.get(item.product_id, [])
        need = item.quantity
        taken = []
        while need and pool:
            name, left = pool[0]
            portion = min(left, need)
            taken.append((name, portion))
            need -= portion
            if portion == left:
                pool.pop(0)
            else:
                pool[0][1] = left - portion
        parts[item.pk] = taken
    return parts


def sources_text(parts: list[tuple[str, int]]) -> str:
    """«со склада: Мельница» или «Мельница — 12, Мельница 2 — 8»; без источников — пусто."""
    if len(parts) == 1:
        return f"со склада: {parts[0][0]}"
    return ", ".join(f"{name} — {bags}" for name, bags in parts)


def source_options(order, items) -> dict:
    """Ответ GET dispatch-sources: спрашивать ли «С какого склада?», склады и товары.

    ``choose`` — тот же предикат, по которому отгрузка требует ``sources``
    (:func:`loader_must_choose`). Склады — только активные, в порядке Meta
    (имя, id): выключенный склад заказа не предлагается. Товары — в порядке
    первой позиции, подпись — без цвета (``Product.plain_label``), мешки — сумма
    позиций товара. ``short`` — {id склада строкой: остаток} только там, где
    остатка меньше, чем нужно; нет карточки — 0. Остаток грузчик видит лишь
    при нехватке (D2). Ничего не пишет.
    """
    warehouses = list(Warehouse.objects.filter(is_active=True))
    needed = bags_by_product(items)
    balances = {warehouse.pk: stock_balances(warehouse, needed.keys()) for warehouse in warehouses}
    first_items = {}
    for item in sorted(items, key=lambda row: row.pk):
        if item.product_id is not None:
            first_items.setdefault(item.product_id, item)
    return {
        "choose": loader_must_choose(order),
        "warehouses": [{"id": warehouse.pk, "name": warehouse.name} for warehouse in warehouses],
        "products": [
            {
                "product": product_id,
                "label": item.product.plain_label,
                "bags": needed[product_id],
                "short": {
                    str(warehouse.pk): balances[warehouse.pk][product_id]
                    for warehouse in warehouses
                    if balances[warehouse.pk][product_id] < needed[product_id]
                },
            }
            for product_id, item in first_items.items()
        ],
    }


def _source_rows():
    """Строки складов-источников со складом и товаром, по порядку (товар, склад)."""
    return ShipmentSource.objects.select_related("product", "warehouse").order_by("product_id", "warehouse_id")


def sources_prefetch() -> Prefetch:
    """Предзагрузка строк для :func:`loaded_shipment_sources` — запрос на страницу, а не на заказ."""
    return Prefetch("shipment__sources", queryset=_source_rows())


def _sources_of(order, items, shipment, read_rows) -> tuple[str, list[ShipmentSource]]:
    if shipment is not None and not shipment.stock_deducted:
        return "not_deducted", []
    rows = read_rows(shipment) if shipment is not None else []
    if rows:
        return "recorded", rows
    return "legacy", default_sources(order, items)


def shipment_sources(order, items) -> tuple[str, list[ShipmentSource]]:
    """Откуда взяты мешки отгрузки заказа — чистое чтение.

    ``not_deducted`` — фиксация задним числом: склад не списывали, возвращать нечего.
    ``recorded`` — записанные строки товар × склад.
    ``legacy`` — отгрузка до складов-источников или отгруженный заказ без
    Shipment: всё списывалось со склада заказа, строки считаются на лету
    (:func:`default_sources`) и не пишутся.
    """
    shipment = Shipment.objects.filter(order_id=order.pk).first()
    return _sources_of(order, items, shipment, lambda found: list(_source_rows().filter(shipment=found)))


def loaded_shipment_sources(order, items) -> tuple[str, list[ShipmentSource]]:
    """:func:`shipment_sources` списка заказов: ``order.shipment`` — через ``select_related``,
    строки — из :func:`sources_prefetch`; без запросов на заказ."""
    shipment = getattr(order, "shipment", None)
    return _sources_of(order, items, shipment, lambda found: list(found.sources.all()))


def checked_sources(order, items, user) -> tuple[str, list[ShipmentSource]]:
    """Склады отгрузки для отката и правки — сверенные с составом заказа.

    Вызывается под блокировкой строки Order (её держат все писатели строк).
    Записанные строки обязаны сходиться с позициями по каждому товару.
    Разойтись они могут только после правки отгруженного заказа старым
    образом (окно автоотката), а тот всю разницу списывал со склада заказа —
    поэтому разница ложится на строку ``order.warehouse`` товара (растёт,
    уменьшается, появляется или исчезает) и пишется ``shipment_sources_healed``.
    Склад при этом не двигается: он уже сдвинут старым кодом. Если строка
    ушла бы ниже нуля, складом заказа разницу не объяснить — отказ
    ``allocation_mismatch`` до любой записи.
    """
    basis, sources = shipment_sources(order, items)
    if basis != "recorded":
        return basis, sources
    recorded: Counter = Counter()
    for source in sources:
        recorded[source.product_id] += source.bags
    expected = bags_by_product(items)
    drift = {
        product_id: expected[product_id] - recorded[product_id]
        for product_id in sorted(expected.keys() | recorded.keys())
        if expected[product_id] != recorded[product_id]
    }
    if not drift:
        return basis, sources

    anchor = order.warehouse
    at_anchor = {source.product_id: source for source in sources if source.warehouse_id == anchor.pk}
    plan = []
    for product_id, difference in drift.items():
        row = at_anchor.get(product_id)
        before = row.bags if row is not None else 0
        if before + difference < 0:
            raise ValidationError({
                "detail": "Склады отгрузки не сходятся с заказом — нужна ручная сверка",
                "code": "allocation_mismatch",
            })
        plan.append((product_id, row, before, before + difference))

    products = {item.product_id: item.product for item in items}
    shipment_id = sources[0].shipment_id
    healed = []
    for product_id, row, before, after in plan:
        if row is None:
            sources.append(ShipmentSource.objects.create(
                shipment_id=shipment_id, product=products[product_id], warehouse=anchor, bags=after,
            ))
        elif after == 0:
            sources.remove(row)
            row.delete()
        else:
            row.bags = after
            row.save(update_fields=["bags"])
        healed.append({"product": product_id, "before": before, "after": after})
    log_event(
        "shipment_sources_healed",
        f"Склады отгрузки выправлены по составу заказа: разница отнесена на «{anchor.name}»",
        user=user,
        order=order,
        payload={"warehouse": anchor.pk, "healed": healed},
    )
    sources.sort(key=lambda source: (source.product_id, source.warehouse_id))
    return basis, sources


def restore_stock(sources, user, *, note: str) -> list[dict]:
    """Вернуть мешки отгрузки на склады, с которых они ушли (откат отгрузки).

    Ячейки блокируются разом в глобальном порядке (товар, склад) — как при
    списании, — затем каждая получает проводку ``adjustment`` и событие
    ``stock_adjust``. Склад могли выключить после отгрузки: возврат туда же
    всё равно проходит (``require_active=False``).
    """
    ordered = sorted(sources, key=lambda source: (source.product_id, source.warehouse_id))
    lock_stock_items((source.product, source.warehouse) for source in ordered)
    for source in ordered:
        adjust_stock(
            source.product,
            source.bags,
            user,
            note=note,
            warehouse=source.warehouse,
            require_active=False,
        )
    return [_source_row(source) for source in ordered]


def reconcile_sources(order, old_items, new_items, *, user, reason: str) -> tuple[list[dict], list[dict] | None]:
    """Правка отгруженного заказа: склад и строки источников по правилу D5 (spec §4).

    Разница мешков товара ложится на его склады через :func:`absorb_delta`
    с якорем ``order.warehouse``; проводки — ``reconcile_shipment_stock`` по
    ячейкам (минус разрешён и пишется в журнал). Возвращает изменения склада
    и строки источников после правки; ``None`` — строк нет: фиксация без
    списания (склад не двигается) или отгрузка до складов-источников (всё
    по-прежнему со склада заказа — ровно как до них).
    Вызывающий держит блокировку Order и позиций.
    Блокировки: Order → OrderItem → Payment → Product → StockItem.
    """
    basis, sources = checked_sources(order, old_items, user)
    if basis == "not_deducted":
        return [], None
    before: dict[int, dict[int, int]] = {}
    for source in sources:
        before.setdefault(source.product_id, {})[source.warehouse_id] = source.bags
    old_bags = bags_by_product(old_items)
    new_bags = bags_by_product(new_items)
    after: dict[int, dict[int, int]] = {}
    deltas: dict[tuple[int, int], int] = {}
    for product_id in sorted(old_bags.keys() | new_bags.keys()):
        rows = before.get(product_id, {})
        change = new_bags[product_id] - old_bags[product_id]
        after[product_id] = absorb_delta(rows, change, anchor_id=order.warehouse_id) if change else rows
        for warehouse_id in rows.keys() | after[product_id].keys():
            delta = rows.get(warehouse_id, 0) - after[product_id].get(warehouse_id, 0)
            if delta:
                deltas[(product_id, warehouse_id)] = delta
    changes = reconcile_shipment_stock(deltas, order=order, user=user, reason=reason)
    if basis == "legacy":
        return changes, None
    if deltas:
        warehouses = {source.warehouse_id: source.warehouse for source in sources}
        if order.warehouse_id not in warehouses:
            warehouses[order.warehouse_id] = order.warehouse
        products = {item.product_id: item.product for item in new_items}
        shipment_id = sources[0].shipment_id
        ShipmentSource.objects.filter(shipment_id=shipment_id).delete()
        sources = ShipmentSource.objects.bulk_create([
            ShipmentSource(
                shipment_id=shipment_id,
                product=products[product_id],
                warehouse=warehouses[warehouse_id],
                bags=bags,
            )
            for product_id in sorted(after)
            for warehouse_id, bags in sorted(after[product_id].items())
        ])
    return changes, [_source_row(source) for source in sources]
