# Встреча 23.09: предоплата, «Фуры | Вагоны», подтверждение заявки, тягач+прицеп, WhatsApp-бот вагонов

Статус: дизайн утверждён владельцем 23.09.2026 (ответы в чате). Источник разведки — workflow `wf_39fcf915-af1`
(по агенту-картографу и агенту-критику на фичу). Здесь уже учтены поправки критиков.

## 0. Общие правила (для всех треков)

- Деньги, долг, выручка — только через `backend/apps/orders/debt.py` + `statuses.is_financial` + `labels.py`. Валюты не складываются.
- Планы запросов списков — в `orders/querysets.py`; никаких N+1 (тесты с `django_assert_max_num_queries` там, где добавляем поля в списки).
- Права = страницы меню (`apps/sys_permissions/perms.py`). Миграции rbac только ДОБАВЛЯЮТ права (образец `0026_stores_permissions`), никто не теряет доступ, reverse может быть noop. Старые коды НЕ удалять (автооткат образа не откатывает миграции).
- Новые колонки в существующих таблицах — с `db_default` (образец `Order.rejection_reason`), чтобы старый образ при откате мог вставлять строки.
- Ошибки модалок — внутри модалки. На опрашиваемых экранах применять ответ POST/PUT, а не `reload()`.
- `position: fixed` внутри AppShell children не работает (transform у анимации) — «прилипающий» низ делать `sticky bottom-0` внутри тела модалки или через слот `footer` у `Modal`.
- Тексты UI — по-русски, коротко.
- Тесты бэка: `cd backend && DB_NAME=asyl_<метка> .venv/bin/pytest …` (своя тестовая база на трек!), вывод в файл, код выхода сразу после pytest (zsh без PIPESTATUS).
- Фронт перед сдачей: `cd frontend && npm run check && npm run build` целиком (prettier падает первым — `npx prettier --write` по изменённым файлам).
- Ничего не пушить. Коммиты — только в ветке своего worktree.

## 1. Трек A / T — номера: тягач + полуприцеп, разные страны, быстрый ввод

Проблема: одно поле `Order.truck_number`; `LicensePlateInput` знает только KZ и портит KG-номера («07KG695ADT» → «076 KGA 95»); нормализации нет — форма пишет слитно, грузчик с пробелами, портал как ввели → ложные отказы «задан другим пользователем» на /loader, поиск не находит.

T1. `backend/apps/common/plates.py` — единственный модуль правил:
- `normalize_plate(raw) -> str` — ЧИСТАЯ функция без страны: strip, upper, кириллица-двойники → латиница (А→A В→B Е→E К→K М→M Н→H О→O Р→P С→C Т→T У→Y Х→X), убрать пробелы, `-`, `.`, `·`. «KG» не вставлять/не удалять. O/0 не менять.
- `plate_match_key(compact)` — для сравнения: срезает префикс `KZ`, вставку `KG` после 2 цифр региона, суффиксы `UZ`/`RUS`.
- `detect_plate_country(compact) -> iso|None` — только для отображения; неоднозначные («01123ABC»: UZ-юрлицо или KG без KG) → None.
- `plate_warning(compact) -> str|None` — несовпадение с известными форматами = предупреждение, НЕ ошибка.
- Жёстко (400): после нормализации `^[0-9A-Z]{4,12}$`; иначе понятная ошибка. Для вагона — прежнее правило 8 цифр.
- Форматы: KZ `^\d{3}[A-Z]{3}\d{2}$`, `^\d{3}[A-Z]{2}\d{2}$`, старый `^[A-Z]\d{3}[A-Z]{2,3}$`; KG `^\d{2}KG\d{3}[A-Z]{2,3}$` (тягач 07KG695ADT, прицеп 07KG837PB), старый `^[A-Z]\d{4}[A-Z]{1,2}$`; UZ `^\d{2}[A-Z]\d{3}[A-Z]{2}$`, `^\d{5}[A-Z]{3}$`; RU `^[ABEKMHOPCTYX]\d{3}[ABEKMHOPCTYX]{2}\d{2,3}$`, прицеп `^[ABEKMHOPCTYX]{2}\d{4}\d{2,3}$`. Прицепы KZ/UZ — только мягкая проверка.
- `format_plate(compact)` для накладной/выписок/уведомлений («07 KG 695 ADT»).
- Регулярки камер и зерна (`cameras/ai.py:28`, `vehicle_plate_events.py:37`, `grain/services.py:48`, `grain/vehicle_weight_capture.py:25`) В ЭТОМ ПАКЕТЕ НЕ ТРОГАТЬ — поведение автоматики зерна не менять.

T2. `Order.trailer_number` CharField(30, blank=True, default="", db_default=""). `Shipment.trailer_number` НЕ добавлять. Миграция: AddField + отдельный RunPython-бэкфилл: только `transport_type='truck'`, переписывать `truck_number`, если `normalize_plate(x) != x` И результат подходит под известный формат (свободный текст типа «САМОВЫВОЗ» не трогать); `apps.get_model('orders','Order').objects` (у исторической модели нет all_objects); reverse = noop.

T3. Сервис `orders/transport.py::set_order_transport(order, user, *, truck, trailer=None, notify_client=True, any_department=False) -> (order, changed)`:
- `lock_live_order(…, any_department=…)`; сравнение пары по `plate_match_key`; совпало → no-op БЕЗ записи (не трогать `truck_number_set_by`).
- Правило владельца `can_set_truck_number` (orders/services.py:~1500) СОХРАНИТЬ (решение владельца: номер меняет только тот, кто ввёл — клиент или сотрудник), но сравнивать нормализованно, чтобы разница форматирования не была «сменой». Прицеп — под тем же владельцем пары.
- Замок после arrived/loading/loaded/shipped или при открытой AI-сессии — только на ЗАМЕНУ непустого значения; заполнить пустое поле можно (чинит тупик грузчика с arrived-заказом без номера).
- Событие журнала с payload {truck, trailer, previous}.
- Уведомление клиенту — только при реальной смене сотрудником и `notify_client`; текст без «КАМАЗ»: «Заказ №N: машина X, прицеп Y».
- Сейчас единственное уведомление клиенту при отгрузке приходит через dispatch → set_truck_number («Ваш КАМАЗ … отправляется»). Перенести в `_do_ship`: «Заказ №N отгружен (машина X / прицеп Y | вагон …)».
- `set_truck_number` оставить тонкой обёрткой (совместимость). `set_transport_type` при переходе на train очищает trailer_number. Вагон: 8 цифр и пустой прицеп.

T4. API/сериализаторы: `OrderSerializer` + trailer_number (правило physicalFieldsLocked как у truck_number — фронт после arrived не шлёт оба поля); create нормализует и ставит `truck_number_set_by`; update — один вызов set_order_transport на пару; repeat копирует пару. Портал `PATCH /portal/orders/{id}/truck/` принимает trailer_number, длина → 400 (не 500), повтор того же номера — без уведомления; `PortalOrderSerializer` отдаёт trailer_number и признак `transport_locked` (номер задал сотрудник/после въезда) → портал показывает номер только для чтения («номер указал менеджер»), без падающей кнопки «Сохранить»; после PATCH `setData(ответ)` вместо reload.

T5. Грузчик: `dispatch_order(order, user, *, truck_number, trailer_number=None)` сравнивает нормализованно, пишет через set_order_transport; `LoaderDispatchSerializer` + trailer_number; `LoaderOrderSerializer` + trailer_number и `transport_suggestions` (≤3 последних пар клиента) — считать ОДИН раз на страницу в `LoaderViewSet._page` и передавать через context.

T6. Поиск `common/query_params.py::plate_search_q`: исходный icontains оставить, добавить OR по нормализованному варианту (и с/без KG) — она же используется в поиске рейсов зерна (`grain/views.py:263`), его не ломать. Искать по truck_number | trailer_number.

T7. Накладная: «№ Автомашины: 07 KG 695 ADT / прицеп 07 KG 837 PB». Выписка xlsx: прицеп в ту же ячейку «Номер» («X / Y»), новую колонку НЕ добавлять (денежные колонки заданы индексами).

T8. Фронт: `lib/plates.ts` (зеркало правил + те же тест-векторы в vitest), `components/ui/plate-input.tsx` по образцу `phone-input.tsx` (флаг + прозрачный select KZ/KG/UZ/RU/Другая, одно поле, autoCapitalize, кириллица→латиница на вводе, серый хвост маски, 16px на телефоне, props kind truck|trailer, defaultCountry из `Client.country` (там русское название → ISO через `findCountry(name)?.iso`)). Удалить `LicensePlateInput` в том же релизе. `formatPlate`/`PlateBadge` с учётом страны (неоднозначный — без флага). `formatTransportNumber`/`TransportNumberBadge` принимают trailer. Поля «Тягач» / «Прицеп (необязательно)» в order-form, loader-order-screen (+ чипы прошлых пар), портале. Сырые выводы номера (debts clients/[id], stores/[id]) → formatTransportNumber.

T9. Быстрый ввод «Фуры»: вкладка в /orders (`?tab=trucks`, видна с orders.edit; расширить union вкладок и router.replace). Список фур в статусе confirmed (фильтр «без номера» по умолчанию + «все на сегодня»): № · клиент (флаг) · дата · мешки · [Тягач] [Прицеп] · чипы «как в прошлый раз». Enter в тягаче → прицеп; Enter в прицепе → сохранить строку и фокус в тягач следующей; Esc → откат; статус ✓/ошибка под строкой; ответ POST применяется к строке. Эндпоинты: `POST /orders/{id}/transport/` {truck_number, trailer_number} (orders.edit; ответ — лёгкая строка + plate_warning) и `GET /orders/transport-queue/` (orders.edit; подсказки одним запросом).

T10. Тесты: векторы plates (pytest+vitest), transport endpoint, замок/владелец/no-op, уведомления, backfill, dispatch «403 bjn 13» vs «403BJN13», портал. Поправить тесты, кодирующие старое: `shipments/tests/test_loader.py:~92`, `frontend/src/app/loader/page.test.tsx` (точные тела), `transport-number.test.tsx`, `vehicle-plate-events/page.test.tsx` (aria-label через formatPlate), бэкенд-тесты с «номерами» вроде CLIENT777 (мягкая проверка их пропускает — не ломать).

## 2. Трек A / R — окно подтверждения заявки («8 из 10», остаток, итог, номер)

R1. Перенести набор `DISPATCHABLE_STATUSES` (confirmed, arrived, loading, loaded) в `orders/statuses.py` как `AWAITING_SHIPMENT_STATUSES`; заменить копии (`shipments/services.py:~762, ~422`, `orders/fixation.py:~62`, `orders/querysets.py:~291`).

R2. `GET /orders/{id}/confirm-context/` (orders.confirm; добавить в SHARED/UNASSIGNED request actions): `{items: {item_id: {on_hand, awaiting_shipment}}}` — on_hand по складу заказа (или складу по умолчанию), awaiting_shipment = сумма позиций ЖИВЫХ заказов в AWAITING_SHIPMENT_STATUSES на том же складе, кроме этого (`OrderItem.objects.filter(order__in=Order.objects.filter(...))` — фильтр корзины наследуется). Общий `warehouse.services.stock_balances(warehouse, product_ids)` (строки warehouse=NULL у main — суммировать, явно и с тестом), `ensure_products_available` перевести на него. Без warehouse.view.

R3. `POST /orders/{id}/confirm/` принимает `quantities: {item_id: int 1..запрошенного}`, `truck_number?`, `trailer_number?`. `ConfirmOrderSerializer`: department CharField; prices — сырой dict (коды ошибок `_apply_prices` не менять); quantities DictField(IntegerField(min_value=1)); номера — CharField(required=False, allow_blank=True), пустая строка = «не передан»; принимать multipart/QueryDict (test_confirm.py:13). Больше запрошенного → 400 `quantity_exceeds_request`; чужая позиция → 400 `invalid_item` (фронт показывает в окне «обновите заявку»). `confirm_order(..., quantities=None, truck=None, trailer=None)`: lock → позиции `select_for_update`, количество правится НА МЕСТЕ (id не меняются) до `_apply_prices` → транспорт через `set_order_transport(any_department=True, notify_client=False)` → `transition` с сообщением «Заказ подтверждён: 8 из 10 меш. (Мука 1с — 8 из 10)» (до 3 позиций, «и ещё N»), payload `quantity_changes`. ОДНО уведомление клиенту при урезании: «Заявка №N подтверждена: Мука 1с — 8 из 10 меш.». Сообщения обрезать до 500 символов (EventLog.message и Notification.text — max_length 500).

R4. Фронт `order-confirmation.tsx`: поле количества на позицию (min 1, max = запрошенному, имя «Количество: <товар>»), строка «На складе M · ждут отгрузки K», кнопка «Отдать сколько есть» = min(запрошено, on_hand), выключена при on_hand ≤ 0 (никогда не ставит 0); жёлтое предупреждение, если количество > on_hand − ждут отгрузки (не блокирует); «Итого: 8 из 10 меш. · сумма» в валюте заказа (sticky); кнопка «Подтвердить 8 из 10 меш.» при урезании; блок «Транспорт (можно позже)»: PlateInput тягач+прицеп для фуры, 8 цифр для вагона. confirm-context грузить при открытии окна. В payload `quantities` и номера — ТОЛЬКО если изменены/заполнены (тесты ждут ровно {department, prices}). Поправить `order-confirmation.test.tsx:~86` (spinbutton без name).

R5. `lib/orders.ts::requestEstimate(items, overrides)` + общий признак «Не рассчитана» (как `hasUnpricedItems` в orders/[id]/page.tsx) — карточка заявки (`order-requests.tsx:~53`) показывает мешки и «≈ сумма» или «Не рассчитана» вместо «0 ₸»; `order-form.tsx:~247` использует тот же хелпер.

## 3. Трек A / L — грузчик: вкладки «Фуры | Вагоны» со своими правами

L1. Права: в раздел `loader` ДОБАВИТЬ `loader.trucks` («Фуры») и `loader.wagons` («Вагоны»); `loader.view`/`loader.confirm` остаются (страница и кнопка). Миграция rbac 0027 по образцу 0026: держателям loader.view или loader.confirm выдать обе области; reverse noop. Шаблоны ролей: «Грузчик: фуры» и «Грузчик: вагоны».

L2. `backend/apps/shipments/access.py`: `allowed_transports(user)` (суперюзер — все), `assert_can_ship(user, order)` = loader.confirm И loader.<область типа> (user=None — системная автоматика, пропускаем). Вызывать ВНУТРИ сервисов после блокировки заказа: `dispatch_order`, `record_shipment`, `start/finish_train_loading`, `set_loading_camera`(если есть), `cameras.counting` start/stop/reset для заказа. Новый сервис `shipments.services.loader_dispatch(order, user, *, truck_number, trailer_number=None)`: assert_can_ship → `close_session_for_dispatch` → `dispatch_order` (перенести цепочку из `LoaderViewSet.confirm`, чтобы проверка шла ДО закрытия AI-сессии; её же вызовет бот/вагоны). `cameras/policies.can_control_session`: область по `session.order.transport_type` (select_related там, где вызывается в цикле). Админские пути (set-status/approve/fixate под orders.edit) не трогать.

L3. `LoaderViewSet`: `get_queryset` фильтрует `transport_type__in=allowed_transports`; queue/history принимают `?transport=truck|train` (без права → 403); rollback проверяет область; waybill чужого типа → 404.

L4. Фронт /loader: вкладки «Фуры | Вагоны» по loader.trucks/loader.wagons (одна область — без переключателя), «К отгрузке / История» сегментом; canConfirm = loader.confirm && область; transport во ВСЕ запросы (queue, history, overdue-счётчик `page.tsx:~57`, счётчики вкладок); последний таб в localStorage (try/catch). ТИХИЙ опрос очереди раз в 15 с (`useVisiblePolling`), пауза при busy/открытом действии; ошибка опроса НЕ заменяет список (держать последние данные + маленький индикатор); если открытый заказ исчез (отгружен с другого устройства) — вернуть к списку с пояснением. Карточка вагона: номер, клиент, тонны (`estimated_load_kg/1000`) и мешки. Группировка — в `lib/loader-groups.ts` с тестом. Сайдбар/RequirePerm — как были (loader.view).

L5. Тесты: фикстуры со scope (conftest operator/boss, `shipments/tests/test_loader.py` фикстуры, `cameras/tests/test_ai.py` loader, test_train_api), доступ по типам (queue/history/dispatch/rollback/waybill/AI), прямой вызов сервиса без права → PermissionDenied, миграция, каталог прав, фронт (вкладки, кнопка, опрос тихий).

## 4. Трек B / P — предоплата («оплачен» до отгрузки)

Принцип d567a96 сохраняется: оплата НИКОГДА не блокирует логистику. Меняется только «когда можно принять деньги». Портал клиента — как было (только после отгрузки).

P1. `statuses.is_payment_open(status, *, method=None, by_client=False)`: клиент — только shipped; сотрудник — shipped (любой способ) ИЛИ статус в AWAITING_SHIPMENT (confirmed/arrived/loading/loaded) и способ в `Payment.SETTLED_ON_RECORD` (наличные, свой Kaspi-терминал, «удалённо»). Kaspi QR (ApiPay) и счёт на телефон — только после отгрузки (поздняя оплата старого QR на отменённом заказе иначе уводит деньги из учёта). `_validate_payment_open(order, *, method, by_client=False)` через него; все вызовы передают method; `create_client_payment` — by_client=True. Окно оплаты магазина — только при shipped (и на фронте `lib/debt-orders.ts::blockingStore`). Фиксация: оплата разрешена и для confirmed (`fixation.py:~155`, `fixation-fields.tsx:~45,69,95`, `order-fixation-modal.tsx:~20-52`).

P2. `OrderSerializer.payment_open` (вычисленное для сотрудника) + `payment_open_methods`; фронт перестаёт зеркалить статусы (`payment-chain.tsx`), до отгрузки показывает только кнопки наличных/терминала/удалённо.

P3. Целостность статуса оплаты: `_do_ship`, `_fix_shipped`, `rollback_shipment` НЕ ставят `unpaid` вслепую — считать через `_payment_status_for(order)` ПОСЛЕ сохранения статуса (в тот же update_fields; `sync_payment_status` делает refresh_from_db — не звать до save). Событие «в долг» — только на остаток > 0 и на сумму остатка.

P4. `assert_order_has_no_money(order)` (чистые подтверждённые > 0 или есть requested/received) → 400 `order_has_payments` «По заказу принято X — сначала оформите возврат». Вызывать под блокировкой: `_force_set_status` → pending/cancelled (покрывает approve), pre-check в `request_status_change`, `rewind_loading` → pending/cancelled, `rollback_shipment` → pending/cancelled, `reject_order` (вместо payments.exists()), `soft_delete_order` для НЕотгруженного. Откат отгрузки в confirmed с оплатами РАЗРЕШЁН (убрать запреты «уже есть оплата» в `loader_rollback_blocker` и `payments_exist` для target confirmed) — деньги остаются предоплатой, статус оплаты сохраняется (тест).

P5. Переплата (решение владельца: список «К возврату»): уменьшение количества/цены НЕ блокировать (подтверждённые деньги — неизменный факт, как уже для shipped). `debt.order_overpaid(order) = max(0, чистые подтверждённые − сумма)`; `correct_order_prices` перевести на общий `_validate_payment_exposure` (убрать копию). `OrderSerializer.overpaid_amount`; карточка заказа: «Переплата X — вернуть клиенту» + кнопка возврата (существующий PaymentRefundView, payments.confirm). В «Кассе → Оплаты» вкладка «К возврату» (queryset в querysets.py через with_order_amounts, живые заказы, скоуп отдела).

P6. «Кассе → Оплаты» вкладка «К отгрузке»: заказы в AWAITING_SHIPMENT с остатком > 0 (скоуп отдела), строки через `OrderPaymentActions` (только наличные/терминал/удалённо). Эндпоинт — рядом с awaiting_payment (payments.create или payments.confirm). Мобильная касса: та же секция на экране «Оплаты».

P7. «Оплата сразу» в форме заказа (шаг «Дополнительно»): видна при !editing && payments.create && orders.confirm && !backdate; способ (Наличные / Kaspi-терминал / Удалённо) + сумма (по умолчанию итог, синхронизируется, пока не правили). Отправка: POST /orders/ → если заказ confirmed → POST /orders/{id}/payments/ тем же телом, что и OrderPaymentActions → переход на заказ. Если оплата упала или заказ не подтвердился — переход на заказ с `?pay=…` и ошибкой/открытым окном «Принять оплату» там. Никаких вызовов провайдера в транзакции создания.

P8. Тексты: без «долг уменьшен» для неотгруженных (order-payment-actions :~92,101,141; POS `pos-steps.tsx:~259`), бейдж оплаты у предоплаченного неотгруженного (карточка, список, портал — только бейдж, без кнопок оплаты). Грузчик ничего нового не получает — его PaymentMark уже показывает «Оплачен/Частично/Не оплачен».

P9. Тесты: параметрический open/closed по статусам и способам; QR/счёт до отгрузки → payment_not_open; окно магазина только shipped; _do_ship сохраняет settled, событие долга на остаток; отмена/удаление/отклонение/rewind с деньгами → order_has_payments, после возврата — можно; откат в confirmed с оплатой; переплата видна и в «К возврату»; «К отгрузке»; отчёты (касса по confirmed_at, продажа по shipped_at, валюты раздельно); переписать тесты старого правила (`test_payment_flow.py:~19`, `test_payments.py:~378`, `test_backdated_orders.py:~156`, `order-payment-actions.test.tsx:~58,159`); портал остаётся закрытым (`test_portal_actions.py:~275`, `test_payments_client.py:~74`).

P10. Перед деплоем (не в коде): read-only подсчёт на проде `Order.objects.exclude(status='shipped').filter(payments__status='confirmed')` (легаси до d567a96).

## 5. Трек W — вагоны: отчёт Джин-Сина, «Вставить отчёт» у грузчика, WhatsApp-бот (после слияния A и B)

Решения владельца: WhatsApp через Green-API, новая SIM + группа «Отгрузка вагонов» (Джин-Син, Динара, бот); бот САМ создаёт заказ и проводит отгрузку; Динара перестаёт вносить вагоны вручную (бот ловит похожий ручной заказ ±2 дня → на разбор); код товара в отчёте не фиксированный → словарь.

Формат отчёта:
```
сб 19.09.26 Узбекистан  ООО OSIYO NAV NIHOL
Ст. Раустан 12 вагон
Д1с-28087658-68 тн
…
```
Строка вагона = `[код товара]-[8 цифр вагона]-[тонны] тн`. Исторически один вагонный заказ = вся партия (16 320 мешков = 12 × 1360), truck_number пустой.

W1. Модели: `shipments.ShipmentWagon` (shipment CASCADE, number 8 цифр, product SET_NULL + снимки названия и веса мешка, bags, weight_kg, position, source_message SET_NULL null; unique (shipment, number)); `Order.rail_station` (CharField 120, db_default ""); `catalog.ProductAlias` (code нормализованный unique, product CASCADE, created_by, created_at) — правится на странице «Товары» (catalog.edit) и пополняется при разборе.

W2. Парсер `apps/bots/parsing.py` (чистый): шапка (день недели необязателен, дата dd.mm.yy(yy), страна из списка, клиент), «Ст. X N вагон(а/ов)», строки вагонов (NBSP, тире –—, латинская c/кириллическая с, «т/тн», десятичная запятая); проверки: число строк = заявленному, дубли номеров, контрольная цифра (импортировать `cameras.shipping_segment_identity.valid_number`, НЕ рефакторить его), тонны×1000/вес мешка — целое.

W3. Сервисы: `resolve_report` → клиент (BotClientProfile: алиас → клиент + валюта; иначе точное нормализованное совпадение, ровно один), товары по ProductAlias, цена из ClientPrice валюты профиля со сверкой с последним отгруженным вагонным заказом клиента по тому же товару и валюте (допуск 15%, иначе разбор), отдел клиента, дубли (ShipmentWagon того же номера у живого заказа за 14 дней; ручной вагонный заказ того же клиента ±2 дня с теми же мешками). `create_rail_order` по образцу `repeat_order` (не выносить OrderSerializer.create), без проверки остатка (вагон уже уехал; минус — предупреждением). `ship_rail_report(order, wagons, user, *, station, shipped_day)`: `assert_can_ship` (область вагонов) → Shipment + ShipmentWagon → `_do_ship` → если день отчёта < сегодня — сдвиг shipped_at и событий shipment/debt через общий хелпер, вынесенный из `fixation._shift_event`/`fixation_moment`. Одна транзакция. Для заранее созданного вагонного заказа — мешки по товарам должны совпасть, иначе «в заказе N, в отчёте M — поправьте заказ». `ShippingLoadingSession.order` НЕ писать.

W4. Грузчик, вкладка «Вагоны»: «Вставить отчёт» → предпросмотр (без записи) → разрешение неизвестного (клиент/валюта/товар — сохраняет алиасы) → «Провести» (loader.confirm + loader.wagons + orders.create + orders.confirm) → результат. У ожидающего вагонного заказа — «Отгрузить по отчёту». История/карточка/накладная/поиск/выписка/портал показывают список вагонов (prefetch в `with_order_api_relations`); накладная — таблица вагонов + «Станция назначения»; «Скопировать отчёт» в формате владельца.

W5. Бот `apps/bots`: `BotMessage` (provider+provider_message_id unique, chat/sender, text ≤8 КБ, status received/parsed/applied/needs_review/awaiting_confirmation/rejected/ignored/failed, parsed/issues JSON, order SET_NULL, reply, attempts), `BotClientProfile`, `WhatsAppBotSettings` (enabled, allowed_chat_ids, allowed_sender_ids, show_amounts_in_reply=False, duplicate_window_days=14, price_tolerance_pct=15). Green-API в режиме опроса (ReceiveNotification/DeleteNotification — без публичного вебхука, очередь у Green-API держит 24 ч, переживает перезагрузки ps.kz): команда `run_whatsapp_bot` (+heartbeat/healthcheck по образцу мониторов), сервис в docker-compose.prod.yml, env необязательные (`${X:-}`), `.env.example`. Сервисный пользователь `ensure_whatsapp_bot_user` (нельзя войти; права orders.create, orders.confirm, loader.view, loader.confirm, loader.wagons; НИКОГДА clients.set_price/суперюзер — тест). Автопроведение, если всё сошлось; иначе needs_review. Ответ цитатой: «Проведено: заказ №N, CLIENT, ст. X, 12 вагонов, 816 т (16 320 мешков …)» (суммы — только если включено), «Принято, на проверке: <причины>». Правки/удаления сообщений → разбор. LLM (флаг, по умолчанию выкл.): только если структура не разобралась, тонкий клиент в bots, результат — черновик на разбор, никогда не проводит. Журнал `/management/whatsapp-bot` (права bots.view/bots.manage, аддитивная миграция): состояние номера, сообщения, разбор, «Провести» от имени человека (с его правами), «Игнорировать».

W6. Тесты: парсер на примере владельца и поломках; резолв/дубли/цены/валюты; ship_rail_report (склад, долг, ShipmentWagon, даты задним числом и события); права; бот идемпотентность и ответы (Green-API замокан); сервисный пользователь без опасных прав; журнал без N+1; фронт.
