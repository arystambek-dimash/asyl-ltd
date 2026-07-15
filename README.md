# АСЫЛ-LTD — CRM мукомольного цеха

Внутренняя система учёта цеха «Асыл-LTD»: заказы и оплаты (в т.ч. долги),
склад, пост погрузки с камерами и AI-подсчётом мешков, клиентский портал,
разграничение доступа по ролям и отделам.

- **Бэкенд** (`backend/`): Django + DRF + PostgreSQL + Redis, JWT (simplejwt).
- **Фронтенд** (`frontend/`): Next.js 15 (App Router) + React 19 + Tailwind 4,
  Zustand, Recharts, Radix UI.
- **Видео**: go2rtc (RTSP → fMP4 без транскодирования), доступ через
  nginx `auth_request` + подписанная cookie.
- **Инфраструктура**: Docker Compose, nginx (rate-limit, TLS), WireGuard-туннель
  до цехового ПК с камерами и ai_service.

---

## Содержание

1. [Запуск](#запуск)
2. [Структура репозитория](#структура-репозитория)
3. [Архитектура](#архитектура)
4. [Доступы: пользователи, RBAC, отделы](#доступы-пользователи-rbac-отделы)
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
Камерные фичи локально выключены (пустые `CAMERA_*` переменные).

### Разработка без Docker

```bash
# Бэкенд
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
pytest                      # 340 тестов

# Фронтенд
cd frontend
npm install
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
    rbac/            # Permission, Role, коды прав, scoping по отделам
    employees/       # Employee: связка User + Role + личные права
    clients/         # Client, Store, Department; долги, аналитика
    catalog/         # Product (+архив), ClientPrice
    orders/          # Order, OrderItem, Payment, StatusChangeRequest
    shipments/       # Shipment: приезд → погрузка → выезд, поезд
    warehouse/       # StockItem, StockReceipt, StockMovement
    portal/          # клиентский портал: каталог, заказы, регистрация
    notifications/   # уведомления клиентам
    eventlog/        # неизменяемый журнал событий (log_event)
    cameras/         # go2rtc, AI-подсчёт, health-мониторинг, алерты
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
backend ──► ai_service :8890 на цеховом ПК (через WireGuard) — AI-подсчёт мешков
camera-monitor (отдельный контейнер) — непрерывный probe камер, инциденты, алерты
```

Ключевые сквозные принципы:

- **Каждое значимое действие логируется** в `eventlog` через `log_event(...)`
  (оплаты, статусы, погрузка, склад, архив товаров, долги).
- **Права** — собственный RBAC по строковым кодам (`orders.confirm`),
  а не Django-группы. Проверка на бэке (`HasPerm`) и на фронте (`can()`).
- **Отделы** (`main` = Отдел 1, `field` = Отдел 2 «Сити») жёстко разделяют
  данные: queryset'ы фильтруются `scope_by_department`.
- **Мягкое удаление**: заказы — в корзину (`deleted_at`), товары — в архив
  (`is_active=False`). Удалённое автоматически исчезает из списков и отчётов.

---

## Доступы: пользователи, RBAC, отделы

### accounts

`User` наследует `AbstractUser` + флаг **`is_client`** (клиент портала).

- `perm_codes` — set кодов прав: суперюзер → все; сотрудник → права роли ∪
  личные права; клиент → пусто.
- `has_perm_code(code)` — точечная проверка.

Эндпоинты: `POST /api/auth/login/` (throttle 10/мин), `POST /api/auth/refresh/`,
`GET /api/auth/me/` → id, username, permissions, role_name, client_id,
department_names.

### common/permissions.py — общие DRF-права

| Класс | Пропускает |
|---|---|
| `IsStaff` | авторизованный сотрудник (не клиент) |
| `IsClientUser` | авторизованный клиент портала |
| `IsSuperuser` | только суперадмин |
| `HasPerm(*codes)` | сотрудник хотя бы с одним из кодов |
| `PermViewSetMixin` | миксин: `required_perms = {action: код или кортеж}` → `HasPerm`; для action без записи — просто `IsAuthenticated` |

### rbac — коды прав

Модели: `Permission(code, section, action, label)`, `Role(name, permissions M2M, is_system)`.

| Раздел | Коды |
|---|---|
| Товары | `catalog.view / create / edit / delete` |
| Клиенты | `clients.view / create / edit / delete` |
| Склад | `warehouse.view / adjust` |
| Заказы | `orders.view / create / edit / confirm` |
| Оплаты | `payments.view / create / confirm` |
| Пост отгрузки | `shipping.view / arrive / load / ship / debt_override` |
| Поезд | `train.view / load` |
| Отдел 2 «Сити» | `dept2.view` (только свои), `dept2.view_all`, `dept2.create` |
| Журнал / Отчёты | `events.view`, `reports.view` |
| Сотрудники / Доступы | `employees.view / manage`, `rbac.view / manage` |

Системные роли-пресеты (`rbac/perms.py`): **Менеджер**, **Касса**, **Оператор**,
**Загрузчик**, **Контролёр**, **Менеджер Сити**, **Начальник**.
Роль нельзя удалить, пока на ней есть сотрудники.

### Скоупинг по отделам (`rbac/scoping.py`)

`scope_by_department(qs, user, base_view_perm, dept_field, owner_field)`:

- базовое право раздела (например `clients.view`) → видит отдел `main`;
- `dept2.view_all` → весь отдел `field` (руководитель, касса);
- `dept2.view` → только записи `field`, где user — менеджер-владелец;
- клиент портала / аноним → ничего; суперюзер → всё.

`sees_all_departments(user)` — для сводных колонок «Отдел» в отчётах.

### employees

`Employee(user OneToOne, role FK, permissions M2M, is_active)` —
`effective_perm_codes` = коды роли ∪ личные коды. Создание сотрудника —
одна транзакция: `User` + `Employee` + права по кодам (`permission_codes`).

---

## Бизнес-логика по приложениям

### orders — заказы и оплаты

#### Статусы заказа

```
draft → pending → confirmed → arrived → loading → loaded → shipped
          └→ rejected            (любой нефинальный → cancelled)
```

Переходы жёстко заданы в `ALLOWED_TRANSITIONS`; `transition()` бросает
`ValidationError` на недопустимый переход.

| Статус | Смысл |
|---|---|
| `draft` | черновик, свободно редактируется |
| `pending` | заявка ждёт подтверждения (цены — у бухгалтера/кассы) |
| `confirmed` | подтверждён, цены зафиксированы, ждёт машину/поезд |
| `arrived` | машина въехала (пост взвешивания) |
| `loading` | идёт погрузка (счёт мешков) |
| `loaded` | погрузка завершена |
| `shipped` | выехал; товар списан со склада |
| `rejected` / `cancelled` | отклонён / отменён |

Ключевые поля `Order`: `payment_status` (`unpaid/partial/settled`),
`settlement_intent` (`debt` — основной путь ~90% заказов / `instant`),
`debt_requested` (клиент попросил долг), `debt_override(+_by)` (долг одобрен
правом `shipping.debt_override`), `department` (денормализован из клиента),
`transport_type` (`truck/train`), `truck_number(+_set_by)`, `store`,
`loading_camera` (какая камера занята под погрузку), `deleted_at/_by`
(корзина). Менеджеры: `objects` — только живые, `all_objects` — с удалёнными.

Вычисляемое: `total_amount`, `paid_total` (только подтверждённые оплаты),
`remaining_amount`, `is_fully_paid`,
**`is_debt` = shipped + intent=debt + остаток > 0** — определение долга во всех
отчётах.

#### Оплаты (`Payment`)

Методы: `cash / card / kaspi / debt`. Цепочка статусов:

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
| `_validate_payment_open(order)` | окно оплаты: отдел `main` — только после `shipped` и в платёжный день магазина; отдел `field` — без ограничений |
| `add_payment(order, amount, user, method, stage)` | старт цепочки (`requested` или сразу `received`) |
| `receive_payment` / `accountant_confirm_payment` / `reject_payment` | шаги цепочки; подтверждение пересчитывает `payment_status` заказа |
| `create_client_payment(order, method, user)` | оплата из портала (card/kaspi) на весь остаток, `update_or_create` от двойных кликов |
| `pay_via_bank(order, user)` | банковская оплата-заглушка на весь остаток |
| `sync_payment_status(order)` | идемпотентный пересчёт `unpaid/partial/settled` |
| `approve_debt(order, user)` | одобрить долг: `debt_override=True`, intent=`debt` |
| `confirm_order(order, user, prices)` | draft/pending → confirmed + фиксация цен позиций |
| `_apply_prices` / `apply_item_prices` | проставить `unit_price` позиций и запомнить прайс клиента в `ClientPrice`; без цены > 0 — ошибка |
| `replace_items(order, items, prices, user)` | замена позиций (только в `draft/pending/confirmed/arrived`), с блокировкой от гонки со стартом погрузки и проверкой склада |
| `set_truck_number(order, value, user)` | номер КАМАЗа; клиент не может переписать номер, заданный сотрудником; уведомляет клиента |
| `request_status_change` / `approve_ / reject_status_change` | ручная смена статуса: с правом `orders.edit` — сразу, без — создаётся `StatusChangeRequest` на одобрение |
| `soft_delete_order` / `restore_order` | корзина: `deleted_at` ставится/чистится |

#### Эндпоинты (`/api/orders/…`)

CRUD + действия: `confirm`, `reject`, `payments` (+ `receive/confirm/reject`
по оплате), `payments-queue` (очередь кассы), `pay-bank`, `debts` (все долги),
`set-status` (+ `status-requests/approve|reject`), `approve-debt`,
`trash` / `restore` (корзина), `train/queue` + `train` (start/count/finish),
`loading-camera` (занять/освободить камеру). Права — см. `required_perms`
во `views.py`; списки скоупятся по отделу.

### shipments — отгрузка

`Shipment` (OneToOne к заказу): `truck_number`, `weigh_in_kg` (вес на въезде —
спрашивается только если у товара `ask_truck_weight=True`, иначе берётся
расчётный `Σ qty × weight_kg`), `bags_loaded`, `arrived_at`,
`loading_started_at`, `shipped_at`.

Поток **грузовик**: `record_arrival` (confirmed→arrived, взвешивание) →
`start_loading` (→loading) → `record_count(bags)` (счёт мешков; из arrived
автоматически переводит в loading) → `finish_loading` (→loaded) →
`record_shipment` (→shipped).

Поток **поезд**: `start_train_loading` (confirmed→loading) →
`record_count` → `finish_train_loading` (→loaded→shipped одним шагом).

Общий финал `_do_ship`: списывает каждую позицию со склада
(`deduct_stock(allow_negative=True)` — по факту можно уйти в минус),
ставит `shipped_at`, `status=shipped`, `payment_status=unpaid`, логирует
`debt` + `shipment`. **Оплата происходит после въезда машины** — заказ едет
в долг, деньги закрываются через кассу.

Эндпоинты: `POST /api/orders/{id}/arrive | load | finish-loading | ship`
(права `shipping.arrive/load/ship`).

### warehouse — склад

- `StockItem(product OneToOne, bags)` — остаток; может быть отрицательным
  (списание в минус при отгрузке).
- `StockReceipt` — акт приёмки; `StockMovement` — история каждого движения
  (`delta`, `balance_after`, `reason: adjustment/receipt/shipment`).

Сервисы (все под `select_for_update`/`F()` — безопасны от гонок):

- `ensure_products_available(products)` — товар заказываем только при
  `stock.bags > 0` (проверка при создании/редактировании заказа);
- `adjust_stock(product, delta, user, note)` — корректировка, минус запрещён;
- `receive_stock(product, bags, user)` — приёмка;
- `deduct_stock(product, bags, user, allow_negative)` — списание; с
  `allow_negative=True` логирует предупреждение `stock_negative`.

Эндпоинты: `GET /api/warehouse/stock/`, `POST …/adjust`, `POST …/receive`,
`GET …/movements` (права `warehouse.view` / `warehouse.adjust`).

### catalog — товары

`Product(name, color: Red/Green/Blue, weight_kg: 25/50, price,
is_active, ask_truck_weight)`, уникальность `(name, color, weight_kg)`.
`cv_class` → `"Red_50"` — класс для AI-классификации мешков на видео.

`ClientPrice(client, product, price)` — запомненный прайс клиента,
обновляется при подтверждении заказа; `GET /api/catalog/client-prices/?client=`
предзаполняет цены в форме заказа.

**Архив вместо удаления**: `DELETE /products/{id}` вызывает
`archive_product` (`is_active=False`); есть явные `POST …/archive` и
`…/restore`. Фильтр архива — в `get_queryset` (`?archived=1`), а не в
default-менеджере, чтобы старые заказы и отчёты видели архивные товары.

### clients — клиенты и магазины

- `Department(code: main|field, name)` — код фиксирован, название редактируется
  (только суперадмин).
- `Client`: ФИО, телефон, реквизиты (ИИН, банк, счёт), `department`,
  `manager` (менеджер Отдела 2, ведущий клиента), `user` (учётка портала).
- `Store` (магазин клиента): `payment_schedule_type` (`none/monthly/weekly`) +
  `payment_days` (дни месяца или ISO-дни недели) — расписание платежей.

Сервисы:

- `is_payment_window_open(store, date)` — открыто ли окно оплаты сегодня;
- `detect_overdue(store, date)` — если окно открыто и есть отгруженные
  неоплаченные заказы — шлёт уведомление клиенту (кнопка «Проверить
  просрочки» в кассе);
- `client_analytics(client)` — KPI (выручка/оплачено/долг/средний чек),
  разбивка по статусам, помесячная динамика (8 мес), топ-5 товаров,
  последние заказы. Нефинансовые статусы (`draft/pending/rejected/cancelled`)
  в деньгах не участвуют.

Эндпоинты: CRUD клиентов/магазинов, `GET /clients/{id}/analytics`,
`GET /clients/debts`, `GET /clients/{id}/debt-detail`,
`GET /clients/stores/debts`, `POST /clients/stores/check-overdue`.
Менеджер Отдела 2 (`dept2.create` без `clients.create`) создаёт клиентов
только в `field` и только на себя.

### portal — клиентский портал

Для пользователей с `is_client=True` (учётка привязана к `Client.user`).

- `POST /api/portal/register/` — самостоятельная регистрация
  (throttle 5/мин): транзакция `User(is_client=True)` + `Client`, сразу
  возвращает JWT.
- `GET /api/portal/catalog/` — активные товары с остатками.
- `GET/POST /api/portal/orders/` — свои заказы; создание: позиции,
  `settlement_intent`, `transport_type`, магазин.
  - `POST …/{id}/pay/` — оплата card/kaspi (реквизиты — `GET /api/portal/payment-info/`);
  - `PATCH …/{id}/truck/` — вписать номер машины (только в `confirmed`);
  - `POST …/{id}/request-debt/` — запросить долг (только в `shipped`).
- **Маскирование денег**: суммы видны клиенту только когда заказ прошёл
  подтверждение (не в `draft/pending/rejected/cancelled`); внутренние стадии
  оплат клиенту не показываются.
- `GET /api/portal/stores/` — магазины своего клиента.
- `GET /api/portal/notifications/` + `POST …/{id}/read/` — уведомления.

### cameras — камеры и AI-подсчёт

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

#### AI-подсчёт мешков (пост погрузки)

Модель `AiCountingSession(order, camera, status: STARTING/ACTIVE/CLOSED/FAILED,
final_total, last_status JSON)` + **UniqueConstraint: на камере максимум одна
открытая сессия** — камеру нельзя занять двумя заказами даже при
одновременных запросах.

Жизненный цикл (`sessions.py`): `reserve` (атомарный захват слота) →
`activate` → `update_status` (поллинг) → `finish` (сохраняет финальный
счётчик) / `fail`. Если AI-воркер на цеховом ПК умер — сессия помечается
failed и слот освобождается автоматически.

Эндпоинты (`/api/cameras/{cam}/ai/`):

- `GET` — статус: чужой заказ получает дешёвый DB-ответ «камера занята»
  (`busy`), не трогая GPU; владелец — живой статус от ai_service.
- `POST` — включить модель (заказ обязан быть в `arrived/loading`).
  Таймаут ai_service — ситуация неоднозначная: владение сохраняется, чтобы
  второй заказ не стартовал на том же GPU; детерминированные ошибки (<500)
  сразу освобождают слот.
- `DELETE` — выключить и зафиксировать итог; `POST …/reset/` — обнулить
  счётчик под новую погрузку.
- Коды ошибок: `ai_disabled` (503, фича не настроена), `ai_unavailable`
  (502, ПК не отвечает), `ai_busy` (409, камера занята другим заказом),
  `ai_error`, `ai_processor_stopped`.
- Права: смотреть — `IsStaff`, управлять — `HasPerm("shipping.load")`.

#### Health-мониторинг и алерты

Отдельный контейнер **camera-monitor** (`manage.py monitor_cameras`) раз в
~30 с делает end-to-end пробы: инвентарь ai_service, каталог go2rtc,
RTSP DESCRIBE каждого потока, выборочный JPEG-кадр через go2rtc. Состояние —
в PostgreSQL (`CameraHealthState` singleton, `CameraIncident`):
статусы HEALTHY/DEGRADED/OUTAGE с дебаунсом (3 плохих подряд — инцидент,
2 хороших — восстановление). Алерты — webhook и/или Telegram
(`CAMERA_ALERT_*` env), с ретраями и аудитом доставки.
`GET /api/cameras/health/` отдаёт состояние (503 при подтверждённом отказе);
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
| `/accounting` | «Касса»: вкладка **Оплаты** (подтверждение pending-заказов и очередь оплат) и **Долги** (клиенты с долгом, «Проверить просрочки») |
| `/accounting/debts/clients/[id]`, `…/stores/[id]` | детализация долга клиента/магазина |
| `/clients`, `/clients/[id]` | база клиентов + аналитика по клиенту (графики за 8 мес, статусы, средний чек) |
| `/stores` | магазины клиентов, графики оплат (нет/еженедельно/ежемесячно) |
| `/catalog/products` | вкладки «Товары» / «Архив»; архивирование вместо удаления; флаг «спрашивать вес грузовика» |
| `/warehouse` | остатки; корректировка/приёмка с быстрыми кнопками и превью «сейчас → станет» |
| `/shipping` | пост погрузки: очередь машин; рабочая зона выбранной машины — госномер, прогресс этапов, live-видео выбранной камеры (камера закрепляется за заказом), счётчик мешков (+1/+5/−1, дебаунс-сохранение), AI-подсчёт с аннотированным потоком, действия «Принять машину» (вес на въезде) / «Погрузка завершена» / «Отгрузить — выезд». Несколько машин грузятся параллельно на разных камерах |
| `/train` | очередь поездов: старт погрузки, счёт мешков, завершение |
| `/reports` | выручка vs поступления, период/группировка, фильтр по клиенту |
| `/management/employees`, `…/roles`, `…/departments` | сотрудники (роль + личные права через `PermissionPicker`), роли, переименование отделов |
| `/events` | журнал событий с фильтрами, группировка по дням |
| `/city/orders`, `/city/clients` | рабочее место менеджера Отдела 2 «Сити» (виден только dept2-пользователям) |
| `/portal/catalog`, `/portal/orders`, `…/new`, `…/[id]` | портал клиента: каталог с остатками, свои заказы, оплата card/kaspi, номер машины, запрос долга |

### Механика

- **Auth**: axios-интерцептор добавляет `Bearer`, на 401 — одиночный
  refresh (без гонок), на неудачу — logout и `/login`. Стор `useAuth`
  (Zustand): `me`, `login`, `loadMe`, `refreshMe` (тихое обновление прав).
- **Права**: `can(me, code)`; `<RequirePerm code=…>` закрывает страницу
  заглушкой «Нет доступа»; сайдбар строится из прав; `homeFor(me)` разводит
  по домашним страницам (клиент → портал, dept2-менеджер → `/city/orders`).
- **UI-кит** (`components/ui`): Button/Input/Select/Modal/ConfirmDialog,
  Table + SortableHeader, Badge/StatusBadge/PaymentStageBadge, KPI-карточки,
  LicensePlateInput (госномер), DataState (loading/error/empty), Tabs.
  Тема light/dark/system. Паттерны дизайна — Stripe/Linear/UniFi.
- **Камеры**: `CameraWall`, `CameraStream` (fMP4/MSE от go2rtc),
  `useAiCounter` — поллинг статуса AI и управление сессией.

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
| `db` / `redis` | PostgreSQL 16 / Redis 7 — в изолированной internal-сети `data` |
| `db-backup` | ежедневный `pg_dump` + бэкап перед каждым деплоем |
| `wireguard` | туннель до цехового ПК (NVR + ai_service :8890) |

Сети изолированы: `edge` (nginx↔front/back), `data` (db/redis), `default` —
фронт не имеет доступа к БД и наружу.

### Деплой (`deploy/remote-deploy.sh`)

1. Только **immutable digest** образов (`ghcr.io/...@sha256:…`) — `:latest`
   отклоняется; flock от параллельных деплоев.
2. `git pull --ff-only` → бэкап БД → `docker compose pull` →
   `up -d --wait` (по healthcheck'ам: у backend — `healthcheck.py`, GET
   `/api/auth/me/`).
3. **Camera health gate**: `wait-for-camera-health.sh` ждёт свежий heartbeat
   camera-monitor (exit-коды: 0 ok / 2 stale / 3 outage / 4 degraded) —
   деплой падает, если камеры не поднялись после рестарта go2rtc.
4. `nginx -t && nginx -s reload` (graceful).

Замечания по прод-хостингу (ps.kz): сервер может внезапно ребутнуться —
деплой и проверки написаны с ретраями; троттлинг DRF выключен под pytest;
go2rtc rate-limit'ить нельзя (живое видео).

### Throttling (уровень Django)

`anon 60/мин`, `user 600/мин`, `login 10/мин`, `register 5/мин`
(config/throttles.py, поверх nginx-лимитов). Единый обработчик ошибок
(`config/exceptions.py`) нормализует ответы к `{"detail", "code"}`.

---

## Тесты

```bash
cd backend && pytest    # 340 тестов
```

- `conftest.py`: фабрики `make_user`, `user_with_perms(коды)`, преднастроенные
  фикстуры ролей (manager, accountant, operator, boss, dept2_manager),
  `auth_client` с JWT.
- Покрыто: цепочки статусов и оплат, окно оплаты и долги, скоупинг отделов,
  склад (гонки, минус), архив товаров, корзина заказов, портал (маскирование
  денег, регистрация), RBAC, камеры (discover с fallback'ами, атомарность
  AI-сессий, health-дебаунс и алерты).
- Внешние сервисы (ai_service, go2rtc, RTSP) в тестах мокаются; DRF-троттлинг
  под pytest отключён.
