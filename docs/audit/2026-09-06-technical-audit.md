# Технический аудит АСЫЛ-LTD — 6 сентября 2026

Исходный commit: `f28daca8f357d15c17a8450a6b94393afe9d70ef`. Этот отчёт составлен **до изменения прикладного кода**. Итоги исправлений и повторных проверок находятся в [отдельном отчёте](2026-09-06-remediation.md); описания ниже фиксируют исходное поведение.

Аудит охватывает структуру репозитория, все приложения backend, маршрутизацию и frontend API layer, основные денежные и физические сценарии, фоновые процессы, доступы, схемы БД и инфраструктуру. Это анализ checkout и локальное воспроизведение, а не проверка реальных производственных данных или установленного на Windows AI-сервиса. Код самого детектора/весов находится вне этого репозитория; его фактическое исполнение и точность распознавания здесь не доказаны. Нет заявления, что найдены все возможные дефекты каждой строки.

Инвентаризация: 205 записей маршрутизации без format suffix, 229 frontend call sites, 199 прикладных Python-файлов, около 83 тысяч строк Python/TS/TSX без тестов и миграций. См. [маршруты и определения модулей](2026-09-06-inventory.md), [frontend вызовы](2026-09-06-frontend-calls.json).

Исходные проверки: **2104 backend-теста + 3 subtests прошли**, **516 frontend-тестов / 79 файлов прошли**, frontend format/lint/typecheck прошли, **33 deploy-теста прошли**. Django не обнаружил расхождения моделей с миграциями. Девять дополнительных проверок ожидаемого поведения воспроизвели девять ошибок; они не входили в существующую тестовую выборку. Локально: Django 5.2.15, DRF 3.17.1, psycopg 3.3.4, Celery 5.6.3, PostgreSQL; CI использует PostgreSQL 16 и Python 3.12, локальные проверки — Python 3.11.

## 1. Architecture overview

Система — модульный Django-монолит с отдельным браузерным Next.js-приложением. PostgreSQL хранит бизнес-состояние, Redis — кэш, блокировки и брокер Celery. Основной backend работает синхронно через gunicorn/WSGI; наличие `asgi.py` не делает сервисы асинхронными. Поэтому основной риск долгого I/O здесь — занятые HTTP workers и блокировки БД, а не пропущенный `await` в async view.

Frontend: Next.js 15.5.21 App Router, React 19.2.7, TypeScript, Tailwind 4, Zustand для авторизации. Основные страницы — client components. Данные загружаются Axios-клиентом; `useApi`, `usePagedApi`, `useVisiblePolling`, `useAiCounter` реализуют локальный query/state слой без общего кэша доменных сущностей. Наличие generic `<Order>` — обещание TypeScript, а не runtime-проверка ответа.

Entry points: `backend/manage.py`, `config/urls.py`, `config/wsgi.py`, `config/celery.py`, management commands камер/автовесов/производственного склада; frontend `src/app/layout.tsx`, `components/layout/app-shell.tsx`, `src/app/**/page.tsx`, `src/store/auth.ts`, `src/lib/api.ts`. Docker Compose запускает backend/frontend/nginx/PostgreSQL/Redis/go2rtc, фоновые мониторы и Celery.

| Контур | Сущности и источник истины | Основной сценарий |
|---|---|---|
| Доступы | User, Employee.permissions, Department, MonoblockDevice | JWT → актуальные персональные permissions → queryset scope по отделу клиента; моноблок ограничен своей камерой |
| Заказы | Order, OrderItem со снимком товара | создать → проставить договорные цены → подтвердить → погрузить → отгрузить → оплатить |
| Оплаты | Payment, ApiPayInvoice, PaymentRefund, ApiPayRefund, ApiPayWebhookEvent | локальная резервация → запрос провайдеру → webhook или reconciliation → подтверждение/возврат |
| Отгрузка | Shipment + Order + AiCountingSession | камера закрепляется за заказом, точный final счёта завершает погрузку; выезд проводит склад |
| Мешковой склад | Warehouse, StockItem, StockMovement, StockReceipt | приёмка/корректировка/перемещение/списание; Product служит mutex для остатков |
| Зерно | GrainSupply, Wagon, SiloReservation, SiloAllocation, GrainMovement | приёмка через лабораторию либо short flow; отдельный обратный по весу passage для вывоза отрубей |
| Автовесы | PassageScaleAutomationState, AutomaticPassageCapture, PassageWeightCapture, UnassignedWeighing | занятость весов → стабильный вес → OCR → атомарная фиксация или ручной разбор |
| Камеры 24/7 | cursors, imported events, production runs, stock batches/postings | события детектора → аналитика → выпуск по цвету/товару → склад |
| Поручения | Task, TaskAttachment, TaskNotification | постановка → ожидание → выполнение, возможен возврат |
| Аудит | EventLog, клиентские Notification | доменная операция → запись события; уведомление не заменяет учётную запись |

Фактические state machines:

- Заказ: `draft/pending → confirmed → arrived → loading → loaded → shipped`; train и camera start могут миновать arrived. Есть отдельные ручные override и rollback. `shipped` завершает логистику, но оплаты остаются доступны. `deleted_at/purged_at` — независимая ось видимости.
- Payment: `requested → received → confirmed`; отклонение, восстановление, повторное открытие и возвраты имеют отдельные guards. Подтверждённая сумма = amount − refunded_amount; pending_refund_amount резервирует доступный возврат.
- AiCountingSession: `starting → active → closed/failed`; ошибка очистки сохраняет обязанность остановить точную remote session перед повторным использованием камеры.
- Зерно: classic `expected → arrived → gross_weighed → lab_pending → unloading_allowed/quarantine → silo_assigned → unloading → unloading_completed → tare_weighed → inventoried → exit_allowed → exited → completed`; промежуточные отклонения/перевзвешивания описаны в `grain/statuses.py`. Simple flow минует лабораторию; passage вычисляет `выход − вход`, intake — `вход − выход`.
- Автоматика: durable capture `processing → completed/failed`; следующая машина допустима после доказанного освобождения весов, для ошибки требуется предусмотренное подтверждение оператора.

Сильные стороны: денежные операции обычно используют Order → Payment locks; финансовые webhook имеют durable inbox и HMAC; суммы валют разделены; исторические позиции имеют снимки; складские операции используют транзакции; AI stop привязан к конкретной сессии; query hooks защищаются от поздних ответов; JWT refresh защищён поколениями сессии; приватное media не выдаётся напрямую nginx; очистка вложений задач выполняется после commit.

## 2. Critical problems

Подтверждённой неавторизованной компрометации уровня CRITICAL не обнаружено. Это не результат penetration test. Самые опасные подтверждённые проблемы — **HIGH**: A01, A02, A03, A04, A05, A07, A10. Они затрагивают физический процесс, состояния, финансовый UI и потерю согласованности с Camera-PC. Их следует устранять до косметического рефакторинга.

### [HIGH] A01. Команды classic grain работают с устаревшим Wagon

**Файлы:** `backend/apps/grain/services.py` (`approve_unplanned`, `record_lab_check`, `assign_silo`, `change_silo`, `start_unloading`, `finish_unloading`, `resolve_discrepancy`, `register_exit`), `backend/apps/grain/views.py`.

**Что происходит:** многие функции имеют `transaction.atomic`, но проверяют переданный из view экземпляр, не перечитывая строку под блокировкой. Atomicity защищает группу записей от частичного commit, но не делает исходный объект актуальным.

**Почему это проблема:** два оператора получают `silo_assigned`. Первый запускает и заканчивает разгрузку; задержанный start второго снова переводит вагон в `unloading` и переписывает timestamps. Сценарий воспроизведён двумя экземплярами одной строки. Аналогично возможно применение старого решения лаборатории или переназначения после изменения физического состояния.

**Backend ↔ Frontend conflict:** кнопка отправляет команду для состояния, которое уже изменил другой оператор; backend принимает её вместо доменной ошибки.

**Как исправить:** канонический parent lock Wagon и повторная проверка состояния внутри транзакции каждой команды; строгий порядок блокировок зависимых силосов. Сохранить существующий контракт сервисов по обновлению переданного экземпляра. Для закрытия поставки отдельно сериализовать пересчёт её финальности.

**Риск изменения:** Medium.

### [HIGH] A02. Назначение силоса не соблюдает правила подбора

**Файлы:** `backend/apps/grain/services.py:561` (`suggest_silos`, `assign_silo`, `change_silo`).

**Что происходит:** suggestions исключают несовместимую культуру/класс, неактивные силосы и неправильный карантинный контур. `assign_silo` проверяет лишь часть этих условий; `change_silo` в основном проверяет вместимость. POST может назначить пшеницу в силос ячменя — воспроизведено.

**Почему это проблема:** UI-подбор не является ограничением API. Сохранённый старый выбор либо прямой запрос создаёт маршрут, который подбор считает невозможным. Перенос из карантина может обойти ограничения исходного назначения.

**Backend ↔ Frontend conflict:** «подходящие силосы» и фактически допустимые сервером силосы — разные множества.

**Как исправить:** одна доменная проверка совместимости для suggestions/assign/change; проверять актуальный silo после lock и учитывать историю карантинного решения, а не только текущий status. Проверять до начала физической разгрузки.

**Риск изменения:** Medium.

### [HIGH] A03. PATCH задачи обходит переназначение и может затереть выполнение

**Файлы:** `backend/apps/tasks/views.py` (`perform_update`, `_resolve_assignee`), `serializers.py`, `services.py`, `frontend/src/app/tasks/page.tsx:266`.

**Что происходит:** UI меняет assignee через PATCH, который вызывает обычный `serializer.save()`. Отдельный `/reassign/` вызывает сервис с уведомлением и eventlog. Auto-generated FK field допускает клиента; тест получил 200 при назначении Task на `is_client=True`. Новый обычный исполнитель при PATCH не получает уведомления. Generic save сохраняет также старое состояние объекта из view.

**Почему это проблема:** задача исчезает из доступного исполнителям контура; при гонке PATCH с complete можно затереть `done/status/done_at`. Проверка can_close перед lock также может пережить смену исполнителя.

**Backend ↔ Frontend conflict:** основная форма использует путь, где отсутствуют эффекты специализированного endpoint.

**Как исправить:** направить PATCH и reassign в один сервис, блокировать Task, валидировать активного сотрудника, проверять актуальные права исполнителя после lock, писать только изменяемые поля. Уведомление и журнал — в одной транзакции.

**Риск изменения:** Medium.

### [HIGH] A04. Портал не узнаёт о внешнем завершении оплаты

**Файлы:** `frontend/src/app/portal/orders/[id]/page.tsx`, `src/lib/portal-actions.ts`, `backend/apps/orders/webhooks.py`, `reconciliation.py`.

**Что происходит:** страница получает заказ при mount и после собственных действий. Подписки или polling оплаты нет. Webhook и кассир меняют БД без действия этого браузера.

**Почему это проблема:** после успешной оплаты QR продолжает отображаться, остаток и возможность скачать квитанцию остаются прежними до ручного reload. То же относится к подтверждению/отгрузке открытого заказа.

**Backend ↔ Frontend conflict:** async completion на backend используется как будто результат изменяется только синхронно после UI mutation.

**Как исправить:** последовательный visible polling заказа во время активного жизненного цикла, остановка на финальных состояниях, восстановление после online/visibility, видимая ошибка обновления с сохранением последнего состояния.

**Риск изменения:** Low.

### [HIGH] A05. Экспорт датасета подтверждает уже изменённую разметку

**Файлы:** `backend/apps/grain/orientation_dataset.py` (`export_pending`, `set_manual_label`, `exclude_sample`, `export_removals`, `purge_samples`, `purge_all`).

**Что происходит:** экспорт читает label, отправляет фото по HTTP, затем без проверки версии ставит `sent_at`. Если во время HTTP оператор меняет front на rear, в CRM rear помечается доставленным, хотя на ПК отправлен front. Проверка с изменением во время remote call воспроизвела ошибку.

**Почему это проблема:** следующая синхронизация пропускает исправление. При исключении/очистке одновременно с экспортом возможна копия на ПК без корректной обязанности удалить её. При неизвестном исходе POST нельзя считать отсутствие `delivered_at` доказательством, что фото не было принято.

**Backend ↔ Frontend conflict:** UI считает ручную правку сохранённой и ожидает её применение при следующем обучении; backend может навсегда оставить старую метку на ПК.

**Как исправить:** проверка версии/ревизии при подтверждении доставки; консервативный след возможной remote-копии до POST; сериализация purge/export относительно удаления одного и того же sample, без удержания денежных locks. Полное решение требует также правил для неизвестного результата HTTP и bulk clear.

**Риск изменения:** Medium для CAS, High для изменения distributed lifecycle.

## 3. Frontend ↔ Backend conflicts

| Сценарий / контракт | Сверка URL, method, body, response | Результат |
|---|---|---|
| Auth login/refresh/me/initial-password | POST credentials; JWT strings; GET Me; structured 401/403 | Основная форма согласована. Есть generation guards. Общий timeout отсутствует: A12 |
| Создание заказа | POST `/orders/`, items/product/quantity, prices по product id | Метод/URL согласованы; transport и raw prices валидируются неполно: A07 |
| Редактирование/confirm | PATCH `/orders/{id}/`, confirm POST с prices по item id | Разные ключи prices имеют смысл, но не формализованы схемой. Снимки/валюта/warehouse guards присутствуют |
| Списки заказов | GET `/orders/?page=...` → results; без page → массив | Преднамеренная совместимость. Пагинация не курсорная: A17 |
| Shipment / AI | POST arrive/load/finish/ship; AI POST/DELETE с order/session id; 409/502/503 | Имена статусов согласованы; remote final проверяется по точной сессии |
| Касса | Payment POST, requested/received/confirmed/rejected; provider fields и available refund | Разделение confirmation modes присутствует; ночная очередь мешает reconciliation: A11 |
| Portal pay / request-debt | POST → полный PortalOrder, денежные поля nullable до подтверждения | Возвращаются старые scalar fields: A06; внешние изменения не обновляют UI: A04 |
| Portal release | POST release, 200 либо 202 при cancelling | 202 означает ожидание; основной helper отдаёт только data. Требуется polling A04 |
| Portal invoice | GET invoice | Условие payment_method == invoice не принимает mixed, хотя смешанная оплата поддерживается: A18 |
| Warehouse | GET stock; POST adjust/receive/transfer; явные from/to warehouse | Публичный контракт согласован, отрицательный остаток и rollback конфликтуют: A10 |
| Tasks | PATCH assignee vs POST reassign | Разные бизнес-эффекты: A03 |
| Grain lifecycle | POST commands, detail Wagon; list brief Wagon | State checks не везде атомарны: A01; raw request validation: A08 |
| Silos | GET список с вычисляемыми balance/reserved/free | Формат согласован, 7 дополнительных запросов на строку: A09 |
| Unassigned weighings | GET unassigned + candidates, POST assign/create/discard | GET ошибки скрываются пустым состоянием, кандидаты не обновляются polling: A14 |
| Orientation | GET страницы/summary; POST label/exclude/purge | Гонки доставки A05, длительные HTTP batches A11/A13 |
| Notifications | GET client array / staff envelope, POST read | Это разные DTO, frontend различает их; не следует объединять механически |
| Reports/statements | GET range/currency filters; JSON или blob PDF/XLSX | Деньги передаются строками, валюты отдельно; blob error parser присутствует |
| Employees/client access | PATCH security отдельным действием; first password change | Server permission escalation guards присутствуют; права не берутся только из frontend |
| Video/private media | signed cookie WebSocket; signed task/photo links | Не JWT в query; expiry учитывается для task links. Транспорт реально WebRTC, README устарел: A22 |

### [MEDIUM] A06. Portal pay отдаёт предыдущий payment_method и settlement_intent

**Файлы:** `backend/apps/portal/views.py:125` (`pay`, `request_debt`, `release_payment`), `backend/apps/orders/services.py:309` и `:463`.

**Что происходит:** сервис перечитывает Order под lock и меняет новый экземпляр; view очищает только payments prefetch исходного объекта. Тест: ответ `payment_method=pending`, БД `debt`.

**Почему это проблема:** один response смешивает актуальные связанные оплаты и старые поля заказа. Текущий UI частично скрывает дефект дополнительным GET, но контракт API неверен и ломается при сетевой ошибке следующего чтения.

**Backend ↔ Frontend conflict:** helper обещает authoritative PortalOrder, а получает смесь состояний.

**Как исправить:** перечитать Order с relations перед формированием mutation response; проверить pay всех методов, debt и release с 202.

**Риск изменения:** Low.

### [HIGH] A07. Staff Order create пропускает недопустимые входные данные

**Файлы:** `backend/apps/orders/serializers.py:18`, `:324`, `:541`, `backend/apps/orders/services.py:959`.

**Что происходит:** transport_type создаётся auto-generated CharField без choices. POST с `plane` принят (201), тогда как последующий setter разрешает только truck/train. items без ограничений количества строк; raw `initial_data['prices']` ожидается как dict без схемы. Queryset товара не ограничен active. Computed totals сериализуются DecimalField(12,2), хотя quantity × допустимая unit_price может превысить 12 цифр.

**Почему это проблема:** сохраняется заказ без корректного физического маршрута; malformed prices вызывают AttributeError, большие quantities/итоги — DB/serialization errors. Список заказов может упасть из-за одного большого документа. Архивный товар можно передать напрямую при наличии остатка.

**Backend ↔ Frontend conflict:** UI предлагает только допустимые варианты и считает Order DTO валидным, backend это не гарантирует на создании.

**Как исправить:** явный ChoiceField transport, структурный bounded item input, prices object validation до save, запрет нового выбора архивного товара при сохранении чтения истории. Отдельно определить/реализовать предел суммы заказа либо расширить безопасную сериализацию рассчитанной суммы.

**Риск изменения:** Medium; денежные пределы требуют осторожности с существующими крупными заказами.

## 4. Business logic conflicts

Основные подтверждённые конфликты — A01/A02/A03/A07/A10. Склад и приложение сознательно допускают перерасход, а приём оплаты сознательно связан с отгрузкой; эти решения сами по себе не названы ошибками. Противоречие возникает там, где один путь допускает состояние, а другой не позволяет его корректно обработать.

### [MEDIUM] A18. Счёт портала недоступен для mixed оплаты

**Файлы:** `backend/apps/portal/views.py:215`, `backend/apps/orders/services.py:309`, `frontend/src/app/portal/orders/[id]/page.tsx`.

**Что происходит:** частичные оплаты разных методов устанавливают payment_method=mixed; endpoint invoice требует строго invoice до поиска реального invoice Payment.

**Почему это проблема:** у заказа может существовать действующий счёт, но документный endpoint его отвергает. В текущем UI основной путь счёта провайдерский, поэтому не доказано, что endpoint сейчас нужен пользователям.

**Backend ↔ Frontend conflict:** общий DTO и оплата поддерживают mixed, старое действие документа — нет.

**Как исправить:** определить, является документ счётом всего заказа или только invoice-части; после этого разрешать по наличию подходящего Payment и формировать правильную сумму.

**Риск изменения:** Medium.

## 5. Backend problems

### [HIGH] A08. Grain batch actions не полностью атомарны и не валидируют типы

**Файлы:** `backend/apps/grain/services.py` (`add_wagon_numbers`, `inventory_wagon`, `publish_supply`), `backend/apps/grain/views.py` (`GrainSupplyViewSet.perform_update`, `add_wagons`, `inventory`).

**Что происходит:** добавление номеров сохраняет их по одному вне общей транзакции. Если второй номер занят, первый уже сохранён, а ответ — ошибка. PATCH поставки сохраняет scalar fields до ошибки добавления. Inventory приводит `amount_kg` через int: 100.9 становится 100, неизвестный silo/неправильный контейнер может дать 500.

**Почему это проблема:** UI считает всю операцию неуспешной, повтор получает дубликаты. В зерновом учёте молча теряется дробная часть вместо 400. Отрицательное распределение защищается типом SiloAllocation в БД, но приводит к IntegrityError, а не корректной validation response.

**Backend ↔ Frontend conflict:** форма ожидает all-or-nothing команду и целые килограммы; сервис это не обеспечивает для всех входов.

**Как исправить:** атомарный batch и PATCH, до записи проверить список/уникальность/длину номеров; strict positive integer amount/id и enum источника измерения; все silo locks брать по pk, сумму распределений проверять до движений.

**Риск изменения:** Medium.

## 6. Frontend problems

### [MEDIUM] A12. У основного Axios-клиента и refresh нет deadline

**Файлы:** `frontend/src/lib/api.ts:6`, `:94`, `use-visible-polling.ts`, `use-ai-counter.ts`.

**Что происходит:** create({baseURL}) и отдельный axios.post(refresh) не задают timeout. AbortController при unmount полезен, но не ограничивает время запроса на открытой странице.

**Почему это проблема:** соединение может зависнуть, оставив busy=true или последовательный polling навсегда в ожидании. Nginx timeout не покрывает все клиентские сетевые режимы и прямой dev backend.

**Backend ↔ Frontend conflict:** сервисы ограничивают upstream HTTP, frontend query lifecycle не имеет собственного срока завершения.

**Как исправить:** разумный default deadline и отдельный refresh deadline, возможность увеличить для известных тяжёлых операций; timeout мутации показывать как неизвестный исход, без автоматического повторного POST.

**Риск изменения:** Low/Medium.

### [MEDIUM] A14. Ошибка неопознанных взвешиваний выглядит как отсутствие записей

**Файлы:** `frontend/src/components/grain/unassigned-weighings.tsx:292`.

**Что происходит:** error/loading из обоих useApi игнорируются; null data превращается в [], после чего компонент возвращает null. Poll обновляет unassigned, но не список существующих рейсов-кандидатов.

**Почему это проблема:** при 500/сети оператор не видит ни проблемы, ни ожидающих разбора весов. За время открытой смены кандидаты устаревают; новый открытый рейс может не появиться в выборе.

**Backend ↔ Frontend conflict:** backend failure превращён в успешный empty state; новый Wagon из автоматики не попадает в local state.

**Как исправить:** явная загрузка/ошибка/повтор в панели, сохранять последние данные с признаком устаревания; вместе обновлять candidate query, выключать действия без достоверных кандидатов.

**Риск изменения:** Low.

### [MEDIUM] A15. Production polling моноблока допускает overlap и позднюю запись после закрытия

**Файлы:** `frontend/src/app/monoblock/page.tsx:1063–1111`.

**Что происходит:** два взаимоисключающих эффекта используют setInterval каждые 15 секунд. Cleanup убирает timer, но не отменяет текущий GET и не инвалидирует sequence при закрытии. Sequence защищает от некоторых старых ответов, но не от самого запуска параллельных запросов и лишней нагрузки в скрытой вкладке.

**Почему это проблема:** медленный production endpoint получает несколько запросов от одной карточки; закрытая/сменённая карточка может принять предыдущий результат. Endpoint дополнительно меняет производственные состояния, поэтому это не только лишний render.

**Как исправить:** один активный polling hook, следующий tick после завершения, visibility gate и abort/generation на scope change; сохранить отдельность дневной аналитики.

**Риск изменения:** Medium.

## 7. Database problems

### [HIGH] A10. Откат отгрузки не работает при остатке, который останется отрицательным

**Файлы:** `backend/apps/warehouse/services.py` (`adjust_stock`), `backend/apps/shipments/services.py:515` (`rollback_shipment`).

**Что происходит:** отгрузка законно списывает в минус. Rollback вызывает adjust_stock с положительной дельтой, который запрещает любой отрицательный итог.

**Почему это проблема:** при −100 мешках возврат отгрузки на 10 мешков даёт −90 и отклоняется. Система разрешает состояние, из которого штатная компенсирующая операция не выполняется. Дополнительно video cleanup уже мог произойти до отказа складской корректировки.

**Backend ↔ Frontend conflict:** UI предлагает контролируемый rollback с причиной; backend отвергает допустимый частичный возврат фактически списанного товара.

**Как исправить:** разрешать положительную коррекцию отрицательного остатка, запрещая его дальнейшее уменьшение ручным adjust; либо использовать отдельную доменную компенсацию склада. Проверить delta, движения и rollback вместе.

**Риск изменения:** Low/Medium.

### [MEDIUM] A16. Часть инвариантов существует только в прикладных guards

**Файлы:** `backend/apps/orders/models.py` (Order/Payment), `backend/apps/grain/models.py` (GrainSettings, Silo/GrainSupply), миграции соответствующих приложений.

**Что происходит:** не все статусы/enum и money inequalities имеют CHECK constraints; GrainSettings.get() делает first-or-create без уникального singleton. Некоторые административные serializers допускают state/границы ёмкости без сверки движений и резервов.

**Почему это проблема:** management command, concurrent initialisation или будущий write path может сохранить данные, которые UI/сервисы не умеют интерпретировать. Снижение capacity ниже текущего balance+reserve возможно отдельно от операций движения.

**Как исправить:** добавлять ограничения только после запроса на существующие нарушения и с отдельной миграцией; singleton key; сервис изменения capacity, сериализованный с резервами. Не накладывать положительность остатка мешкового склада: отрицательные значения здесь предусмотрены.

**Риск изменения:** High для production-миграций с неизвестными историческими данными.

## 8. Concurrency / async problems

A01/A03/A05/A08 — подтверждённые нарушения согласованности. У HTTP mutations нет общего автоматического retry, что правильно для неидемпотентных операций. Однако внешние side effects и БД не являются одной транзакцией.

### [HIGH] A11. Обучающие фото выполняются в единственной очереди платежей

**Файлы:** `backend/config/_settings/base.py` (`CELERY_TASK_ROUTES`, `CELERY_BEAT_SCHEDULE`), `backend/apps/grain/tasks.py`, `orientation_dataset.py`, `backend/apps/cameras/ai.py:477`, оба Compose-файла (`celery-payments`).

**Что происходит:** `grain.export_orientation_samples` и reconciliation отправляются в `payments`, worker concurrency=1. До 300 uploads и 300 removals выполняются последовательно, на HTTP — до 20 секунд; collect по умолчанию перечитывает все записи retention-окна.

**Почему это проблема:** ночная обработка фотографий задерживает пропущенные webhook и возвраты, heartbeat reconciliation устаревает, expiring периодические сообщения теряются. Верхний расчётный бюджет remote calls может составлять часы; это сценарий задержек, а не измеренная длительность production.

**Backend ↔ Frontend conflict:** платежный UI ждёт завершения, recovery занят unrelated задачей камер.

**Как исправить:** отдельная очередь/worker для камер с media volume и собственным lifecycle/healthcheck. Обновлять Compose и deploy orchestration одновременно, иначе scheduler начнёт отправлять задачи в очередь без потребителя.

**Риск изменения:** Medium.

### [MEDIUM] A13. Очистка датасета не ограничена временем HTTP-запроса

**Файлы:** `backend/apps/grain/orientation_dataset.py` (`PURGE_BATCH=100`, `purge_samples`), `backend/apps/cameras/ai.py` (`ORIENTATION_SAMPLE_TIMEOUT=20`), `deploy/nginx/conf.d/asyl-ltd.conf`.

**Что происходит:** комментарий обещает уложиться в 60 секунд за счёт лимита 100 строк, но это до 100 последовательных HTTP DELETE. Даже 1 секунда на запрос превышает стандартный nginx read timeout.

**Почему это проблема:** браузер получает timeout при частично применённой очистке. Повтор может пересекаться с ещё работающим запросом. При ответах AiError цикл продолжает работать до конца пакета.

**Как исправить:** deadline на весь batch, корректные progress/remaining/deferred counters, continuation из UI; удаление remote копий — безопасно повторяемое. Для больших объёмов использовать выделенную фоновую работу.

**Риск изменения:** Medium.

### [MEDIUM] A19. Длительный I/O удерживает DB locks

**Файлы:** `backend/apps/cameras/counting.py`, `backend/apps/orders/apipay.py` (`_provider_scope_fence`), `backend/apps/shipments/services.py` (`rollback_shipment`).

**Что происходит:** некоторые remote calls намеренно выполняются под transaction/row lock; AI start/stop и удаление записей видео могут занимать десятки секунд. У большинства операций нет явного PostgreSQL lock_timeout.

**Почему это проблема:** недоступность камеры/провайдера блокирует связанные команды и занимает gunicorn worker. В rollback внешнее удаление видео не откатывается при последующем DB failure.

**Как исправить:** ограничить общий бюджет и измерять lock wait; выносить сопутствующую очистку в durable job после commit. Разделять reservation/remote/apply только при сохранении fences и точной идентичности операции — механический вынос HTTP из atomic может вернуть уже устранённые гонки.

**Риск изменения:** High.

## 9. Security problems

Подтверждённое нарушение назначения аккаунта — A03. Неполная серверная validation — A07/A08. Нельзя считать выборы frontend авторизацией.

Проверено: JWT требует активного пользователя и проверяет отзыв после смены пароля; staff/client разделены; неизвестные permission actions закрыты; выдача permissions сотрудниками ограничена собственным набором, привилегированные цели защищены; portal queryset ограничен client.user; department scope реализован на backend; webhook ApiPay подписан; media закрыто nginx и выдаётся signed URL; SQL, найденный для advisory locks и settings, использует параметры; task attachments проверяют размер и сигнатуру файла. Подтверждённого SQL injection/path traversal в просмотренных пользовательских входах не найдено.

`config/`, `.env`, `backups/` присутствуют локально, но не входят в `git ls-files` и исключены `.gitignore`. Содержимое секретных файлов не выводилось. Это не доказывает отсутствие старых секретов во всей истории Git или в реальном окружении.

### [MEDIUM] A20. Сборка не воспроизводит точные Python dependencies; PostCSS advisory

**Файлы:** `backend/requirements-prod.txt`, `backend/requirements.txt`, `frontend/package.json`, `frontend/package-lock.json`, `.github/workflows/ci.yml`.

**Что происходит:** Python-зависимости заданы диапазонами без lock/hashes. CI устанавливает заново и помечает pip-audit как informative (`continue-on-error` плюс `|| true`). npm audit локально показал **0 high/critical, 3 moderate** — один PostCSS advisory и зависимые Next/Tailwind записи, а не три независимые уязвимости.

**Почему это проблема:** повторная сборка commit может получить другой код библиотек. Из advisory само по себе не следует эксплуатируемость публичного CRM API; условие связано с обработкой управляемого атакующим CSS/sourceMappingURL.

**Как исправить:** reproducible Python lock/constraints и политика обновления; отдельно проверить безопасный patch PostCSS. Не выполнять npm audit fix --force с major upgrade Next.js ради транзитивной записи.

**Риск изменения:** Medium.

Источник advisory: [GHSA-fxqj-rqcc-2cmp](https://github.com/advisories/GHSA-fxqj-rqcc-2cmp). Python CVE scan отдельно не выполнен: pip-audit не установлен в локальном venv.

## 10. Performance problems

### [MEDIUM] A09. N+1 в списке силосов

**Файлы:** `backend/apps/grain/models.py:100`, `serializers.py:27`, `views.py` (`SiloViewSet`).

**Что происходит:** current_balance, reserved, free_capacity, fill_percent, sensor_difference и active_wagons повторно обращаются к БД на каждой строке. Prefetch default_for_types не покрывает эти вычисления.

**Почему это проблема:** измерено 9 SQL для одного и 37 для пяти силосов. Каждая открытая вкладка дополнительно умножает запросы.

**Backend ↔ Frontend conflict:** формат DTO правильный, стоимость получения скрыта от UI.

**Как исправить:** read projection с subquery последнего balance, aggregate active reservations и одним prefetch active wagons; не добавлять cached_property в write domain, где после движения требуется свежий баланс.

**Риск изменения:** Low/Medium.

### [MEDIUM] A17. Неограниченные списки и offset pagination в меняющихся данных

**Файлы:** `backend/apps/common/pagination.py`, `backend/apps/portal/views.py`, `backend/apps/tasks/views.py`, `backend/apps/notifications/views.py`, `frontend/src/lib/use-paged-api.ts`, страницы отчётов/архива.

**Что происходит:** opt-in pagination оставляет многие запросы плоскими массивами. Портал, задачи, уведомления и часть отчётов читают всю историю. usePagedApi конкатенирует results без идентичности/cursor; вставка перед второй страницей может повторить строку, удаление — пропустить.

**Почему это проблема:** по мере роста данных растут JSON, Python memory и DOM; query hook reload возвращает первую страницу, сбрасывая накопленную глубину. На часто меняющихся списках возможны одинаковые React keys/повторы.

**Как исправить:** мигрировать конкретные растущие списки на paging с одновременным frontend изменением; cursor или стабильный snapshot для live списка, дедупликация отображения по id. Глобальное PAGE_SIZE сейчас сломает действующие array consumers.

**Риск изменения:** Medium.

Дополнительная нагрузка: A11/A13/A15/A19; высокая частота AI status 500 мс является осознанным требованием overlay, её нельзя снижать без проверки UX. Статический обзор не заменяет нагрузочный тест с реальным числом камер и заказов.

## 11. Dead code / duplicated code

### [LOW] A21. Правила и orchestration разбросаны между слоями

Дополнение после исходного аудита: [подробный разбор сложности cameras](2026-09-06-camera-architecture.md). В нём отдельно описаны перегруженные обязанности и два дополнительных MEDIUM-риска поведения: мутация при GET и конкурирующие источники state карточки.

**Файлы:** `orders/services.py` (1781 строк), `orders/apipay.py` (2013), `orders/views.py` (1427), `grain/services.py` (3169), `frontend/src/app/monoblock/page.tsx` (2242), `orders/page.tsx` (1559), `components/order-form.tsx` (1002), `src/lib/types.ts` (1253).

**Что происходит:** views координируют provider create/reject/restore; serializers создают/редактируют заказы и цены; frontend page содержит таблицу, формы, состояние камер и polling. Статусы/labels/условия действий представлены в Python и TS отдельно. Regex KZ_VEHICLE_PLATE_RE дублируется в grain/services и vehicle_weight_capture.

**Почему это проблема:** одинаковое действие легко реализовать двумя путями с разными эффектами — A03 уже показывает такой дефект. Размер файлов сам по себе не ошибка, но затрудняет восстановление lock order и контрактов.

**Backend ↔ Frontend conflict:** дублируемые определения требуют ручной синхронизации; массовая генерация DTO пока отсутствует.

**Как исправить:** выделять по завершённым доменным сценариям: intake/passages/silo operations, cashier orchestration, production hooks. Сначала контрактные тесты, затем перенос без смены API. Генерировать DTO из проверяемой схемы постепенно.

**Риск изменения:** Medium.

Не объявлены dead code только по отсутствию frontend вызова: legacy routes `/train`, compatibility warehouse fields/DB triggers, ApiPay recovery aliases и старые management команды могут быть rollback/операционными интерфейсами. `PortalOrderSerializer.validate()` содержит update-ветку при create/read-only viewset; endpoint старого PDF invoice требует уточнения A18. Удаление без проверки внешних потребителей не выполнено.

## 12. Непонятные места

### [MEDIUM] A22. Документация архитектуры противоречит коду

**Файлы:** `README.md`, `backend/apps/sales/access.py`, `backend/apps/accounts/models.py`, `frontend/src/components/camera-stream.tsx`, `backend/apps/warehouse/models.py`.

**Что происходит:** README утверждает, что отдел не ограничивает данные, хотя scope_by_client_department это делает; описывает StockItem OneToOne вместо многоскладского rollout; описывает fMP4, тогда как frontend использует WebRTC/UDP. Часть старых endpoint/service имён отсутствует.

**Почему это проблема:** будущий разработчик может «исправить» работающий guard в соответствии с устаревшим описанием или неправильно диагностировать сеть камер.

**Backend ↔ Frontend conflict:** прямого runtime конфликта нет; документация даёт неверную модель.

**Как исправить:** обновить README из фактических контрактов и отделить исторические сведения/rollback.

**Риск изменения:** Low.

### [INFO] A23. Видимость удалённых финансовых документов и определение долга

**Файлы:** `backend/apps/orders/models.py`, `orders/debt.py`, `orders/reports.py`, `clients/services.py`.

> Не могу однозначно определить предполагаемое поведение.

**Что делает код сейчас:** shipped можно скрыть из живого менеджера и отчётов; физический финансовый документ остаётся для reconciliation. Долг определяется как shipped + intent=debt + положительный остаток. При начале частичной instant оплаты оставшийся остаток переходит в awaiting, а не debt.

**Возможные интерпретации:** корзина означает исключение ошибочного документа из управленческого отчёта; либо архив должен скрывать только операционную карточку при сохранении финансовых итогов. Аналогично долг может означать только согласованную отсрочку либо любую непогашенную отгрузку.

**Почему потенциально опасно:** перемещение документа в корзину меняет отчёт без денежной компенсации; клиентский выбор способа оплаты может менять состав видимой дебиторки. В коде есть отдельный awaiting, поэтому исчезновение из debt нельзя автоматически назвать потерей денег.

**Backend ↔ Frontend conflict:** определения в текущем коде в основном согласованы; неясен бизнес-смысл.

**Как исправить:** закрепить определения «архив», «аннулирование», «дебиторка», «ожидающая оплата» и сценарии возврата; менять только после такого решения.

**Риск изменения:** High.

### [MEDIUM] A24. Резерв силоса, изменение поставки и физический факт разгрузки

**Файлы:** `backend/apps/grain/serializers.py`, `grain/views.py` (`GrainSupplyViewSet.perform_update`), `grain/services.py` (`prepare_simple_supply`, `inventory_wagon`).

> Не могу однозначно определить предполагаемое поведение.

**Что делает код сейчас:** simple supply создаёт Wagon и резерв при создании. Последующий PATCH исходных полей поставки не обязательно пересчитывает уже созданные wagon/reservation. Inventory проверяет физическую capacity, но не вычитает резервы других вагонов при размещении сверх собственного плана.

**Возможные интерпретации:** поля поставки — редактируемый план, который должен распространяться на ещё не прибывшие вагоны; либо это первоначальный документ, а маршрутизация вагона после создания независима. Резерв может быть жёсткой гарантией или лишь планом, уступающим фактическому весу.

**Почему потенциально опасно:** пользователь видит изменённый план, а камера/учёт исполняют прежний; свободная вместимость может стать отрицательной за счёт обещаний другим вагонам.

**Как исправить:** определить момент фиксации плана и политику переполнения резервов; затем единая edit command с пересчётом либо запрет соответствующих PATCH после публикации.

**Риск изменения:** High.

### [MEDIUM] A25. Backup не имеет атомарного общего поколения DB/media

**Файлы:** `deploy/backup/backup.sh`.

**Что происходит:** pg_dump выполняется до tar media; затем несколько файлов latest/prev/checksum заменяются последовательными mv. Lock через mkdir снимается trap, но не переживает SIGKILL автоматически.

**Почему это проблема:** удаление/запись медиа между dump и tar может оставить DB-ссылку без файла. Падение при rotation даёт разные поколения DB/media; SIGKILL может оставить lock и блокировать следующие backup. `pg_restore --list` проверяет структуру архива, но не полноценное восстановление приложения.

**Backend ↔ Frontend conflict:** не применимо.

**Как исправить:** версионированные backup directories с manifest и одним atomic promotion; согласованный retention медиа/snapshot или короткая пауза destructive media writes; проверка живого владельца lock; регулярный restore drill в отдельную БД.

**Риск изменения:** Medium; существующие production dumps не изменялись.

## 13. Recommended architecture

Сохранить модульный монолит и существующие приложения. Микросервисы, универсальные repository для каждой модели и замена всего state management не нужны.

1. **Команда — единый путь изменения.** Route валидирует request DTO и permissions; сервис под parent lock проверяет актуальное состояние и пишет учёт/событие. Generic PATCH не должен обходить этот путь. Read serializers не запускают remote side effects.
2. **Read projections отдельно от write state.** Для силосов и больших списков использовать queryset annotations/prefetch. Не кешировать balance в mutable domain instance без явной invalidation.
3. **Разные очереди для денег и обучения камер.** Общие image/config допустимы; процессы и heartbeat независимы. Для provider операций сохранять idempotency keys, inbox и reconciliation.
4. **Remote effects: reservation → remote operation → fenced apply.** При неизвестном HTTP результате хранить unresolved obligation. Послеcommit cleanup должен быть durable, если его потеря существенна. Не переносить side effects через простой on_commit без recovery.
5. **Контракты.** Явные serializers для изменяющих actions; единый error envelope; documented 202/409/502/503. Постепенно OpenAPI/DTO generation и сценарные contract tests. Оставить legacy envelopes до миграции всех consumers.
6. **Frontend.** Расширять существующие hooks: bounded requests, последовательный visible polling, server response как authoritative snapshot, явный stale/error. Выделять крупные страницы по доменным задачам при реальной работе над ними.
7. **DB-инварианты.** Добавлять проверяемые CHECK/unique только с предварительной диагностикой данных и безопасными миграциями. Задокументировать глобальный lock order по каждому контуру.

Основание по транзакциям и query loading: [Django QuerySet reference](https://docs.djangoproject.com/en/5.2/ref/models/querysets/#select-for-update). По разделению длительных задач: [Celery tasks](https://docs.celeryq.dev/en/stable/userguide/tasks.html#performance-and-strategies). По deadline клиента: [Axios request config](https://axios-http.com/docs/req_config). Выводы о конкретных дефектах сделаны из этого репозитория и локальных тестов, не из общих рекомендаций.

## 14. Priority roadmap

**P0 — исправить немедленно**

- A01: актуальное состояние Wagon под lock; не допустить возвращения физического процесса назад устаревшим запросом.
- A02: не допустить неверную культуру/карантинный маршрут через альтернативный action.
- A03: Task PATCH и актуальные permissions после lock.
- A10: безопасная компенсация законного отрицательного склада.

**P1 — следующим этапом**

- A04/A06: authoritative portal responses, обновление результата внешних оплат.
- A05: не подтверждать устаревшую разметку; закрыть purge/export races.
- A07/A08: строгие входы и атомарные batch actions.
- A11/A13: изоляция background очередей и временной бюджет очистки.
- A12/A14: deadline запросов и наблюдаемые ошибки весов.
- A09: устранить доказанный N+1.

**P2 — хороший рефакторинг**

- A15/A17/A21: lifecycle polling, paging растущих списков, выделение доменных частей больших файлов.
- A16: диагностические запросы и продуманные DB constraints.
- A19/A25: durable cleanup, метрики lock wait, backup generations/restore drill.
- A20: воспроизводимые зависимости и отдельный dependency upgrade.
- A22: исправить документацию и матрицу contracts.

**P3 — optional improvements**

- OpenAPI generation, runtime schemas на наиболее критичных внешних DTO, performance budgets и нагрузочные сценарии.
- A18/A23/A24: сначала согласовать бизнес-значение; не подменять отсутствующие требования предположением.

Прежде чем удалять legacy compatibility, проверить операционные команды и rollback-процедуры. Большой рефакторинг следует проводить после устранения подтверждённых дефектов, а не одновременно со сменой правил продукта.
