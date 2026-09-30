# Грузчик: «С какого склада?» — опросник и смешанная отгрузка по складам

Статус: дизайн утверждён владельцем 30.09.2026 (ответы в чате). Разведка — workflow `wf_7ce22198-870`,
дизайн-панель (3 варианта + 2 критика + синтез) — `wf_01601f50-bdc`. Поправки критиков здесь уже учтены.

## 0. Контекст и решения владельца

Сейчас у заказа один склад (`Order.warehouse`, «Склад отгрузки»), и `_do_ship` списывает всё с него. Откуда
реально взяли мешки, нигде не записано. Откат и правка отгруженного заказа поэтому пересчитывают «позиции ×
order.warehouse». Старого выбора склада при отгрузке в git нет (проверены все ветки, stash, недостижимые коммиты,
чистка eadcfed), так что делаем с нуля.

Прод на 30.09: 2 активных склада — «Мельница» (`main`, default) и «Мельница 2» (ни одной карточки `StockItem`,
ни одного движения); остатки «Мельницы» условные (1 000 000, 9 899 551).

Решения владельца:

| # | Решение |
|---|---------|
| D1 | «Фуры» на /loader: после «Подтвердить отгрузку» — нижний лист-**опросник**, шаг на товар «С какого склада?», большие кнопки складов + «С двух складов…». **Без предвыбора** — выбор всегда сознательный. |
| D2 | Остатка не хватает → **предупредить, не блокировать**: «осталось N — уйдёт в минус». Остаток грузчик видит только при нехватке. |
| D3 | Накладная PDF: под строкой «со склада: Мельница» или «Мельница — 12, Мельница 2 — 8». |
| D4 | Вагоны («Отгрузить по отчёту», бот) и ручной «Отгружен» / одобрение запроса статуса — без опросника, всё со «Склада отгрузки» (как сейчас), но источник записывается. |
| D5 | Правка отгруженного смешанного заказа — сначала «Склад отгрузки» (правило §4). |
| D6 | Починить фиксацию задним числом (откат/правка не трогают склад, который не списывали) — и для новых, и для старых (бэкфилл). |

Инварианты: Σ мешков по складам товара = Σ `OrderItem.quantity` товара → деньги, долг, выписки, отчёты не
меняются. `Order.warehouse` остаётся со всеми ролями (форма заказа, фильтр товаров, confirm-context, триггер
`orders_item_requires_stock_card`) и становится складом по умолчанию + «якорем» правила D5.
`set_order_warehouse` уже запрещает менять его после подтверждения — якорь стабилен.

## 1. Модель данных (только expand, contract не нужен)

### 1.1 `shipments.ShipmentSource` (новая, после `ShipmentWagon`)

```python
class ShipmentSource(models.Model):
    """С какого склада взяты мешки товара этой отгрузки (строка на товар×склад).

    Σ bags по товару = Σ OrderItem.quantity товара. Пишет только apps.shipments.sources —
    в одной транзакции с проводками склада, под блокировкой строки Order.
    Нет строк у отгруженного заказа = отгружен до складов-источников (всё со склада заказа).
    Откат удаляет Shipment — строки уходят каскадом. Сводки по нескольким заказам обязаны
    фильтровать shipment__order__deleted_at__isnull=True.
    """

    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="sources")
    product = models.ForeignKey(
        "catalog.Product", null=True, blank=True, on_delete=models.SET_NULL, related_name="shipment_sources"
    )
    warehouse = models.ForeignKey("warehouse.Warehouse", on_delete=models.PROTECT, related_name="shipment_sources")
    bags = models.PositiveIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["shipment", "product", "warehouse"], name="shipment_source_unique_cell"),
            models.CheckConstraint(condition=models.Q(bags__gt=0), name="shipment_source_bags_positive"),
        ]
```

- Привязка к `Shipment`, а не к `OrderItem`: `replace_items` удаляет и пересоздаёт позиции, их id меняются.
  Ключ — товар. К `Order` FK не нужен: `Shipment` 1:1 с заказом.
- Почему не FK `StockMovement.order`: PROTECT ломает `Order.all_objects…delete()` при удалении клиента
  (`clients/views.py:331`) → 500. Кроме того, нулевая сумма проводок неоднозначна: это может быть старый заказ,
  фиксация или откат.
- Несохранённые экземпляры служат «планом» в памяти, отдельный dataclass не нужен.

### 1.2 Миграции

- `shipments/0016_shipmentsource.py` — CreateModel.
- `shipments/0017_shipmentsource_db_on_delete.py` — `common.migration_ops.db_on_delete` для
  (`shipment`, CASCADE) и (`product`, SET NULL). Прецедент — 0009 для `ShipmentWagon`: `shipment.delete()`
  старого образа (автооткат не откатывает миграции) про таблицу не знает.
- `shipments/0018_shipment_stock_deducted.py` — `Shipment.stock_deducted = BooleanField(default=True,
  db_default=True)`. В PG это быстрый default без перезаписи таблицы, а вставки старого образа получат True.
- `shipments/0019_backfill_fixation_stock_deducted.py` — RunPython, reverse = noop (D6, §5).

`StockMovement` не меняется. Проводки отгрузки получают note «Отгрузка заказа #N».

## 2. Бэкенд

### 2.1 Новый модуль `backend/apps/shipments/sources.py`

Это единственный владелец вопроса «откуда взяты мешки отгрузки». Модуль импортирует только `warehouse.models`,
`warehouse.services`, `eventlog` и `.models`. **Нельзя импортировать `orders.services`**: он сам импортирует
shipments на уровне модуля.

- `bags_by_product(items) -> Counter`. **Переносится** из `orders/services.py:1272` (`_bags_by_product`
  удаляется, `orders.services` импортирует отсюда).
- `bags_mismatch(order, lines, *, counted: str) -> str`. **Переименование и обобщение**
  `shipments/services.py::rail_bags_mismatch` (627-642). `lines` — всё, у чего есть `.product` и `.bags`
  (`RailWagon`, `ShipmentSource`). Текст: «Д1с»: в заказе 20, {counted} 18. Вызовы обновляются:
  `services.py:680` (counted="в отчёте"), `bots/rail.py:36,643`. Коды ошибок `rail_bags_mismatch` остаются,
  обёрток «для совместимости» нет.
- `loader_must_choose(order) -> bool` = `order.transport_type == "truck"` и активных складов ≥ 2.
  Это **один предикат** и для `choose` в GET, и для требования `sources` в POST.
- `default_sources(order, items) -> list[ShipmentSource]` — несохранённые строки «Σqty товара × order.warehouse».
  Используется для путей D4 и для отката старых заказов.
- `plan_sources(order, items, raw: list[dict] | None) -> list[ShipmentSource]` — только путь грузчика:
  - `raw is None`: при `loader_must_choose` → 400 `sources_required` («Выберите, с какого склада отгрузка
    (если окна выбора нет — обновите страницу)»), иначе `default_sources`;
  - повтор пары (товар, склад) → 400 `sources_duplicate`;
  - каждый склад → `resolve_warehouse(wid, require_active=True)` (коды `warehouse_not_found` / `warehouse_inactive`);
  - товара нет в заказе или суммы не сходятся → 400 `sources_mismatch` через `bags_mismatch(…, counted="выбрано")`:
    «Состав заказа изменился — ответьте заново: …»;
  - всё считается по товару, поэтому дубли позиций одного товара суммируются.
- `write_off(order, shipment, sources, user) -> list[dict]`:
  - `lock_stock_items({(s.product, s.warehouse)} ∪ {(s.product, order.warehouse)})`. Вторая половина сохраняет
    сегодняшнюю гарантию «карточка на складе заказа есть» для триггера orders/0047;
  - затем по (product_id, warehouse_id) `deduct_stock(…, warehouse=s.warehouse, require_active=False,
    note=f"Отгрузка заказа #{order.pk}", order=order)`;
  - затем `bulk_create` строк;
  - возвращает `[{product, warehouse, warehouse_name, bags}]`.
- `shipment_sources(order, items) -> tuple[basis, rows]` — чистое чтение (для накладной):
  - `stock_deducted is False` → `("not_deducted", [])`;
  - есть строки → `("recorded", rows)`;
  - иначе → `("legacy", default_sources(...))`.
- `checked_sources(order, items, user)` — для писателей (откат, правка), вызывается под блокировкой Order.
  Для `recorded` сверяет Σстрок по товару с `bags_by_product(items)`. Расхождение может оставить только правка
  отгруженного заказа старым образом в окне автооткатa, а старый код всю дельту вешал на `order.warehouse`.
  Поэтому расхождение **лечится** тем же образом: дельта ложится на строку `order.warehouse` товара, пишется
  `log_event("shipment_sources_healed", …)`. Если строка ушла бы ниже 0, возвращается 400 `allocation_mismatch`
  («Склады отгрузки не сходятся с заказом — нужна ручная сверка»), и ничего не двигается.
- `restore_stock(sources, user, *, note)` — `lock_stock_items` по ячейкам, затем
  `adjust_stock(p, +bags, …, warehouse=w, require_active=False)` на каждую. Проводки `adjustment` и события
  `stock_adjust` остаются как сейчас, только по складам.
- `absorb_delta(rows: dict[wid, bags], delta: int, *, anchor_id: int) -> dict[wid, bags]` — чистое правило D5 (§4).
- `reconcile_sources(order, old_items, new_items, *, user, reason) -> (changes, sources_after)`:
  - `not_deducted` → `([], None)`;
  - иначе по каждому товару: `absorb_delta`, дельты по ячейкам → `reconcile_shipment_stock(...)`;
  - `recorded`: строки переписываются;
  - `legacy`: строк не пишет, так что «позиции × order.warehouse» остаётся правдой (ровно сегодняшнее поведение).
- `source_options(order, items) -> dict` — ответ GET, §3.1. `short` = `{wid: balance}` только где balance < bags.
  Берётся через `stock_balances(w, product_ids)` по активным складам; нет карточки = 0.
- `line_sources(sources, items) -> dict[item_id, list[(name, bags)]]` — жадно раскладывает строки товара по его
  позициям в порядке id. Нужна только для отображения в накладной при дублях позиций.
- `sources_text(parts) -> str` — «со склада: Мельница» | «Мельница — 12, Мельница 2 — 8».

### 2.2 `backend/apps/warehouse/services.py`

- `lock_stock_items(cells: Iterable[tuple[Product, Warehouse]]) -> dict[(pid, wid), StockItem]` **заменяет**
  версию для одного склада (103-114). Сортировка (pid, wid), `_locked_stock_item(create=True)`: сначала мьютекс
  Product, потом склады по возрастанию id. Старая версия удаляется, вызывающие переписываются.
- `deduct_stock(…, *, require_active=True, note="", order=None)`: note уходит в `_post_movement`, order — в
  `log_event("stock_negative", order=…)`.
- `reconcile_shipment_stock(deltas: dict[(pid, wid), int], *, order, user, reason)` — ключ по ячейке, kwarg
  `warehouse=` убран; payload `stock_negative` и `changes` — со складом ячейки.

### 2.3 `backend/apps/shipments/services.py`

- `_lock_order_stock` (106-122) **удаляется**. Вместо неё `_order_items(order, *, refusal)`: сохраняет отказ
  `product_deleted`, ничего не блокирует.
- `_do_ship(…, sources: list[ShipmentSource] | None = None)`: `write_off(order, shipment, sources if sources is
  not None else default_sources(...), user)` (именно `is not None`, не `or`). В payload события `shipment` добавляется `"sources"`.
  Если `sources` не передан, вызывающие D4 получают строки на `order.warehouse`, и их код не меняется.
- `dispatch_order(…, sources=None)`: после `assert_no_open_ai_session` и до `set_order_transport` выполняется
  `plan = plan_sources(order, _order_items(...), sources)` — перепроверка под блокировкой Order, затем
  `_do_ship(…, sources=plan)`.
- `_check_loader_dispatch(order, user, truck_number, trailer_number)` выносится из `loader_dispatch`
  (`assert_can_ship` + `check_transport_change(…, ignore_ai_session=True)`), вызывающих двое.
- `loader_dispatch(…, sources=None)` по шагам:
  1. `_check_loader_dispatch`;
  2. `plan_sources(...)` — предпроверка без блокировки, результат выбрасывается;
  3. `counting.close_session_for_dispatch`;
  4. `dispatch_order(…, sources=sources)`.
  Кривой или пустой ответ опросника **не останавливает AI-подсчёт**.
- Новый `loader_dispatch_preflight(order, user, *, truck_number="", trailer_number=None) -> dict`:
  `_check_loader_dispatch`, затем `invalid_status`, если заказ не в `AWAITING_SHIPMENT_STATUSES`, затем
  `source_options(...)`. Ничего не пишет.
- `rollback_shipment`: цикл `_lock_order_stock` + `adjust_stock` заменяется на `checked_sources` +
  `restore_stock(note=f"Откат отгрузки заказа #{pk}: {reason}")`. Блокировка и удаление `Shipment` остаются на
  месте, строки уходят каскадом. В payload: `"restored"`, `"stock_basis"`, `restored_bags` = Σ.
- `manual_complete_order`, `ship_rail_report`, `bots/rail.py`, `approve_status_change` — **без изменений** (D4).

### 2.4 `backend/apps/orders/services.py::replace_items`

Удаляются `old_quantities` и блок stock_deltas (1460, 1476-1491). После `_validate_payment_exposure` вызывается
`stock_changes, sources_after = reconcile_sources(order, old_items, created, user=user, reason=reason)`.
Payload `order_edit` получает `"sources_after"`. Порядок блокировок не меняется.

### 2.5 `backend/apps/orders/fixation.py::_fix_shipped`

`shipment.stock_deducted = False` перед `shipment.save()` (D6).

### 2.6 Сериализаторы, вью, накладная

- `ShipmentSourceInputSerializer(product, warehouse, bags — IntegerField(min_value=1))`.
  `LoaderDispatchSerializer.sources = ShipmentSourceInputSerializer(many=True, required=False, allow_empty=False,
  max_length=100)`.
- `LoaderViewSet`: `required_perms["dispatch_sources"] = "loader.confirm"`, `@action(detail=True, methods=["get"],
  url_path="dispatch-sources")`. Параметры запроса валидирует `TransportNumbersSerializer`. `confirm` передаёт
  `sources`.
- `waybill.py` (147-161): `shipment_sources` → `line_sources` → под названием товара мелкой строкой
  `sources_text`. У `not_deducted` строки источника нет.

### 2.7 Порядок блокировок (задокументировать в docstring `write_off`)

- Отгрузка: Order FOR UPDATE → Shipment get_or_create → Product FOR UPDATE (pid↑) → StockItem (pid, wid↑; создаётся
  с 0) → INSERT проводок и строк → UPDATE Shipment.
- Откат: Order → Product → StockItem → Shipment FOR UPDATE → delete.
- Правка: Order → OrderItem → Payment → Product → StockItem.
- Строки Warehouse не блокируются. INSERT берёт KEY SHARE, это совместимо с NO KEY UPDATE у `transfer_stock`
  (main → Warehouse → Product → StockItem), так что цикла нет. Отдельная блокировка для `ShipmentSource` не
  нужна: все писатели держат Order.

## 3. API (новых прав нет)

### 3.1 `GET /api/loader/orders/{id}/dispatch-sources/?truck_number=…&trailer_number=…`

- Параметры — ровно `transportChanges(...)`, та же семантика, что у dispatch.
- Право `loader.confirm`. Область: чужая зона или отдел → 404, заказ в корзине → 404 (LiveOrderManager).
- Проверки (как в §2.3): `_check_loader_dispatch` (`assert_can_ship` + `check_transport_change`) →
  `invalid_status` → `product_deleted`.
- Ничего не пишет. Вызывается один раз на нажатие «Подтвердить отгрузку» у фуры и **не опрашивается**
  (~4 запроса).

```json
{"choose": true,
 "warehouses": [{"id": 1, "name": "Мельница"}, {"id": 2, "name": "Мельница 2"}],
 "products": [
  {"product": 12, "label": "Д1с · Красный 50 кг", "color": "Red", "bags": 20, "short": {"2": 0}},
  {"product": 15, "label": "Б · Синий 25 кг", "color": "Blue", "bags": 40, "short": {}}]}
```

`warehouses` — активные склады в порядке Meta (name, id). `products` идут в порядке первой позиции, `label` —
`product_label` первой позиции товара.

### 3.2 `POST /api/loader/orders/{id}/dispatch/` (изменён)

```json
{"truck_number": "403BJN13",
 "sources": [{"product": 12, "warehouse": 1, "bags": 12},
             {"product": 12, "warehouse": 2, "bags": 8},
             {"product": 15, "warehouse": 2, "bags": 40}]}
```

- `sources` обязателен ⇔ `loader_must_choose`. Без него всё списывается с `order.warehouse`.
- Ответ не меняется (строка LoaderOrder).
- Новые 400: `sources_required`, `sources_mismatch`, `sources_duplicate`, `warehouse_inactive`,
  `warehouse_not_found`. Все выдаются до закрытия AI-сессии и перепроверяются под блокировкой.

### 3.3 Поведение существующих

- `/loader/orders/{id}/rollback/` и `/orders/{id}/rollback-shipment/` возвращают мешки на записанные склады.
- PATCH отгруженного заказа работает по правилу D5.
- 400 `allocation_mismatch` бывает только при нелечимом расхождении.
- Накладная получает строки источника.
- `/loader/queue/` и `/loader/history/` **не меняются**, тест числа запросов остаётся зелёным.

Журнал:
- `shipment` получает `sources`;
- `shipment_rollback` получает `restored` и `stock_basis`;
- `order_edit` получает `sources_after`;
- `stock_negative` при отгрузке получает `order`;
- новый тип `shipment_sources_healed` (подпись в `frontend/src/lib/event-types.ts`: «Склады отгрузки выправлены»).

## 4. Правило правки отгруженного заказа (D5)

Для товара p: R = его строки {склад: мешки}. У старого заказа R = {order.warehouse: Σqty}, и ничего не
сохраняется. Δ = новое − старое.

Порядок складов в R:
1. `order.warehouse`, если он есть в R;
2. больше мешков;
3. меньший id.

- Δ > 0: вся дельта списывается с первого склада. Нового товара нет в R — он идёт с `order.warehouse`.
- Δ < 0: |Δ| возвращается по порядку, ни одна строка не уходит ниже 0, опустевшие строки удаляются.
  Удалённый товар возвращает каждому складу его долю.
- Остатки: `reconcile_shipment_stock({(p, w): old − new})`. Минус разрешён и пишется в журнал, как сейчас.

Примеры (Д1с 20 = Мельница 12 + Мельница 2 8, «Склад отгрузки» = Мельница):

| Δ | Итог |
|---|------|
| +3 | Мельница 15, Мельница 2 8 |
| −5 | Мельница 7, Мельница 2 8 |
| −15 | Мельница 0 (строка удалена), Мельница 2 5 |
| все 20 с Мельницы 2, затем +3 | Мельница 2 23 |
| новый товар | Мельница |

Свойства: правило детерминировано, +Δ затем −Δ — точная отмена, одноисточниковая отгрузка остаётся
одноисточниковой. Старые заказы ведут себя как сейчас, а лечение расхождения из §2.1 точно повторяет старый код.

## 5. Фиксация задним числом (D6)

Ошибка сейчас: `_fix_shipped` склад не списывает (payload `stock_deducted: False`), но `rollback_shipment`
возвращает «позиции × order.warehouse», а `replace_items` правит склад так, будто его списывали.

- Новые фиксации: `Shipment.stock_deducted = False` → `shipment_sources` отдаёт `not_deducted`. Откат ничего не
  возвращает, правка не двигает склад и не пишет строк, в накладной нет строки источника. Повторная отгрузка
  после отката создаёт новый `Shipment` (default True) и списывает как обычно.
- Бэкфилл 0019 идёт от `Shipment` с `order__status="shipped"`: у исторической модели в миграции нет
  `Order.all_objects`, а заказы из корзины тоже попадают. Берутся `Shipment` без строк источников:
  - берётся последний `EventLog(event_type="shipment", order=o)` **по id** (created_at у фиксации сдвинут назад);
  - если `payload.stock_deducted is False` и нет более позднего `order_edit` с `shipment_correction` →
    `stock_deducted=False`;
  - заказы, правленные после фиксации, только печатаются в вывод миграции для ручной сверки.
- Текущие остатки миграция **не трогает**.

## 6. Фронтенд

### 6.1 `frontend/src/lib/loader.ts` (только добавления, `lib/types.ts` не трогаем)

- Типы:
  - `DispatchSourceProduct {product; label; color; bags; short: Record<string, number>}`;
  - `DispatchSources {choose; warehouses: {id; name}[]; products}`;
  - `DispatchSource {product; warehouse; bags}`;
  - `SourceAnswers = Record<productId, Record<warehouseId, bags>>`;
  - `SOURCE_RESTART_CODES = ["sources_required", "sources_mismatch", "warehouse_inactive", "warehouse_not_found"]`.
- Чистые хелперы (каждый используется листом или страницей, иначе knip):
  - `dispatchSourcesPayload(answers)` — плоский список, нули выброшены;
  - `shortageNote(p, wid, bags)` — «осталось N — уйдёт в минус», N = max(balance, 0);
  - `sourcesText(ctx, chosen)` — те же слова, что в накладной;
  - `splitLabel(n)` — «С двух складов…» | «С нескольких складов…»;
  - `splitRemainder(bags, parts)`;
  - `sameSourceContext(a, b)`.

### 6.2 Новый `frontend/src/components/loader/shipment-sources-sheet.tsx`

`ShipmentSourcesSheet({ context, answers, onAnswers, busy, error, notice, onClose, onConfirm })`

- Каркас: `<Modal variant="sheet" dismissible={false}>`, закрывается только ✕. Высота 92dvh, тело скроллится,
  внизу слот footer с safe-area. Лист в портале, так что проблемы fixed-внутри-AppShell нет.
- **Шаг товара.** Сверху «{i+1} из {N}», заголовок «С какого склада?», точка цвета `colorMeta(p.color).dot`,
  название, крупно `bagsLabel(bags)`.
  - На каждый склад полноширинная `Button variant="outline" className="h-16 text-lg"`. **Ничего не выбрано.**
    Под кнопкой `shortageNote` (`text-[var(--warning)]`).
  - Одно касание записывает {w: bags} и переходит дальше.
  - Последняя `Button variant="ghost"` с `splitLabel(n)` открывает шаг разбивки.
  - С шага 2 в футере «Назад»; при возврате подсвечен прошлый ответ самого грузчика (это не предвыбор).
- **Разбивка.** Заголовок «Сколько с каждого склада?». Поле на каждый склад, кроме последнего (касание ставит
  фокус); ввод через существующий `Numpad` + `pressAmountDigit(value, digit, bags)` / `eraseAmount`.
  - Последний склад только для чтения: «Мельница 2 — 8 · остаток».
  - `shortageNote` у каждой строки.
  - Если остаток < 0: подсказка «Больше, чем в заказе: 20», «Дальше» выключена. Также выключена, пока ничего не
    введено. Нулевые части выбрасываются.
- **Сводка.** «Проверьте и отгрузите». По каждому товару: название, `sourcesText`, предупреждения, «Изменить»
  (переход к шагу).
  - `FormError` и notice **внутри листа**.
  - В футере крупная h-16 «Отгрузить · N мешков»; в процессе «Отгружаем…» и кнопка выключена.
- Segmented и Chip не используем: это мелкие радио/фильтры с предвыбором. `portal/quantity-stepper` — портальный,
  тоже не подходит.

### 6.3 `frontend/src/app/loader/page.tsx`

- Состояние: `sourceSheet {orderId, context} | null`, `sourceAnswers {orderId, answers} | null`, `sourceError`,
  `sourceNotice`.
- `confirm()`:
  - не фура → `dispatch()` как сейчас;
  - фура → `api.get<DispatchSources>(…/dispatch-sources/, { params: transportChanges(...) })`, кнопка в режиме
    «Проверяем склады…»:
    - ошибка (номер, статус, удалённый товар) → `setError` на экране заказа, рядом с полями номера;
    - `!choose` → `dispatch()`;
    - иначе открыть лист. Ответы сохраняются, если тот же заказ и `sameSourceContext`, и тогда лист открывается
      сразу на сводке.
- `dispatch(sources?)` — прежнее тело + `{...(sources ? { sources } : {})}`. Успех → очистить состояние листа.
  Ошибка:
  - код из `SOURCE_RESTART_CODES` → заново GET, сброс ответов, шаг 1, notice «Состав заказа или склады
    изменились — ответьте заново»;
    - свежий GET дал `choose=false` (остался один склад, §7) → лист закрыть, то же пояснение — на экране заказа
      (`setError`); следующее нажатие пойдёт `confirm` → `!choose` → `dispatch()` без `sources`;
    - GET не удался → ошибка в листе, если он открыт сейчас (а не при нажатии: ✕ работает и во время отгрузки),
      иначе — на экране заказа;
  - другой код → закрыть лист с сохранением ответов, `setError` на экране, `queue.refresh()`.
- Опрос очереди приостанавливается, пока лист открыт (`&& !sourceSheet`). Эффект «заказ пропал» тоже ждёт
  закрытия листа. `backToList` / `switchTransport` очищают лист и ответы.

### 6.4 `loader-order-screen.tsx`

Необязательный проп `busyLabel`.

## 7. Граничные случаи

| Случай | Поведение |
|--------|-----------|
| Дубли позиций одного товара | Один шаг с суммой, проверка Σ по всем позициям, в накладной жадная раскладка |
| Откат split-заказа | Строки читаются под блокировкой до delete, +мешки на каждый склад (и на выключенный — `require_active=False`) |
| Повторная отгрузка после отката | Новый Shipment, новые строки |
| Старый заказ (до выката) или shipped без Shipment | «Позиции × order.warehouse», строк не пишем — ровно как сейчас |
| Старая вкладка без `sources` | 400 `sources_required` «обновите страницу» — намеренно (D1) |
| Новый фронт на откаченном бэке | 404 на dispatch-sources, ошибка до перезагрузки; старый бэк `sources` игнорирует |
| Правка во время открытого листа | Перепроверка под блокировкой → `sources_mismatch` → лист заново |
| Склад включили/выключили между GET и POST | `sources_required` / `warehouse_inactive` → лист заново |
| Два устройства | Блокировка Order; второе получает `invalid_status` |
| 1 активный склад | `choose=false`, листа нет, всё с `order.warehouse` (даже выключенного), строки пишутся |
| >2 складов | «С нескольких складов…», n−1 полей + остаток |
| Выключенный `order.warehouse` при ≥2 активных | Не предлагается, выбор из активных |
| «Мельница 2» без карточки | GET `short {"2": 0}` → «осталось 0 — уйдёт в минус»; при списании карточка создаётся с 0, минус, `stock_negative` с заказом; карточка `order.warehouse` тоже гарантирована |
| Корзина | Все входы через `lock_live_order` / живой queryset. Строки заказа в корзине лежат (как `ShipmentWagon`), никто их не агрегирует; восстановление их сохраняет |
| Удаление клиента | ORM + DB CASCADE Order→Shipment→ShipmentSource |
| Удалённый товар | Откат и правка отказывают `product_deleted`, как сейчас |
| Откат старым образом split-заказа | Нельзя обнаружить задним числом (у любого дизайна). Ранбук: события `shipment` с `payload.sources` на >1 склада или ≠ order.warehouse, за которыми `shipment_rollback` без `restored` → исправить перемещением |
| Фиксация задним числом старым образом в окне автоотката после 0018 | Предел дизайна: `Shipment` получает `stock_deducted=True` из `db_default`, а 0019 выполняется один раз и её уже не пометит — откат и правка такого заказа двинут склад, как до выката. Ранбук: `Shipment` с `stock_deducted=True` без строк источников, у которого последнее событие `shipment` (по id) с `payload.stock_deducted=false` и позже нет `order_edit` с `shipment_correction`, → вручную `stock_deducted=False` |

## 8. Тесты

Бэкенд (`cd backend && DB_NAME=asyl_wh_sources .venv/bin/pytest …`, вывод в файл):

- Новый `apps/shipments/tests/test_shipment_sources.py`:
  1. split через API (12/8 + 40): оба StockItem изменились, строки есть, note проводок, `sources` в событии,
     карточка `order.warehouse` есть;
  2. `sources_required` у фуры при ≥2 складах; вагон или 1 склад — без `sources`, строки на `order.warehouse`;
  3. `sources_mismatch` (сумма, лишний товар, недостающий товар), `sources_duplicate`, `warehouse_inactive`,
     `warehouse_not_found`, `product_deleted` отклоняются, и `close_session_for_dispatch` при этом **не вызван**;
  4. правка после GET → POST даёт `sources_mismatch`;
  5. «Мельница 2» без карточки: карточка создана, минус, `stock_negative` с order, 200;
  6. дубли позиций суммируются;
  7. откат грузчиком и старшим: мешки по складам, строки ушли, `restored` в журнале, проводки adjustment на месте;
  8. повторная отгрузка после отката;
  9. D4: `manual_complete_order`, `approve_status_change`, `ship_rail_report`, бот — строки на `order.warehouse`;
  10. старые заказы: откат и правка как сейчас, строк нет;
  11. лечение расхождения + `allocation_mismatch` без движения;
  12. GET dispatch-sources: `choose`, `short`, ошибка номера → 400 без записи, `invalid_status`, 404 для чужой
      зоны и корзины, 403 без права;
  13. чистые: `absorb_delta` таблично по §4 + отмена ±Δ, `line_sources`, `sources_text`.
- Обновить:
  - `orders/tests/test_order_edit_after_loading.py` (D5 на split);
  - `orders/tests/test_order_warehouse.py:158-201`;
  - `warehouse/tests/test_warehouse.py:103-178` (ячейки);
  - `shipments/tests/test_transport_documents.py` (накладная);
  - `shipments/tests/test_rail_report.py` и тесты bots (переименование);
  - `common/tests/test_migration_ops.py` (CASCADE / SET NULL, raw DELETE Shipment);
  - `orders/tests/test_backdated_orders.py` (D6 + бэкфилл по max id).
- `test_loader.py::test_queue_query_count_does_not_grow_with_rows` — не трогать, должен остаться зелёным.
- Гейты:
  - `ruff check --select F apps`;
  - `rg "_lock_order_stock|rail_bags_mismatch\(|_bags_by_product|reconcile_shipment_stock\(.*warehouse="` → 0;
  - `manage.py makemigrations --check --dry-run`.

Фронтенд (из `frontend/`):

- новый `components/loader/shipment-sources-sheet.test.tsx`:
  - «1 из 2», ничего не нажато; касание → дальше; нехватка показана только у короткого склада;
  - разбивка: numpad, авто-остаток, «Дальше» выключена при переборе; 3 склада → «С нескольких складов…»;
  - «Изменить»; payload; ошибка и notice внутри листа; busy;
- `app/loader/page.test.tsx`:
  - в мок api добавить `get`; `choose=false` — прежние проверки payload держатся;
  - `choose=true` — лист, POST с `sources`; ошибка номера на GET — лист не открывается;
  - `sources_mismatch` — рестарт; другая ошибка — ответы сохранены, лист открывается на сводке;
  - опрос на паузе; вагон не зовёт GET;
- `lib/loader.test.ts` — хелперы из §6.1;
- гейты: `npx knip --no-progress`, `npm run check`, `npm run build`;
- вёрстка 375 px в превью (`frontend-local-api` + `backend-local`).

## 9. Выкатка

1. До выката (владелец): оприходовать остаток на «Мельницу 2», иначе каждая отгрузка с неё «в минус» и
   светится на Главной.
2. Пуш только по «пушни», одним коммитом.
3. После деплоя обновить страницу на планшетах грузчиков. Без этого на фуре будет «обновите страницу».
4. Сразу после деплоя прочитать вывод миграции 0019 в логе backend (миграции идут в `entrypoint.sh`):
   `docker compose -f docker-compose.prod.yml logs --no-color backend | grep "shipments.0019"`.
   Строка «shipments.0019: после фиксации задним числом правили состав … заказы: #…» — это заказы, которым флаг
   «склад не списан» не поставлен (§5): их номера передать владельцу для ручной сверки склада. Есть только
   «Applying shipments.0019…» — сверять нечего. 0019 выполняется один раз, а лог пропадает при пересоздании
   контейнера, поэтому читать сразу.

## 10. Вне рамок (отмечено, не делаем)

- `bots/rail.py:~309 _stock_warnings` проверяет склад по умолчанию, а не `order.warehouse`.
- Кнопка «Накладная» на экране ещё не отгруженного заказа всегда получает 400 «Накладная печатается после
  отгрузки».
- Отгруженный заказ в корзине оставляет склад списанным. Сейчас это правило «корзина = удалённое» только для
  отчётов, склад не возвращается.
