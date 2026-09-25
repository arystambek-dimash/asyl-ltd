# АСЫЛ-LTD — CRM мукомольного цеха

Внутренняя система учёта цеха «Асыл-LTD»: заказы и оплаты (в т.ч. долги),
склад, отгрузка (Моноблок с камерами и AI-подсчётом мешков, страница грузчика),
приход зерна вагонами и автовесы вывоза, клиентский портал, разграничение
доступа по персональным системным правам и отделам.

- **Бэкенд** (`backend/`): Django + DRF + PostgreSQL + Redis, JWT (simplejwt).
- **Фронтенд** (`frontend/`): Next.js 15 (App Router) + React 19 + Tailwind 4,
  Zustand, Recharts, Radix UI.
- **Видео**: go2rtc (RTSP → WebRTC), доступ через
  nginx `auth_request` + подписанная cookie.
- **Инфраструктура**: Docker Compose, nginx (rate-limit, TLS), Tailscale до
  цехового ПК с камерами и ai_service (`CAMERA_HOST`).

---

## Содержание

1. [Запуск](#запуск)
2. [Структура репозитория](#структура-репозитория)
3. [Архитектура](#архитектура)
4. [Доступы: пользователи, системные права, отделы](#доступы-пользователи-системные-права-отделы)
5. [Бизнес-логика по приложениям](#бизнес-логика-по-приложениям)
   - [orders — заказы и оплаты](#orders--заказы-и-оплаты)
   - [shipments — отгрузка](#shipments--отгрузка)
   - [warehouse — склад](#warehouse--склад)
   - [catalog — товары](#catalog--товары)
   - [clients — клиенты и магазины](#clients--клиенты-и-магазины)
   - [portal — клиентский портал](#portal--клиентский-портал)
   - [cameras — камеры и AI-подсчёт](#cameras--камеры-и-ai-подсчёт)
   - [notifications, eventlog](#notifications-eventlog)
6. [Фронтенд: страницы и механика](#фронтенд-страницы-и-механика)
7. [Инфраструктура и деплой](#инфраструктура-и-деплой)
8. [Тесты](#тесты)

---

## Запуск

### Docker (вся система)

```bash
docker compose up --build
```

- Фронтенд: <http://localhost:3000>
- API: <http://localhost:8000/api>
- Django-админка: <http://localhost:8000/admin>

При старте бэкенда `entrypoint.sh` ждёт PostgreSQL, применяет миграции и
идемпотентно создаёт суперпользователя (`create_superuser_env`).
Камеры локально не выключены: при пустом `CAMERA_HOST` settings подставляют
Tailscale-IP боевого ПК цеха (`backend/config/_settings/base.py`). Чтобы не
ходить на прод-камеры, задайте в `.env` свой хост, например
`CAMERA_HOST=host.docker.internal` (мок на маке).
Автоматика автомобильных весов также не стартует в обычном dev-стеке:
профиль `hardware` включают только явно после настройки тестовых URL:

```bash
docker compose --profile hardware up --build
```

### Разработка без Docker

```bash
# Бэкенд
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
pytest

# Фронтенд
cd frontend
nvm use                     # Node 22 из .nvmrc
npm ci
npm run dev                 # http://localhost:3000
```

---

## Структура репозитория

```
backend/
  config/            # settings, urls, throttles, exception handler
  apps/
    common/          # общие DRF-права (IsStaff, HasPerm, PermViewSetMixin…)
    accounts/        # User (is_client, perm_codes), /auth/login|refresh|me
    sys_permissions/ # Permission и единый каталог кодов системных прав
    employees/       # Employee: профиль User + персональные права
    clients/         # Client, Store; долги, история, выписки
    sales/           # Department: динамические отделы продаж
    catalog/         # Product (+архив), ClientPrice
    orders/          # Order, OrderItem, Payment, StatusChangeRequest
    shipments/       # Shipment: отгрузка, страница грузчика, накладная, вагон
    warehouse/       # StockItem, StockReceipt, StockMovement
    portal/          # клиентский портал: каталог, заказы, регистрация
    notifications/   # уведомления клиентам
    eventlog/        # неизменяемый журнал событий (log_event)
    cameras/         # go2rtc, AI-подсчёт, health-мониторинг, алерты
    grain/           # приход зерна вагонами, силосы, автовесы вывоза
    bots/            # WhatsApp-бот отчётов о вагонах (Green-API)
    tasks/           # задачи сотрудников
frontend/
  src/app/           # страницы (App Router), см. раздел «Фронтенд»
  src/components/    # ui-кит, layout (sidebar/topbar), доменные компоненты
  src/lib/           # api-клиент, can(), типы, форматтеры, хуки
  src/store/auth.ts  # Zustand: useAuth (me, login, logout)
deploy/              # nginx, go2rtc.yaml, remote-deploy.sh, health-гейты, backup
docker-compose.yml / docker-compose.prod.yml
```

---

## Архитектура

```
Браузер (сотрудник / клиент портала)
    │  JWT: Authorization Bearer + refresh
    ▼
nginx :443  ── rate-limit (30 r/s API, 10 r/m login), TLS, security-headers
 ├── /            → frontend (Next.js :3000)
 ├── /api/        → backend  (Django+gunicorn :8000)
 ├── /go2rtc/     → go2rtc :1984   (auth_request → /api/cameras/auth/,
 │                                   проверка подписанной cookie cam_token)
 └── /static, /media
backend ──► PostgreSQL (данные)  ──► Redis (кэш discover_cameras и пр.)
backend ──► ai_service :8890 на цеховом ПК (через Tailscale) — AI-подсчёт мешков
camera-monitor (отдельный контейнер) — непрерывный probe камер, инциденты, алерты
passage-scale-monitor (отдельный контейнер) — импорт очереди сборщика весов вывоза
остальные фоновые процессы — см. «Прод-состав»
```

Ключевые сквозные принципы:

- **Каждое значимое действие логируется** в `eventlog` через `log_event(...)`
  (оплаты, статусы, погрузка, склад, архив товаров, долги).
- **Права** — прямые системные permissions по строковым кодам (`orders.confirm`),
  а не Django-группы. Проверка на бэке (`HasPerm`) и на фронте (`can()`).
- **Отделы продаж** — динамический справочник: сотрудника можно закрепить за
  отделом. Permissions назначаются отдельно. Доступ к заказам и клиентам
  ограничивается текущим отделом клиента через `sales/access.py`; снимок
  `Order.department` используется в фильтрах и отчётах, но не заменяет этот scope.
- **Мягкое удаление**: заказы — в корзину (`deleted_at`), товары — в архив
  (`is_active=False`). Операционные списки скрывают удалённые заказы; денежная
  история сохраняется и отдельные финансовые выборки используют `all_objects`.

---

## Доступы: пользователи, системные права, отделы

### accounts

`User` наследует `AbstractUser` + флаг **`is_client`** (клиент портала).

- `perm_codes` — set кодов прав: суперюзер → все; сотрудник → его прямые
  permissions; клиент → пусто.
- `has_perm_code(code)` — точечная проверка.

Эндпоинты: `POST /api/auth/login/` (throttle 10/мин), `POST /api/auth/refresh/`,
`GET /api/auth/me/` → id, username, permissions, position,
sales_department.

### common/permissions.py — общие DRF-права

| Класс | Пропускает |
|---|---|
| `IsStaff` | авторизованный сотрудник (не клиент) |
| `IsClientUser` | авторизованный клиент портала |
| `IsSuperUser` | только суперадмин |
| `HasPerm(*codes)` | сотрудник хотя бы с одним из кодов |
| `PermViewSetMixin` | миксин: `required_perms = {action: код или кортеж}` → `HasPerm`; неизвестный action закрывается через `DenyAll` |

### sys_permissions — коды прав

Модель: `Permission(code, section, action, label)`. Права назначаются сотрудникам напрямую.
Единственный источник кодов — `backend/apps/sys_permissions/perms.py`: разделы
совпадают со страницами меню, подписи прав — там же. Таблица ниже — выжимка.

| Раздел | Коды |
|---|---|
| Отчёты | `reports.view / export` |
| Заказы | `orders.view / create / edit / confirm / confirm_all / correct_price / rollback` |
| Касса | `payments.view / create / confirm` |
| Моноблок | `monoblock.view` |
| Грузчик | `loader.view / confirm / trucks / wagons` |
| Склады / Силосы | `warehouse.view / adjust`, `silos.view` |
| Приход и вывоз | `grain.view / supply / arrive / weigh / correct_weighing / inventory / delete / admin` |
| Клиенты | `clients.view / create / edit / delete / set_price / manage_access` |
| Магазины | `stores.view / create / edit / delete` |
| Товары | `catalog.view / create / edit` |
| Задачи | `tasks.view / create` |
| Журнал | `events.view` |
| WhatsApp-бот | `bots.view / manage` |
| Сотрудники / Администрирование | `employees.view / manage`, `sys_permissions.manage` |

Ролей и наследования прав нет: итоговый доступ сотрудника равен его прямому
набору `Employee.permissions`. Отдел хранит организационную принадлежность и
не добавляет permissions автоматически.

### employees

`Employee(user OneToOne, permissions M2M, is_active)` хранит прямые права.
Создание сотрудника —
одна транзакция: `User` + `Employee` + права по кодам (`permission_codes`).

---

## Бизнес-логика по приложениям

### orders — заказы и оплаты

#### Статусы заказа

```
draft → pending → confirmed → arrived → loading → loaded → shipped
          └→ rejected            (любой нефинальный → cancelled)
```

Заявку переводят только `confirm_order` (из `draft`/`pending`) и `reject_order`
(из `pending`): каждый сам проверяет исходный статус под блокировкой заказа.
Дальше заказ ведут сервисы отгрузки (`shipments`) и административная смена статуса.

| Статус | Смысл |
|---|---|
| `draft` | черновик, свободно редактируется |
| `pending` | заявка ждёт подтверждения (цены — у бухгалтера/кассы) |
| `confirmed` | подтверждён, цены зафиксированы, ждёт машину/вагон |
| `arrived` | машина отмечена прибывшей (старые заказы бывшего поста) |
| `loading` | идёт AI-погрузка (счёт мешков) |
| `loaded` | погрузка завершена |
| `shipped` | выехал; товар списан со склада |
| `rejected` / `cancelled` | отклонён / отменён |

Ключевые поля `Order`: `payment_status` (`unpaid/partial/settled`),
`settlement_intent` (`debt` — основной путь ~90% заказов / `instant`),
`debt_requested` (клиент попросил долг), `department` (денормализован из клиента),
`transport_type` (`truck/train`), `truck_number(+_set_by)`, `store`,
`loading_camera` (какая камера занята под погрузку), `deleted_at/_by`
(корзина). Менеджеры: `objects` — только живые, `all_objects` — с удалёнными.

Вычисляемое: `total_amount`, `paid_total` (только подтверждённые оплаты),
`remaining_amount`, `is_fully_paid`,
**`is_debt` = shipped + остаток > 0** — любой неоплаченный остаток отгруженного
заказа, способ расчёта не важен. Суммы долга и выручки в отчётах считаются
только через `orders/debt.py` и `statuses.is_financial`.

#### Оплаты (`Payment`)

Способы приёма в кассе (`Payment.CASHIER_METHODS`): `cash / kaspi / remote /
invoice`; служебный метод `debt` деньгами не считается. Цепочка статусов:

```
requested (счёт выставлен) → received (деньги на руках) → confirmed (касса подтвердила)
                                                        ↘ rejected
```

Каждый шаг фиксирует автора и время (`recorded_by`, `received_by/_at`,
`confirmed_by/_at`). Клиенту видны только подтверждённые (in-progress статусы —
внутренние).

#### Функции сервисов (`orders/services.py`)

| Функция | Что делает |
|---|---|
| `_validate_payment_open(order, method)` | статус заказа допускает оплату этим способом (`statuses.is_payment_open`: после `shipped` — любым способом, до отгрузки касса принимает предоплату только `cash / kaspi / remote`); у отгруженного заказа магазина дополнительно проверяется его платёжный день |
| `add_payment(order, amount, user, method, stage)` | старт цепочки (`requested` или сразу `received`) |
| `receive_payment` / `accountant_confirm_payment` / `reject_payment` | шаги цепочки; подтверждение пересчитывает `payment_status` заказа |
| `create_client_payment(order, method, user)` | оплата из портала (card/kaspi) на весь остаток, `update_or_create` от двойных кликов |
| `sync_payment_status(order)` | идемпотентный пересчёт `unpaid/partial/settled` |
| `request_client_debt` / `move_order_to_debt` | клиент просит долг из портала / касса согласует долг: intent=`debt`, остаток остаётся долгом клиента |
| `confirm_order(order, user, prices)` | draft/pending → confirmed + фиксация цен позиций |
| `apply_item_prices` | проставить `unit_price` позиций и запомнить прайс клиента в `ClientPrice`; без цены > 0 — ошибка |
| `replace_items(order, items, prices, user)` | замена позиций (только в `draft/pending/confirmed/arrived`), с блокировкой от гонки со стартом погрузки и проверкой склада |
| `can_set_truck_number(order, user)` | номер транспорта (тягач + прицеп) меняет тот, кто его ввёл: номер клиента — только этот клиент, номер сотрудника — любой сотрудник; сама смена — `transport.set_order_transport` (уведомляет клиента) |
| `request_status_change` / `approve_ / reject_status_change` | ручная смена статуса: с правом `orders.edit` — сразу, без — создаётся `StatusChangeRequest` на одобрение |
| `soft_delete_order` / `restore_order` | корзина: `deleted_at` ставится/чистится |

#### Эндпоинты (`/api/orders/…`)

CRUD + действия: `confirm` (+ `confirm-context`), `reject`, `correct-price`,
`fixate` (заказ задним числом), `transport` (тягач + прицеп),
`payments` (+ `receive/confirm/reject/reopen` по оплате; восстановление
отклонённой — `/api/payment-transactions/{id}/restore/`), очереди кассы
`payments-queue`, `awaiting-payment`, `to-refund`, `to-debt`
(касса согласует долг), `set-status` (+ `status-requests/approve|reject`),
`rollback-shipment`, `trash` / `trash-preview` / `restore` / `purge` (корзина);
сводки `dashboard-operational`,
`department-summary`, `transport-queue`, `awaiting-shipment`,
`shipping-calendar`, `form-options`. Права — см. `required_perms` во
`views.py`; списки скоупятся по отделу.

### shipments — отгрузка

`Shipment` (OneToOne к заказу): `truck_number`, нейтральный учётный
`weigh_in_kg`, `bags_loaded`, `arrived_at`,
`loading_started_at`, `shipped_at`.

Основной путь — страница грузчика: `loader_dispatch` / `dispatch_order`
(кнопка «Отгружено») отгружает подтверждённый, ещё не выехавший заказ на
заказанное количество без въезда и счёта мешков; номер накладной — номер заказа.
Вагонный заказ отгружается и по отчёту о вагонах (`ship_rail_report`: мешки
отчёта сверяются с заказом, вагоны записываются в `ShipmentWagon`).

AI-погрузка начинается через `begin_camera_loading` (камера закрепляется за
заказом, →loading) и завершается `finish_ai_counting` (→loaded); выезд —
та же кнопка «Отгружено». `rewind_loading` (административная смена статуса)
возвращает незавершённую погрузку в ожидание и освобождает камеру.

Monoblock/AI работает только с заказом, камерой, числом мешков и
переходом `loading→loaded`; номер машины и физические весы для его запуска не
нужны. Интеграция автовесов принадлежит отдельному приложению `grain`.

Общий финал `_do_ship`: списывает каждую позицию со склада
(`deduct_stock` — по факту можно уйти в минус),
ставит `shipped_at`, `status=shipped`, пересчитывает `payment_status` по
факту денег (предоплата сохраняется), логирует `shipment` и `debt` при
остатке > 0. Неоплаченный остаток отгруженного заказа — долг клиента, деньги
закрываются через кассу.

Эндпоинты — страница грузчика `/api/loader/…` (`queue`, `history`,
`orders/{id}/dispatch|rollback|waybill`, `waybill-settings`, `rail-report/…`,
`wagon-report/…`); смотреть — `loader.view`, отгружать — `loader.confirm`.

### warehouse — склад

- `StockItem(warehouse, product, bags)` — остаток с уникальной парой склад/товар; может быть отрицательным
  (списание в минус при отгрузке).
- `StockReceipt` — акт приёмки; `StockMovement` — история каждого движения
  (`delta`, `balance_after`, `reason: adjustment/receipt/shipment`).

Сервисы (все под `select_for_update`/`F()` — безопасны от гонок):

- `ensure_products_available(products)` — товар заказываем только при
  `stock.bags > 0` (проверка при создании/редактировании заказа);
- `adjust_stock(product, delta, user, note)` — корректировка, минус запрещён;
- `receive_stock(product, bags, user)` — приёмка;
- `deduct_stock(product, bags, user)` — списание при отгрузке; уход в минус
  логирует предупреждение `stock_negative`.

Эндпоинты: `GET /api/stock/`, `POST …/adjust`, `POST …/transfer`,
справочник `/api/warehouses/` — список, создание и правка, без удаления
(права `warehouse.view` / `warehouse.adjust`).

### catalog — товары

`Product(name, color: Red/Green/Blue, weight_kg: 2/5/10/25/50, is_active,
photo)`, уникальность `(name, color, weight_kg)`. Своей цены у товара нет:
цена берётся из прайса клиента (`ClientPrice`) или вводится в заказе.
`cv_class` → `"Red_50"` — класс для AI-классификации мешков на видео.

`ClientPrice(client, product, price)` — запомненный прайс клиента,
обновляется при подтверждении заказа; `GET /api/client-prices/?client=`
предзаполняет цены в форме заказа.

**Архив вместо удаления**: `DELETE /products/{id}` вызывает
`archive_product` (`is_active=False`); есть явные `POST …/archive` и
`…/restore`. Фильтр архива — в `get_queryset` (`?archived=1`), а не в
default-менеджере, чтобы старые заказы и отчёты видели архивные товары.

### sales — отделы продаж

- `Department(code, name, color, is_active, is_default)` — динамический
  справочник, используемый сотрудниками и заказами; API `/api/departments/`.

### clients — клиенты и магазины

- `Client`: телефон, реквизиты (ИИН, банк, счёт), предпочтительная валюта и
  обязательная `user`-учётка портала; имя и фамилия хранятся только в `User`.
- `Store` (магазин клиента): `payment_schedule_type` (`none/monthly/weekly`) +
  `payment_days` (дни месяца или ISO-дни недели) — расписание платежей.

Сервисы:

- `is_payment_window_open(store, date)` — открыто ли окно оплаты сегодня;
- `detect_overdue(store, date)` — если окно открыто и есть отгруженные
  неоплаченные заказы — шлёт уведомление клиенту (кнопка «Проверить
  просрочки» в кассе);
- `client_history(client)` — продажи, погашения и долги клиента плоскими
  строками для карточки клиента.

Эндпоинты: CRUD клиентов (`/api/clients/`) и магазинов (`/api/stores/`),
`POST /clients/{id}/password/` для выдачи временного пароля,
`GET /clients/{id}/history/`, `GET /clients/{id}/statement/` и
`GET /clients/statement/` (выписки Excel), `GET|PUT /clients/{id}/prices/`,
`GET /clients/picker/`, `POST /clients/{id}/assign-department/`,
`POST /clients/{id}/purge/`, `GET /clients/debts/`,
`GET /clients/{id}/debt-detail/`,
`POST /stores/check-overdue/`.
Новый клиент получает уникальный логин, отключённую учётку и unusable password.
Сотрудник с `clients.manage_access` включает доступ действием «Выдать доступ в портал»:
пароль не логируется, а при первом входе клиент обязан заменить его.

### portal — клиентский портал

Для пользователей с `is_client=True` (учётка привязана к `Client.user`).

- `POST /api/portal/register/` — самостоятельная регистрация
  (throttle 5/мин): транзакция `User(is_client=True)` + `Client`, сразу
  возвращает JWT.
- `GET /api/portal/catalog/` — активные товары с остатками.
- `GET/POST /api/portal/orders/` — свои заказы; создание: позиции,
  `settlement_intent`, `transport_type`, магазин.
  - `POST …/{id}/pay/` — запуск оплаты через ApiPay;
  - `PATCH …/{id}/truck/` — вписать номер машины (только в `confirmed`);
  - `POST …/{id}/request-debt/` — запросить долг (только в `shipped`).
- **Маскирование денег**: суммы видны клиенту только когда заказ прошёл
  подтверждение (не в `draft/pending/rejected/cancelled`); внутренние стадии
  оплат клиенту не показываются.
- `GET /api/portal/stores/` — магазины своего клиента.
- `GET /api/portal/notifications/` + `POST …/{id}/read/` — уведомления.

### cameras — камеры и AI-подсчёт

Windows runtime, модели, тесты и установщик AI-сервиса принадлежат отдельному
репозиторию
[`bag-counter-cv-service`](https://github.com/arystambek-dimash/bag-counter-cv-service).
Этот репозиторий хранит только CRM-клиент его HTTP-контракта и серверную
оркестрацию; исходники edge-сервиса сюда не копируются. Агент ПК камер
(надзор за MediaMTX, NVR-sync, запись `cam…ai`) тоже ставится только оттуда —
по runbook `deploy/camera-pc/README.md` того репозитория.

Камера, назначенная активному устройству «Моноблок», автоматически входит в
обязательную политику AI 24/7 с логическим источником `sub`. Физический RTSP URL
и пароль камера-сайтом не хранятся: на Windows camera-PC оператор один раз
сопоставляет `camN` прямому substream камеры, после чего MediaMTX публикует его
как `camNsub`. Поэтому назначение в интерфейсе включает уже подготовленный
прямой поток, а не NVR-канал. Если сопоставления или свободной AI-ёмкости нет,
API возвращает `pending` и интерфейс не должен показывать камеру как успешно
запущенную.

> **Важно — maintenance barrier и порядок выкладки.** До обновления временно
> запретите новые старты Моноблока и убедитесь, что нет ни одной открытой
> `AiCountingSession` (`STARTING`/`ACTIVE`) и ни одного заказа с активной
> `loading_camera`. Не переходите границу версий, пока эти списки не пусты:
> старый Asyl может открыть на новом CV непрерывную сессию без durable
> `session_id`, которую новый Asyl уже не сможет безопасно завершить. После
> полного drain сначала обновите camera-PC/CV service, проверьте прямые потоки
> `camNsub` и только затем выкладывайте этот backend и снова разрешайте старты.
> Запуск заказа требует `continuous_analytics=true` и точный совпадающий
> `session_id`; со старым CV backend намеренно завершит запрос ошибкой, а не
> запустит холодный счётчик.

#### Живой просмотр

- `POST /api/cameras/token/` (сотрудник) — ставит подписанную HttpOnly-cookie
  `cam_token` (TimestampSigner, 12 часов, path `/go2rtc/`).
- nginx на `/go2rtc/` делает `auth_request` → `GET /api/cameras/auth/`,
  который валидирует cookie (204/403). Так браузер смотрит потоки go2rtc,
  не имея прямого доступа к нему.
- `GET /api/cameras/` → `discover_cameras()`: основной путь — живой инвентарь
  от ai_service (`GET /cameras`: каналы NVR + direct-камеры по MAC; камеры
  без доступа «locked» скрываются), динамические потоки досоздаются в go2rtc
  через его API; резервный путь — параллельные RTSP-пробы cam1..camN.
  Кэш в Redis: 240 с рабочий, 7 дней last-good (fallback при сбое сети).

#### AI-подсчёт мешков (Моноблок)

Обязательные камеры Моноблока считаются непрерывно, даже когда заказа нет.
Запуск отгрузки не создаёт второй decoder/model runtime и не обнуляет общую
аналитику: заказ с устойчивым `session_id` присоединяется к уже работающему
`always_on`-процессору и видит только дельту от момента старта. Пересечения во
время заказа одновременно остаются в AI 24/7 с признаком
`continuous_analytics`. Холодный session-процессор для камеры Моноблока контракт
не принимает; после перезапуска camera-PC тот же `session_id` восстанавливает
сохранённую базовую точку.

Модель `AiCountingSession(order, camera, status: STARTING/ACTIVE/CLOSED/FAILED,
final_total, last_status JSON)` +
**UniqueConstraint: на камере максимум одна
открытая сессия** — камеру нельзя занять двумя заказами даже при
одновременных запросах.

Жизненный цикл (`sessions.py`): `reserve` (атомарный захват слота) →
`activate` → `update_status` (поллинг) → `finish` (сохраняет финальный
счётчик) / `fail`. Потеря AI-воркера не освобождает слот автоматически:
владение остаётся за заказом до явной сверки, чтобы новый заказ не получил
старый или обнулённый счёт.

HTTP-эндпоинтов старта, стопа и сброса подсчёта нет: открытую сессию закрывает
отгрузка грузчика (`counting.close_session_for_dispatch` — активный подсчёт
идущей погрузки завершается с мешками камеры, остальной отменяется). Открытые
сессии для Моноблока — `GET /api/cameras/ai/sessions/` (право `monoblock.view`).

Поздние события мешков: после перезапуска camera-PC догрузка пропуска может
принести мешки смены, которая уже проведена на склад. `cameras/event_sync.py`
не отклоняет такую страницу (иначе журнал камеры замерзает и health-гейт
деплоя падает): мешок попадает в ещё открытую дневную аналитику,
импортированная строка остаётся с `applied_to_production=False`, в лог уходит
предупреждение с камерой и количеством. Проведённая партия не меняется.

#### Health-мониторинг и алерты

Отдельный контейнер **camera-monitor** (`manage.py monitor_cameras`) раз в
~30 с делает end-to-end пробы: инвентарь ai_service, каталог go2rtc,
RTSP DESCRIBE каждого потока, выборочный JPEG-кадр через go2rtc. Состояние —
в PostgreSQL (`CameraHealthState` singleton, `CameraIncident`):
статусы HEALTHY/DEGRADED/OUTAGE с дебаунсом (3 плохих подряд — инцидент,
2 хороших — восстановление). Алерты — webhook и/или Telegram
(`CAMERA_ALERT_*` env), с ретраями и аудитом доставки.
`manage.py check_camera_health` — гейт для деплоя.

### notifications, eventlog

- `Notification(client, text, is_read)` — создаются сервисом
  `notify(client, text)` из orders/clients (смена статусов, просрочка);
  клиент читает в портале (колокольчик).
- `EventLog(event_type, message, user, order, payload JSON)` — неизменяемый
  журнал (повторное сохранение/удаление запрещены). Пишется через
  `log_event(...)` из всех сервисов: `payment`, `status`, `status_override`,
  `arrival`, `loading_start`, `loading`, `loading_done`, `shipment`, `debt`,
  `debt_override`, `stock_adjust`, `receipt`, `stock_negative`, `catalog`,
  `order`, `order_edit`. Чтение: `GET /api/events/` (право `events.view`),
  фильтры по типу, заказу, тексту, датам.

---

## Фронтенд: страницы и механика

### Страницы (App Router)

| Роут | Что делает |
|---|---|
| `/login`, `/register` | вход (JWT в localStorage), регистрация клиента |
| `/dashboard` | вкладки «Аналитика» (KPI: склад, отгрузки за 14 дней, выручка/поступления, долги; графики; live-очередь отгрузки; топ должников) и «Камеры» (стена камер) |
| `/orders` | вкладки «Заказы» / «Корзина» (восстановление удалённых); поиск, фильтры по статусу/отделу; создание и редактирование через `OrderForm` |
| `/orders/[id]` | деталь заказа: позиции, цепочка оплат (`PaymentChain`), номер машины, действия по статусу |
| `/accounting` | «Касса», экран выбирается `?view=`: на десктопе `overview` («Общее»/«Долги»: аналитика кассы, должники, «Проверить просрочки»), `confirm` («Оплаты»), `transactions`; на телефоне ещё `home`, `debts`, `report`, `pos` (Kaspi QR) и `remote` (удалённый счёт) |
| `/accounting/debts/clients/[id]` | детализация долга клиента |
| `/clients`, `/clients/[id]`, `…/prices` | база клиентов; карточка клиента — вкладки «Аналитика клиента», «Продажи», «Погашения», «Долги» (`/clients/{id}/history/`) и выписка Excel; цены клиента |
| `/stores` | магазины клиентов, графики оплат (нет/еженедельно/ежемесячно) |
| `/catalog/products` | вкладки «Товары» / «Архив»; архивирование вместо удаления; флаг «спрашивать вес грузовика» |
| `/warehouse` | остатки; корректировка/приёмка с быстрыми кнопками и превью «сейчас → станет» |
| `/warehouse/silos` | силосы (право `silos.view`) |
| `/monoblock` | Моноблок (только просмотр, `monoblock.view`): очередь машин и вагонов, камеры и AI-подсчёт; печать сегментов отгрузки |
| `/loader` | Грузчик: вкладки «Фуры» / «Вагоны» по правам `loader.trucks` / `loader.wagons`, очередь «К отгрузке» и «История», кнопка «Отгружено», накладная PDF, отчёты о вагонах |
| `/grain`, `/grain/passages`, `/grain/wagons/[id]`, `/grain/orientation` | приход зерна вагонами, автовесы вывоза (рейсы, накладная рейса), разметка ориентации |
| `/tasks` | задачи: свои — каждому сотруднику, всех — с `tasks.view` |
| `/shipping` | устаревшая ссылка; сервер перенаправляет на `/monoblock` |
| `/reports` | выручка и поступления по валютам, период и фильтр по отделу |
| `/management/employees` | сотрудники, отделы и персональные системные права |
| `/management/whatsapp-bot` | журнал WhatsApp-бота отчётов о вагонах: проведённые и ждущие человека |
| `/events` | журнал событий с фильтрами, группировка по дням |
| `/portal/catalog`, `/portal/cart`, `/portal/orders`, `…/[id]` | портал клиента: каталог с остатками, корзина (старый адрес `/portal/orders/new` перенаправляется сюда), свои заказы, оплата, номер машины, запрос долга |

### Механика

- **Auth**: axios-интерцептор добавляет `Bearer`, на 401 — одиночный
  refresh (без гонок), на неудачу — logout и `/login`. Стор `useAuth`
  (Zustand): `me`, `login`, `loadMe`, `refreshMe` (тихое обновление прав).
- **Права**: `can(me, code)`; `<RequirePerm code=…>` закрывает страницу
  заглушкой «Нет доступа»; сайдбар строится из прав; `homeFor(me)` разводит
  по домашним страницам (клиент → `/portal/catalog`, сотрудник → `/dashboard`).
- **UI-кит** (`components/ui`): Button/Input/Select/Modal/ConfirmDialog,
  Table + SortableHeader, Badge/StatusBadge/PaymentStageBadge, KPI-карточки,
  PlateInput (госномер одним полем: флаг страны, маски тягача и прицепа),
  DataState (loading/error/empty), Tabs.
  Тема light/dark/system. Паттерны дизайна — Stripe/Linear/UniFi.
- **Камеры**: `CameraWall`, `CameraStream` (WebRTC от go2rtc).

---

## Инфраструктура и деплой

### Прод-состав (`docker-compose.prod.yml`)

| Сервис | Роль |
|---|---|
| `nginx` | вход: TLS (certbot), rate-limit (API 30 r/s burst 90; login/admin 10 r/m; conn-limit 20–30/IP), таймауты против Slowloris, security-headers (HSTS, X-Frame-Options DENY), `auth_request` для `/go2rtc/` |
| `backend` | gunicorn: 3 воркера, `--max-requests 1000` (+jitter), лимиты размера запроса |
| `frontend` | Next.js standalone |
| `go2rtc` | 32 статических слота cam1..cam32 + динамические потоки от бэкенда; ffmpeg-транскод только если кодек не H.264 |
| `camera-monitor` | тот же образ backend, `manage.py monitor_cameras` |
| `ai-stock-monitor` | тот же образ backend, `manage.py post_always_on_stock`; приходует на склад завершённые смены AI 24/7 |
| `shipping-transport-monitor` | тот же образ backend, `manage.py monitor_shipping_sessions`; группирует счёт отгрузки в сессии и простои |
| `whatsapp-bot` | тот же образ backend, `manage.py run_whatsapp_bot`; отчёты о вагонах из Green-API, без `WHATSAPP_BOT_ENABLED=1` простаивает |
| `passage-scale-monitor` | тот же образ backend, `manage.py monitor_passage_scale`; импортирует очередь независимого сборщика весов (`deploy/weighbridge/`, при маркере `/var/lib/weighbridge/enabled`), досылает фото, проверяет номера и ведёт вагонную арку |
| `celery-payments` | Celery worker только очереди `payments`, concurrency/prefetch = 1; сверка ApiPay |
| `celery-orientation` | отдельная очередь `orientation`, concurrency/prefetch = 1; экспорт разметки и фото на Camera-PC |
| `celery-beat` | периодически ставит сверку ApiPay в Redis с expiry; schedule/pid живут в отдельном tmpfs |
| `db` / `redis` | PostgreSQL 16 / Redis 7 — в изолированной internal-сети `data` |
| `db-backup` | ежедневный `pg_dump` + бэкап перед каждым деплоем |

Сети изолированы: `edge` (nginx↔front/back), `data` (db/redis), `default` —
фронт не имеет доступа к БД и наружу.

### Деплой (`deploy/remote-deploy.sh`)

CI проверяет frontend/backend и инфраструктурные инварианты. Успешный push
`main` запускает выпуск; ручной запуск также требует успешного последнего CI
для того же commit. Образы frontend/backend собираются параллельно, затем
на сервер передаются их digest. Отменённый CI нужно перезапустить, а не
обходить ручным деплоем. Python dependency audit остаётся информативным,
но его ошибка теперь видна отдельным warning.

1. Только **immutable digest** образов (`ghcr.io/...@sha256:…`) — `:latest`
   отклоняется; flock от параллельных деплоев.
2. `git pull --ff-only` → бэкап БД → `docker compose pull` →
   остановка старых camera/scale writers → `up -d --wait`. У backend
   healthcheck проверяет GET `/api/auth/me/`, у `passage-scale-monitor` —
   свежий container-private heartbeat цикла. `degraded` из-за внешних
   весов/камеры считается живым процессом и не вызывает rollback.
3. **Camera health** проверяется workflow после запуска контейнеров:
   `wait-for-camera-health.sh` требует свежий (не старше
   `CAMERA_HEALTH_STALE_SECONDS`) heartbeat нового релиза и готовый журнал
   событий (`--require-events`). Провал проверки приводит
   к rollback; эти проверки не отключаются ради зелёного статуса выпуска.
4. `nginx -t && nginx -s reload` (graceful).

Замечания по прод-хостингу (ps.kz): сервер может внезапно ребутнуться —
деплой и проверки написаны с ретраями; троттлинг DRF выключен под pytest;
go2rtc rate-limit'ить нельзя (живое видео).

### Наблюдаемость

- **Backend Sentry** включается только непустым `SENTRY_BACKEND_DSN`. События
  получают единые `APP_RELEASE`, `APP_ENVIRONMENT` и тег `APP_SERVICE`;
  web-процесс и каждый monitor-контейнер имеют своё имя сервиса. По умолчанию
  ошибки отправляются, а tracing/profiling выключены (`…_SAMPLE_RATE=0`).
- SDK не отправляет default PII, тела/заголовки HTTP-запросов, query params,
  cookie, database query data, локальные переменные и source-context строки
  stack frames. Query string и fragment рекурсивно удаляются из URL-подобных
  значений event, breadcrumb, transaction/span и structured log;
  `request.headers`, cookies и
  query string, всё request body/form/files и response body/data дополнительно
  удаляются fail-closed перед отправкой. В остальных
  вложенных данных нормализованные поля паролей, токенов, private/API keys,
  credentials, Authorization, ApiPay и camera/AI credentials
  рекурсивно заменяются на `[Filtered]`. Это страховка, а не повод писать
  секреты в сообщения логов — строку уже сформированного сообщения невозможно
  надёжно очистить по имени поля.
- В production Django пишет по одному JSON-объекту на строку stdout с полями
  `timestamp`, `level`, `logger`, `message`, `exception`, `service`,
  `environment`, `release`. Локально формат остаётся читаемым; для JSON можно
  задать `LOG_FORMAT=json`. Gunicorn access log выключен, потому что raw request
  target содержит query string до применения privacy-фильтров; error log
  остаётся на stderr. Docker хранит только `10m × 3` на сервис: это
  ограниченная локальная диагностика, а не долговечное централизованное
  хранилище. Подключение удалённого sink остаётся отдельной инфраструктурной
  операцией.
- **Sentry Logs** не включаются вместе с error tracking автоматически.
  `SENTRY_ENABLE_LOGS=1` (и отдельные frontend-флаги) разрешается только после
  проверки privacy и бюджета ingestion: объём рабочих monitor-логов значительно
  выше объёма исключений.
- **Frontend Sentry** только браузерный: публичный
  `NEXT_PUBLIC_SENTRY_DSN` встраивается при сборке, необработанные browser/React
  ошибки и оба App Router error boundary отправляются напрямую из браузера.
  Axios-ошибки глобально не перехватываются, чтобы частые poller-сбои не создавали
  шторм событий. Replay и profiling выключены, tracing по умолчанию равен 0.
- Source maps загружаются только когда build одновременно получил
  `SENTRY_ORG`, `SENTRY_PROJECT` и `SENTRY_AUTH_TOKEN`. Токен передаётся в
  `frontend/Dockerfile` как BuildKit secret `sentry_auth_token`, не как build arg
  или ENV, и не попадает в образ. После успешной загрузки карты удаляются из
  `.next`.
- Production release — полный Git SHA. Deploy-скрипт экспортирует его как
  `APP_RELEASE` (с `EXPECTED_SHA` как fail-safe fallback в Compose), а frontend
  build получает тот же SHA как `NEXT_PUBLIC_APP_RELEASE`, чтобы
  backend/browser события и source maps совпадали. При rollback скрипт
  экспортирует SHA предыдущего checkout вместе с предыдущими digest images.

После включения проектов в Sentry нужно создать минимум два внешних правила:
уведомление о новой/regressed production issue и внешний uptime-check публичных
frontend/API адресов. Репозиторий не может доказать состояние этих правил в
Sentry, поэтому выпуск observability считается проверенным только после
синтетического browser/backend exception и тестового health outage. Camera
DEGRADED/OUTAGE/RECOVERY по-прежнему доставляются независимо через существующие
`CAMERA_ALERT_*` webhook/Telegram с durable retry-аудитом.

### Throttling (уровень Django)

`anon 60/мин`, `user 600/мин`, `login 10/мин`, `register 5/мин`
(config/throttles.py, поверх nginx-лимитов). Единый обработчик ошибок
(`config/exceptions.py`) нормализует ответы к `{"detail", "code"}`; durable
физические операции дополнительно возвращают `request_id`, состояние и
`retryable`.

### Номера автомобилей с camera-PC

Для вывоза действует weight-first цепочка: Asyl фиксирует один стабильный вес,
после чего idempotent POST включает on-demand распознавание камеры и потока из
`VEHICLE_PLATE_WEIGHT_FIRST_CAMERA/SOURCE` внутри сохранённого ROI. Номер, вес
и переходы рейса коммитятся одной транзакцией;
повтор того же UUID не читает весы снова и идёт через retry-only camera endpoint.
Если исходный POST не дошёл, durable tombstone запрещает запоздалый захват
следующей машины. Webhook номеров только сохраняет события и не читает весы.
Точный контракт, конфигурация, проверка и откат описаны в
[deploy/vehicle-plate-events.md](deploy/vehicle-plate-events.md). Фото и видео
в Asyl не передаются и не сохраняются.

### ApiPay / Kaspi Pay

- «Kaspi Pay · QR» создаётся через `POST /invoices/qr`; «Счёт на оплату»
  отправляется на телефон через `POST /invoices`. Оба запроса выполняет только
  backend с серверным `X-API-Key` того отдела продаж, к которому относится
  заказ.
- Ключ ApiPay и секрет вебхука задаются суперюзером в «Заказы → Отделы →
  отдел → Kaspi / ApiPay» и хранятся в базе зашифрованными (Fernet от
  `SECRET_KEY`; `SECRET_KEY_FALLBACKS` даёт ротацию). API отдаёт только факт
  подключения и последние символы ключа. У отдела без ключа оплата через
  Kaspi отклоняется с текстом «В отделе «…» не подключён Kaspi (ApiPay)».
- Остаток заказа можно разделять между наличными, QR и счётом. Незавершённую
  клиентскую часть можно закрыть и заменить другим способом; уже открытый QR
  считается потенциально оплачиваемым до терминального статуса провайдера.
- Публичный адрес уведомлений:
  `https://asyl-ltd.kz/api/webhooks/apipay/`.
- Подпись `X-Webhook-Signature` проверяется как HMAC-SHA256 от исходного тела
  запроса секретом каждого отдела по очереди; совпавший секрет определяет
  отдел события, и событие применяется только к счетам этого отдела
  (`403 invoice_department_mismatch` иначе). У каждого ключа в кабинете
  ApiPay указывается один и тот же адрес вебхука. Переменные `APIPAY_API_KEY`
  и `APIPAY_WEBHOOK_SECRET` читает только разовая миграция `sales.0004`,
  которая переносит старый общий ключ в основной отдел; после первого деплоя
  их можно удалить из `.env`.
- Денежные webhook-события остаются быстрым путём и применяются идемпотентно
  через durable inbox `ApiPayWebhookEvent`. Периодическая задача Celery в
  выделенной очереди `payments` восстанавливает пропущенные статусы счетов и
  возвратов через API ApiPay. Один worker, prefetch=1, expiring beat-сообщения и
  Redis-lease не допускают параллельных итераций; повтор выполняется с прежним
  ограниченным backoff только после безопасной для повтора ошибки сверки.
- Результаты задач не сохраняются; источники истины — транзакционные записи и
  heartbeat worker-а. Для ручной диагностики/аварийного fallback после остановки
  Celery остаётся `python manage.py reconcile_apipay_invoices --once` (без
  `--once` доступен прежний непрерывный loop).

---

## Тесты

`pytest.ini` выбирает `config.test_settings`: тестовые пользователи используют
быстрый hasher, чтобы не тратить время на production PBKDF. Этот модуль
отказывается загружаться вне тестового процесса; production-настройки
хеширования паролей не меняются.

```bash
cd backend && pytest
```

- `apps/conftest.py`: фабрики `make_user`, `user_with_perms(коды)`,
  преднастроенные сотрудники с прямыми правами (manager, accountant,
  operator, boss),
  `auth_client` с JWT.
- Покрыто: цепочки статусов и оплат, окно оплаты и долги, скоупинг отделов,
  склад (гонки, минус), архив товаров, корзина заказов, портал (маскирование
  денег, регистрация), системные права, камеры (discover с fallback'ами, атомарность
  AI-сессий, health-дебаунс и алерты).
- Внешние сервисы (ai_service, go2rtc, RTSP) в тестах мокаются; DRF-троттлинг
  под pytest отключён.
