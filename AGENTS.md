# AGENTS.md — правила для любой правки кода в asyl-ltd

Обязательны для каждого агента и человека: Claude Code (и его субагенты и
воркфлоу), Codex, Cursor и любой другой. Цель — чистая кодовая база без мусора и
дублей, чтобы не понадобилась ещё одна большая чистка, как 24–25.09.2026
(−55 тыс. строк). `CLAUDE.md` только импортирует этот файл — правила живут здесь.

## 1. Прежде чем писать — найди, что уже есть

- Перед новой функцией, хуком, компонентом, эндпоинтом, константой или
  хелпером **ищи существующую реализацию по смыслу**, а не только по имени:
  `rg` по ключевым словам логики (регулярка номера, формула суммы, текст
  подписи, URL). Нашёл — используй или расширь. **Вторую реализацию того же не
  создавай.** Владельцы тем — в таблице раздела 2.
- Общие места, куда смотреть в первую очередь:
  - backend: `apps/common/*` (plates, locks, heartbeat, daemon,
    openai_responses, pdf, money, query_params, models.SingletonModel,
    pagination, uuids), `orders/debt.py`, `statuses.py`, `labels.py`,
    `transport.py`, `querysets.py`, `refunds.py`, сервисы приложения;
  - frontend: `src/lib/*` (utils, plates, orders, debt-orders, constants,
    focus, use-api, use-paged-api, use-visible-polling, use-confirm-action,
    use-dismiss), `src/components/ui/*` (FormError, ErrorAlert, SearchInput,
    InfoRow, Tabs, Select, DepartmentBadge, CurrencyAmounts, EmptyRow).
- Фронт не пересчитывает то, что отдаёт бэкенд (суммы, долг, остатки, статусы).

## 2. Один модуль — одна ответственность

Модуль (файл, класс, компонент) отвечает за одну тему. Общая логика не
смешивается с фичевой, транспорт — с бизнес-правилами, отрисовка — с расчётами.

### Слои backend

| Слой | Делает | Не делает |
|---|---|---|
| `views.py`, `api_views/*` | HTTP: разбор запроса, права, вызов сервиса, ответ | бизнес-правил, записей в несколько моделей, переходов статусов |
| `serializers.py` | валидация формы данных и форма ответа | расчётов денег/склада, побочных записей |
| `services.py` / тематический модуль (`debt.py`, `sources.py`, …) | бизнес-правила, `transaction.atomic`, `select_for_update` | HTTP, форматирования ответа |
| `querysets.py` | планы запросов (`select_related`, аннотации) | бизнес-решений |
| `management/commands/*`, демоны, Celery-задачи | оркестрация: расписание, ретраи, вызов сервисов | собственной бизнес-логики |
| `apps/common/*` | доменно-нейтральные утилиты | импортов из фичевых приложений |

### Слои frontend

| Слой | Делает | Не делает |
|---|---|---|
| `app/**/page.tsx` | сборка страницы, загрузка данных через хуки `src/lib/use-*` | бизнес-правил, расчётов денег/склада |
| `components/<домен>/*` | отрисовка и UI-состояние своей темы | сетевых вызовов в обход `lib/api.ts`, дублей хелперов |
| `components/ui/*` | общие примитивы без знания домена | импортов из `components/<домен>` |
| `lib/*` | чистая логика, форматирование, хуки | JSX-разметки страниц |
| `lib/api.ts` | только транспорт (запрос, токен, ошибки) | бизнес-логики |

### Кто владеет темой (единственное место правила)

| Тема | Backend | Frontend |
|---|---|---|
| Долг, остаток, оплачено | `orders/debt.py` + `statuses.is_financial` | `lib/debt-orders.ts` (читает поля бэкенда, не пересчитывает) |
| Деньги по валютам (не складывать валюты) | `common/money.py` | `lib/utils.ts` (`formatCurrency`, `sumMoneyByCurrency`), `ui/currency-amounts.tsx` |
| Статусы и подписи заказа | `orders/statuses.py`, `orders/labels.py` | `lib/orders.ts` |
| Транспорт заказа (фура/прицеп/вагон) | `orders/transport.py` | `ui/transport-number-fields.tsx` |
| Номер авто | `common/plates.py` | `lib/plates.ts`, `ui/plate-input.tsx` |
| Номер вагона | `common/wagon_numbers.py` | `ui/wagon-number-input.tsx`, `lib/wagons.ts` |
| Нетто вагона | `Wagon.computed_net_kg` | — |
| Склад и остатки | `warehouse/services.py` | — |
| Час автоприхода AI 24/7 (закрытие смены, с историей) | `cameras/production_runs.py` (`close_time_for`, `set_close_time`), `cameras.AlwaysOnCloseTime` | `components/monoblock/always-on-close-time.tsx` |
| Сводка «Главной» и режим ТВ | `orders/dashboard.py`, `warehouse/services.stock_by_product`, «Приход сегодня» по товарам — `cameras/intake.py`; что показывать (отделы, склады, товары остатка) — `apps/dashboard` | `lib/use-dashboard-metrics.ts`, `lib/dashboard-analytics.ts`, `components/dashboard/*` |
| С какого склада ушли мешки | `shipments/sources.py` | `components/loader/shipment-sources-sheet.tsx` |
| Оплата долга клиента | `orders/debt_payments.py` | `components/payments/client-debt-payment-modal.tsx` |
| Подключения Kaspi (ApiPay) отделов | `sales/apipay_accounts.py`, `sales.ApiPayAccount`; запросы и вебхуки — `orders/apipay.py`, `orders/webhooks.py` | `components/orders/apipay-accounts.tsx`, `apipay-account-form.tsx` |
| Возвраты товара | `orders/goods_returns.py` | `components/storekeeper/*` |
| Магазин сотрудника: привязка и клиент для пополнения | `employees/shop.py`, `employees.EmployeeShop` | `components/employees/employee-shop-fields.tsx` |
| Остатки магазина (допускают минус) и пополнение подтверждённой отгрузкой мельницы | `employees/shop_stock.py`, `employees.ShopStock`; подтверждение и откат — `shipments/services.py` | `app/shop-orders` |
| Внутренние продажи магазина (отдельно от заказов и кассы мельницы) | `employees/shop_sales.py`, `employees.ShopSale`; API — `employees/shop_views.py` | `app/shop-orders`, `components/shop-orders/*`; загрузка — `lib/use-api.ts`, `lib/use-paged-api.ts` |
| Дата продажи и отдельный учёт оплат магазина | `employees/shop_payments.py`, `employees.ShopSalePayment` | `components/shop-orders/shop-order-additions.tsx`, `components/shop-orders/shop-order-history.tsx` |
| Импорт акта сверки 1С | `client_acts/` (`parser.py`, `services.py`) | `lib/client-acts.ts`, `components/clients/client-act-import-modal.tsx` |
| Задним числом / фиксация | `orders/backdate.py`, `orders/fixation.py` | — |
| Права | `sys_permissions/perms.py`, `common/permissions.py` | `lib/can.ts` |
| Параметры запроса (даты, суммы, поиск) | `common/query_params.py` | — |
| Блокировки, heartbeat, демоны | `common/locks.py`, `common/heartbeat.py`, `common/daemon.py` | — |
| Telegram API | `common/telegram.py` | — |
| ИИ-ассистент по данным (только чтение) | `assistant/` (`sql_guard.py` — что можно читать, `queries.py` — READ ONLY, `schema.py`, `knowledge.md`) | `lib/assistant.ts`, `components/assistant/*` |
| Зона видимости (маска) камеры — только для ИИ, людям кадр целиком | хранит и применяет AI-сервис; CRM — прокси `cameras/api_views/visibility_zone.py` (общий `save_polygon`), кэш инвентаря — `services.update_cached_visibility_zone` | `components/camera-visibility-modal.tsx`, `camera-visibility-mask.tsx` |

Новая тема → новый модуль с говорящим именем, строка в эту таблицу в той же
правке. Никаких `utils.py`/`helpers.py`/`misc` внутри фичевых приложений.

### Зависимости

- Приложение импортирует у другого только публичные функции сервиса, не
  `_приватные` хелперы и не внутренности моделей.
- Локальный импорт внутри функции ради обхода цикла — сигнал, что логика лежит
  не в том модуле: перенеси её, а не прячь импорт.
- Хелпер, нужный двум приложениям, переезжает в `apps/common` (если он
  доменно-нейтрален) или в сервис приложения-владельца темы.

### Сигналы смешения (проверь, прежде чем дописывать)

Файл > ~600 строк, функция > ~80 строк, компонент > ~300 строк, view с
несколькими `.save()`/`.create()`. Дели по ответственности, а не по размеру:
большой, но цельный модуль — нормально.

## 3. Не оставляй мусор

- **Удаляешь или заменяешь фичу — удаляй всю цепочку**: эндпоинт, view,
  сериализатор, сервис, queryset, права, пункт меню, страницу/компонент, хук,
  тип в `lib/types.ts`, тесты, доки. Без обёрток «для совместимости», без
  закомментированного кода, без комментариев «удалено», без заглушек.
- После удаления проверь сирот: хелперы, экспорты, импорты, фикстуры, поля
  сериализатора, которые никто не читает. Мёртвое — удали тоже.
- Нет кода «только для тестов» в прод-модулях и лишних `export` (тип, нужный
  только своему файлу, — без `export`).
- Нет отладочных `print`/`console.log`, временных файлов, отчётов и выгрузок в
  репозитории. Временное — в scratchpad/`/tmp`, не в корень проекта.
- Поле модели убирай через expand/contract: из state (`SeparateDatabaseAndState`),
  колонку физически — отдельной миграцией позже (безопасный откат).

## 4. Перед тем как сказать «готово»

Прогони и приведи в порядок то, что затронул:

- backend: `ruff check --select F apps` (без неиспользуемых импортов/переменных),
  `rg` по удалённым/переименованным символам — ноль висящих ссылок,
  `pytest` затронутых приложений (`DB_NAME=asyl_<метка>` — своя тестовая база),
  `manage.py makemigrations --check --dry-run`;
- frontend (из `frontend/`): `npx knip --no-progress` — без новых неиспользуемых
  файлов/экспортов; `npm run check` (prettier → eslint → tsc → vitest)
  и `npm run build` перед пушем.

## 5. Решения, которые принимает только владелец

- **Денежные и отчётные правила** (что попадает в кассу, выписки, долги,
  отчёты), статусы заказа, права доступа — не менять «по пути» и не прятать в
  пачку правок. Каждое такое изменение — отдельный явный вопрос владельцу.
- **Корзина = удалённое.** Заказ в корзине (`deleted_at`) не попадает никуда:
  ни в списки, ни в кассу, ни в выписки, ни в отчёты — даже отгруженный и
  оплаченный. Там, где `order__…` обходит `LiveOrderManager`, фильтруй
  `order__deleted_at__isnull=True` явно.
- Не уверен, что код мёртв (есть живой вызов, внешний потребитель —
  cv-service, ПК камер/весов, вебхуки — или сомнение) — **не удаляй**, спроси.

## 6. Процесс

- Коммит и пуш — только по команде владельца («пушни»), одним коммитом,
  стейджинг явными путями (не `git add -A`: в дереве бывают чужие файлы).
- Параллельные pytest делят базу `test_asyl` — всегда свой `DB_NAME`.
