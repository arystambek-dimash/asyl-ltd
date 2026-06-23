# Клиентский портал самообслуживания — Design

Дата: 2026-06-23
Ветка: feat/order-detail-redesign (новая работа поверх)

## Цель

Дать клиенту возможность: зарегистрировать аккаунт, создать заказ (сколько муки),
дождаться решения staff (одобрить/отклонить), после одобрения оплатить
(картой / Kaspi QR / в долг), и после подтверждения оплаты отправить свой КАМАЗ
(указать номер). Весь staff-флоу приёмки/отгрузки остаётся как есть.

## Принципы реализации

- **Без дублирования логики.** Переходы статусов, проверки прав на редактирование
  `truck_number`, подтверждение оплаты — единый источник истины в сервисах/хелперах,
  переиспользуемых между portal- и staff-вьюхами.
- Небольшие, сфокусированные модули с чёткими границами.
- Backend — источник истины для прав доступа; UI лишь отражает права.
- Следовать существующим паттернам кодовой базы (DRF ViewSet + services.py,
  Next.js App Router + lib/ хелперы).

---

## Секция 1 — State machine заказа

Полный список статусов:

```
draft → pending → confirmed → paid → arrived → loading → loaded → shipped
                ↘ rejected
         (любой) ↘ cancelled
```

- `draft` — черновик staff (без изменений).
- `pending` *(новый)* — клиент создал заказ через портал, ждёт решения staff.
  Стартовый статус ВСЕХ portal-заказов.
- `confirmed` — staff одобрил. Открывается оплата клиенту.
- `rejected` *(новый)* — staff отклонил. Терминальный.
- `paid` — оплата подтверждена ИЛИ долг одобрен → «тотально готов», открывается ввод КАМАЗа.
- `arrived → loading → loaded → shipped` — без изменений (staff).
- `cancelled` — отмена (без изменений).

Переходы (кто инициирует / требуемый пермишен):

| Переход | Кто | Пермишен |
|---|---|---|
| создание portal-заказа → `pending` | клиент | IsClientUser |
| `pending → confirmed` | staff (одобрить) | `orders.confirm` |
| `pending → rejected` | staff (отклонить) | `orders.confirm` |
| `confirmed → paid` | авто при подтверждении оплаты ИЛИ одобрении долга staff | см. секцию 2 |

Все переходы реализуются через **единую функцию-переходник** в `orders/services.py`
(валидация допустимости перехода в одном месте), а не разрозненными `order.status = ...`.

---

## Секция 2 — Оплата

### Изменения модели `Payment` (orders/models.py)

Новые поля:
- `method` — choices: `cash | card | kaspi | debt`. Default `cash` (совместимость со staff-записями).
- `status` — choices: `pending | confirmed | rejected`. Default `confirmed`
  (старые записи и записи staff сразу подтверждены).
- `confirmed_by` — FK User, nullable.
- `confirmed_at` — DateTime, nullable.

`paid_total` на `Order` считает только `Payment` со `status=confirmed`.

### Изменения модели `Order`

- `debt_requested` — Boolean, default False (клиент запросил долг).
- (существующие `debt_override`, `debt_override_by` переиспользуются для одобрения долга.)

### Флоу оплаты клиента (доступно только при статусе `confirmed`)

1. **Картой / Kaspi QR**: клиент создаёт `Payment(method=card|kaspi, status=pending,
   amount=остаток)`. Заказ остаётся `confirmed`.
2. **Staff подтверждает** платёж (новый пермишен `payments.confirm`) → `status=confirmed`,
   `confirmed_by/at` заполняются. Если заказ `confirmed` и полностью оплачен (по
   подтверждённым платежам) → авто-переход `→ paid` (через переходник из секции 1).
   Staff может **отклонить** платёж (`status=rejected`) — клиент видит и пробует снова.
3. **В долг**: клиент жмёт «Взять в долг» → `debt_requested=True` (это НЕ Payment).
   Staff одобряет долг (существующий `shipping.debt_override`) → `debt_override=True`,
   `debt_override_by`, переход `→ paid`. Долг = неоплачен, но разрешён к отгрузке.

### Kaspi QR (MVP)

Статичный QR/реквизиты из настроек (Django settings или модель настроек —
выбрать минимальный вариант: settings + endpoint). Клиенту показываем QR + сумму
к оплате текстом. Динамическая генерация под сумму — позже (интеграция).

Способы оплаты, видимые клиенту: **Картой, Kaspi QR, В долг**. `cash` — только staff.

Логика подтверждения платежа и авто-перехода — в `orders/services.py`
(`confirm_payment`, `reject_payment`, `approve_debt`), переиспользуется вьюхами.

---

## Секция 3 — КАМАЗ (truck_number) + защита прав

После `paid` клиенту открывается поле «Номер КАМАЗа».

### Изменение модели `Order`
- `truck_number_set_by` — FK User, nullable. Кто последним установил номер.

### Правила изменения `truck_number` (enforced на backend)
- Номер ещё не задан → задать может владелец-клиент (портал) ИЛИ staff (приёмка).
- Номер задал **клиент** (`truck_number_set_by` = клиентский User) → менять может
  **только этот же клиент**; staff получает 403.
- Номер задал **staff** → менять может staff (как сейчас).

Реализация — единый хелпер `can_set_truck_number(order, user)` в `orders/services.py`,
вызывается и из portal-action, и из staff-флоу (`set-status`, `arrive`).

- Portal: `PATCH /api/portal/orders/{id}/truck/` — проверяет `order.client.user == request.user`
  И `order.status == paid` И `can_set_truck_number`. Иначе 403/409.
- Staff: перед записью `truck_number` вызывает тот же хелпер; если номер принадлежит
  клиенту — запрет перезаписи, поле read-only с пометкой «введено клиентом».

Дальше — существующий staff-флоу `arrive → load → ship`, без изменений.

---

## Секция 4 — Backend API

### Регистрация (публичная, AllowAny)
- `POST /api/portal/register/` — `{username, password, first_name, last_name, phone, iin}`
  → в транзакции создаёт `User(is_client=True)` + `Client(user=...)`, возвращает JWT.
  Валидация: уникальность username, формат телефона/ИИН, сила пароля.

### Портал клиента (IsClientUser, фильтр `client__user=request.user`)
- `GET /api/portal/catalog/` *(есть)*
- `GET /api/portal/orders/` *(есть)*
- `POST /api/portal/orders/` — создать → `pending` *(меняем: было draft)*
- `GET /api/portal/orders/{id}/` *(есть)*
- `POST /api/portal/orders/{id}/pay/` — `{method: card|kaspi}` → `Payment(status=pending)` на остаток *(новый)*
- `POST /api/portal/orders/{id}/request-debt/` — `debt_requested=True` *(новый)*
- `PATCH /api/portal/orders/{id}/truck/` — `{truck_number}` (только `paid` + владелец) *(новый)*
- `GET /api/portal/payment-info/` — статичный Kaspi QR + реквизиты *(новый)*

### Staff
- `POST /api/orders/{id}/confirm/` *(есть)* — теперь `pending → confirmed`
- `POST /api/orders/{id}/reject/` — `pending → rejected` (`orders.confirm`) *(новый)*
- `POST /api/orders/{id}/payments/{pid}/confirm/` — (`payments.confirm`) *(новый)*
- `POST /api/orders/{id}/payments/{pid}/reject/` — (`payments.confirm`) *(новый)*
- `POST /api/orders/{id}/approve-debt/` — `debt_override` + `→ paid` (`shipping.debt_override`) *(новый)*

### RBAC
- Новый пермишен `payments.confirm`. Добавить в пресеты: Бухгалтер, Менеджер, Начальник.
- `reject` заказа — переиспользует `orders.confirm`.
- Одобрение долга — существующий `shipping.debt_override`.

---

## Секция 5 — Frontend (Next.js App Router)

### Страницы
- `/register` *(новый)* — публичная форма (React Hook Form + Zod), auto-login после успеха.
- `/portal/orders` *(есть)* — добавить колонку статуса с лейблами/бейджами.
- `/portal/orders/new` *(есть)* — без изменений (создаёт `pending`).
- `/portal/orders/[id]` *(новый)* — деталь клиента: позиции, клиентский статус-степпер,
  контекстная панель действий по статусу:
  - `pending` → «На рассмотрении»
  - `confirmed` → форма оплаты (Картой / Kaspi QR / В долг)
  - `rejected` → «Отклонён»
  - `paid` → ввод/изменение номера КАМАЗа (если владелец)
  - `arrived+` → статус доставки (read-only)

### Чистая архитектура (без дублирования)
- Лейблы/тона статусов и способов оплаты — расширить `lib/constants.ts`.
- «Что доступно клиенту в этом статусе» — один хелпер `lib/portal-actions.ts`,
  используется в списке и детали.
- Мутации (`pay`, `request-debt`, `set truck`) — переиспользуемые функции в `lib/`
  поверх `api`, не разрозненные `api.post` по компонентам.
- Переиспользовать существующие UI-компоненты; степпер вынести в общий компонент,
  если staff- и client-версии разойдутся.

---

## Тестирование

- Backend: pytest для services (переходы статусов, `confirm_payment`/`approve_debt`,
  `can_set_truck_number` — все ветки прав), для register endpoint, для portal-actions
  (403 на чужой заказ, 409 на неверный статус).
- Защита truck_number: тест, что staff НЕ может перезаписать клиентский номер, и наоборот.

## Вне scope (позже)

- Реальная интеграция Kaspi/банка с callback (авто-подтверждение оплаты).
- Динамическая генерация Kaspi QR под сумму.
- Email/SMS уведомления о смене статуса.
