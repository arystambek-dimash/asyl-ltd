# АСЫЛ-LTD — CRM мукомольного цеха

Внутренняя система учёта цеха «Асыл-LTD»: заказы и оплаты (в т.ч. долги),
склад, пост погрузки с камерами и AI-подсчётом мешков, клиентский портал,
разграничение доступа по персональным системным правам и отделам.

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
Камерные фичи локально выключены (пустые `CAMERA_*` переменные).

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
    clients/         # Client, Store; долги, аналитика
    sales/           # Department: динамические отделы продаж
    catalog/         # Product (+архив), ClientPrice
    orders/          # Order, OrderItem, Payment, StatusChangeRequest
    shipments/       # Shipment: приезд → погрузка → выезд, вагон
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
- **Права** — прямые системные permissions по строковым кодам (`orders.confirm`),
  а не Django-группы. Проверка на бэке (`HasPerm`) и на фронте (`can()`).
- **Отделы продаж** — динамический справочник: сотрудника можно закрепить за
  отделом, а его код фиксируется в заказе для фильтров и отчётов. Сам отдел
  не выдаёт permissions и не ограничивает доступ к данным.
- **Мягкое удаление**: заказы — в корзину (`deleted_at`), товары — в архив
  (`is_active=False`). Удалённое автоматически исчезает из списков и отчётов.

---

## Доступы: пользователи, системные права, отделы

### accounts

`User` наследует `AbstractUser` + флаг **`is_client`** (клиент портала).

- `perm_codes` — set кодов прав: суперюзер → все; сотрудник → его прямые
  permissions; клиент → пусто.
- `has_perm_code(code)` — точечная проверка.

Эндпоинты: `POST /api/auth/login/` (throttle 10/мин), `POST /api/auth/refresh/`,
`GET /api/auth/me/` → id, username, permissions, position, client_id,
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

| Раздел | Коды |
|---|---|
| Товары | `catalog.view / create / edit / delete` |
| Клиенты | `clients.view / create / edit / delete / set_price / manage_access` |
| Склад | `warehouse.view / adjust` |
| Заказы | `orders.view / create / edit / confirm / correct_price` |
| Оплаты | `payments.view / create / confirm` |
| Пост отгрузки | `shipping.view / arrive / load / ship / debt_override` |
| Вагон | `train.view / load` |
| Журнал / Отчёты | `events.view`, `reports.view` |
| Сотрудники / Системные права | `employees.view / manage`, `sys_permissions.view / manage` |

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

Переходы жёстко заданы в `ALLOWED_TRANSITIONS`; `transition()` бросает
`ValidationError` на недопустимый переход.

| Статус | Смысл |
|---|---|
| `draft` | черновик, свободно редактируется |
| `pending` | заявка ждёт подтверждения (цены — у бухгалтера/кассы) |
| `confirmed` | подтверждён, цены зафиксированы, ждёт машину/вагон |
| `arrived` | машина отмечена прибывшей отдельным постом |
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
| `_validate_payment_open(order)` | для любого отдела оплата доступна после `shipped`; при наличии магазина дополнительно проверяется его платёжный день |
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

`Shipment` (OneToOne к заказу): `truck_number`, нейтральный учётный
`weigh_in_kg`, `bags_loaded`, `arrived_at`,
`loading_started_at`, `shipped_at`.

Поток **грузовик**: `record_arrival` (confirmed→arrived, без обращения к весам) →
`start_loading` (→loading) → `record_count(bags)` (счёт мешков; из arrived
автоматически переводит в loading) → `finish_loading` (→loaded) →
`record_shipment` (→shipped).

Поток **вагон**: `start_train_loading` (confirmed→loading) →
`record_count` → `finish_train_loading` (→loaded) → `record_shipment` (→shipped).

Monoblock/AI/ESP32 работает только с заказом, камерой, числом мешков и
переходом `loading→loaded`; номер машины и физические весы для его запуска не
нужны. Интеграция автовесов принадлежит отдельному приложению `grain`.

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
- `client_analytics(client)` — KPI (выручка/оплачено/долг/средний чек),
  разбивка по статусам, помесячная динамика (8 мес), топ-5 товаров,
  последние заказы. Нефинансовые статусы (`draft/pending/rejected/cancelled`)
  в деньгах не участвуют.

Эндпоинты: CRUD клиентов/магазинов, `POST /clients/{id}/password/` для выдачи
временного пароля, `GET /clients/{id}/analytics`,
`GET /clients/debts`, `GET /clients/{id}/debt-detail`,
`GET /clients/stores/debts`, `POST /clients/stores/check-overdue`.
Новый клиент получает уникальный логин, отключённую учётку и unusable password.
Сотрудник с `clients.manage_access` включает доступ действием «Выдать доступ в портал»:
пароль не логируется, а при первом входе клиент обязан заменить его.

После первой миграции существующих клиентов можно один раз выдать общий
временный пароль интерактивной командой. Пароль вводится скрыто дважды и не
попадает в аргументы процесса или shell history:

```bash
docker compose -f docker-compose.prod.yml exec backend \
  python manage.py provision_client_accounts --dry-run
docker compose -f docker-compose.prod.yml exec backend \
  python manage.py provision_client_accounts
```

Команда обрабатывает только отключённые клиентские учётки с unusable password,
блокирует строки транзакционно и безопасна при повторном запуске.

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
target_total, conveyor_enabled, final_total, last_status JSON)` +
**UniqueConstraint: на камере максимум одна
открытая сессия** — камеру нельзя занять двумя заказами даже при
одновременных запросах.

Жизненный цикл (`sessions.py`): `reserve` (атомарный захват слота) →
`activate` → `update_status` (поллинг) → `finish` (сохраняет финальный
счётчик) / `fail`. Потеря AI-воркера не освобождает слот автоматически:
владение остаётся за заказом до явной сверки, чтобы новый заказ не получил
старый или обнулённый счёт.

Эндпоинты (`/api/cameras/{cam}/ai/`):

- `GET` — статус: чужой заказ получает дешёвый DB-ответ «камера занята»
  (`busy`), не трогая GPU; владелец — живой статус от ai_service.
- `POST` — привязать заказ и включить модель. Для ESP32 это двухфазная операция:
  target фиксируется в БД, edge готовит AI при проверенном `OFF`, заказ переходит
  в loading, затем отдельной командой включается привод.
  Таймаут ai_service — ситуация неоднозначная: владение сохраняется, чтобы
  второй заказ не стартовал на том же GPU; детерминированные ошибки (<500)
  сразу освобождают слот.
- `POST …/conveyor/stop/` — терминально остановить привод без закрытия заказа.
  `DELETE` сначала требует подтверждённый feedback `OFF`, затем фиксирует итог;
  `POST …/reset/` доступен только для старой сессии без автоматики.
- На camera-PC `total >= target_total`, stale/frozen AI, отсутствие прогресса,
  предельное время хода, ошибка Modbus и shutdown независимо переводят выход в
  терминальный `OFF`; браузер только отображает состояние.
- Camera-PC постоянно переутверждает write/readback `OFF`. Сам Modbus/TCP не
  имеет аутентификации: ESP32 `TCP/502` обязательно изолируется allowlist/VLAN
  только для camera-PC, а аппаратный E-stop остаётся независимым.
- Для удалённого ESP32 задайте камере transport `cloud`: camera-PC только
  отправляет HTTPS-наблюдения, backend выдаёт короткие ON-lease, а ESP32 сам
  опрашивает `/api/conveyors/v1/device/sync/`. Потеря Wi-Fi/API, reboot,
  устаревший AI или достижение `target_total` дают терминальный OFF; старый ON
  после восстановления связи не возобновляется. Настройка и протокол описаны в
  `backend/apps/conveyors/README.md` и `firmware/esp32-conveyor/README.md`.
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
| `/train` | устаревшая ссылка; сервер перенаправляет на единый пост `/shipping` |
| `/reports` | выручка и поступления по валютам, период и фильтр по отделу |
| `/management/employees` | сотрудники, отделы и персональные системные права |
| `/events` | журнал событий с фильтрами, группировка по дням |
| `/portal/catalog`, `/portal/orders`, `…/new`, `…/[id]` | портал клиента: каталог с остатками, свои заказы, оплата card/kaspi, номер машины, запрос долга |

### Механика

- **Auth**: axios-интерцептор добавляет `Bearer`, на 401 — одиночный
  refresh (без гонок), на неудачу — logout и `/login`. Стор `useAuth`
  (Zustand): `me`, `login`, `loadMe`, `refreshMe` (тихое обновление прав).
- **Права**: `can(me, code)`; `<RequirePerm code=…>` закрывает страницу
  заглушкой «Нет доступа»; сайдбар строится из прав; `homeFor(me)` разводит
  по домашним страницам (клиент → портал, моноблок → `/monoblock`,
  сотрудник → `/dashboard`).
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
| `celery-payments` | Celery worker только очереди `payments`, concurrency/prefetch = 1; сверка ApiPay |
| `celery-beat` | периодически ставит сверку ApiPay в Redis с expiry; schedule/pid живут в отдельном tmpfs |
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
3. **Camera health** не блокирует выпуск приложения: `camera-monitor`
   продолжает проверять потоки и отправлять алерты, а
   `wait-for-camera-health.sh` остаётся отдельной ручной диагностикой. Поэтому
   плановое отключение камер не запускает повторный деплой уже обновлённых
   контейнеров.
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
  credentials, Authorization, ApiPay, camera/AI и conveyor credentials
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
- **Frontend Sentry** по умолчанию только браузерный: публичный
  `NEXT_PUBLIC_SENTRY_DSN` встраивается при сборке, необработанные browser/React
  ошибки и оба App Router error boundary отправляются напрямую из браузера.
  Axios-ошибки глобально не перехватываются, чтобы частые poller-сбои не создавали
  шторм событий. Replay и profiling выключены, tracing по умолчанию равен 0.
- `SENTRY_FRONTEND_SERVER_DSN` оставлен пустым. Frontend-контейнер намеренно
  находится только во внутренней сети `edge` и не имеет выхода к hosted Sentry;
  server/edge reporting допустим только после добавления контролируемого relay
  или узкого egress без снятия сетевой изоляции целиком.
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
(`config/exceptions.py`) нормализует ответы к `{"detail", "code"}`.

### Номера автомобилей с camera-PC

AI на ПК камер передаёт только подтверждённые метаданные номера в защищённый
HTTPS webhook `https://asyl-ltd.kz/api/integrations/vehicle-plate-events`
(без завершающего `/`). Настройка токена, точный JSON-контракт, проверка и
откат описаны в [deploy/vehicle-plate-events.md](deploy/vehicle-plate-events.md).
Фото и видео для этой интеграции не передаются и не сохраняются.

### ApiPay / Kaspi Pay

- «Kaspi Pay · QR» создаётся через `POST /invoices/qr`; «Счёт на оплату»
  отправляется на телефон через `POST /invoices`. Оба запроса выполняет только
  backend с серверным `X-API-Key`.
- Остаток заказа можно разделять между наличными, QR и счётом. Незавершённую
  клиентскую часть можно закрыть и заменить другим способом; уже открытый QR
  считается потенциально оплачиваемым до терминального статуса провайдера.
- Публичный адрес уведомлений:
  `https://asyl-ltd.kz/api/webhooks/apipay/`.
- Подпись `X-Webhook-Signature` проверяется как HMAC-SHA256 от исходного тела
  запроса. Секреты задаются только через `APIPAY_API_KEY` и
  `APIPAY_WEBHOOK_SECRET` в `.env`.
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
