# Order/Payment Flow Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move payment to AFTER the «Отгружен» (shipped) status, split logistics from payment status, make debt the default path, and add Stores with payment-day schedules plus client Notifications.

**Architecture:** Django REST backend under `backend/apps/*`. Each app has `models.py`, `services.py` (all business logic lives here — views stay thin), `serializers.py`, `views.py`, `urls.py`. Logistics state lives in `Order.status`; payment state moves to a new `Order.payment_status`. Payment becomes possible only once `status == "shipped"`. Stores belong to Clients and gate payments by allowed weekday/month-day windows. A new `notifications` app stores per-client messages.

**Tech Stack:** Django, Django REST Framework, pytest + pytest-django. Tests run from `backend/` with `pytest` (config in `backend/pytest.ini`, settings `config.settings`). The `boss` fixture (conftest.py) is a user with broad perms.

## Global Constraints

- All business logic goes in `services.py`, never in views/serializers (centralize, no duplication — user rule).
- Run all commands from `backend/` directory.
- Migrations: every model change needs `python manage.py makemigrations <app>` committed alongside code.
- Existing flow constant `Order.STATUSES` and `ALLOWED_TRANSITIONS` must stay internally consistent.
- Logistics statuses: `draft, pending, confirmed, arrived, loading, loaded, shipped, rejected, cancelled` (NO `paid`).
- Payment statuses: `unpaid, partial, settled`.
- Settlement intents: `debt` (default), `instant`.
- Commit after each task with a clear `feat:`/`refactor:`/`test:` message.

---

### Task 1: Add payment_status + settlement_intent to Order; drop `paid` from logistics

**Files:**
- Modify: `backend/apps/orders/models.py`
- Create: `backend/apps/orders/migrations/00XX_payment_status.py` (via makemigrations)
- Test: `backend/apps/orders/tests/test_order_model.py`

**Interfaces:**
- Produces: `Order.PAYMENT_STATUSES = ["unpaid", "partial", "settled"]`; `Order.SETTLEMENT_INTENTS = ["debt", "instant"]`; fields `Order.payment_status` (default `"unpaid"`), `Order.settlement_intent` (default `"debt"`). `Order.STATUSES` no longer contains `"paid"`.
- `Order.remaining_amount` property → `Decimal` = `total_amount - paid_total`.

- [ ] **Step 1: Write the failing test**

Create `backend/apps/orders/tests/test_order_model.py`:

```python
import pytest
from decimal import Decimal
from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem

pytestmark = pytest.mark.django_db


def test_order_defaults_debt_and_unpaid():
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    o = Order.objects.create(client=c)
    assert o.payment_status == "unpaid"
    assert o.settlement_intent == "debt"
    assert "paid" not in Order.STATUSES


def test_remaining_amount():
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    p = Product.objects.create(name="P", color="Red", weight_kg="50", price="100.00")
    o = Order.objects.create(client=c)
    OrderItem.objects.create(order=o, product=p, quantity=2)
    assert o.remaining_amount == Decimal("200.00")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest apps/orders/tests/test_order_model.py -v`
Expected: FAIL (AttributeError / assertion on `settlement_intent`).

- [ ] **Step 3: Implement the model changes**

In `backend/apps/orders/models.py`, update `Order`:

```python
class Order(models.Model):
    STATUSES = ["draft", "pending", "confirmed", "arrived",
                "loading", "loaded", "shipped", "rejected", "cancelled"]
    PAYMENT_STATUSES = ["unpaid", "partial", "settled"]
    SETTLEMENT_INTENTS = ["debt", "instant"]

    client = models.ForeignKey(
        "clients.Client", on_delete=models.PROTECT, related_name="orders"
    )
    status = models.CharField(max_length=20, default="draft")
    payment_status = models.CharField(max_length=20, default="unpaid")
    settlement_intent = models.CharField(max_length=20, default="debt")
    truck_number = models.CharField(max_length=30, blank=True, default="")
```

(keep the rest of the existing fields unchanged below `truck_number`).

Add the property next to `is_fully_paid`:

```python
    @property
    def remaining_amount(self) -> Decimal:
        return self.total_amount - self.paid_total
```

- [ ] **Step 4: Make migration**

Run: `cd backend && python manage.py makemigrations orders`
Expected: new migration file created adding `payment_status`, `settlement_intent`.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && pytest apps/orders/tests/test_order_model.py -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
cd backend && git add apps/orders/models.py apps/orders/migrations apps/orders/tests/test_order_model.py
git commit -m "feat(orders): split logistics/payment status, add settlement_intent"
```

---

### Task 2: Rework transitions — loading no longer needs payment; payment lives after shipped

**Files:**
- Modify: `backend/apps/orders/services.py`
- Modify: `backend/apps/shipments/services.py`
- Test: `backend/apps/orders/tests/test_payment_flow.py`

**Interfaces:**
- Consumes: `Order.payment_status`, `Order.remaining_amount` (Task 1).
- Produces:
  - `ALLOWED_TRANSITIONS` with `"arrived": {"loading", "cancelled"}` (no `paid`).
  - `add_payment(order, amount, user, method="cash", status="confirmed")` raises `ValidationError` code `payment_not_open` unless `order.status == "shipped"`.
  - `_apply_payment_status(order)` sets `payment_status` to `unpaid`/`partial`/`settled` from totals.
  - `start_loading(order, user)` and `record_count(order, ...)` require `order.status == "arrived"`/`loading`, not `paid`.

- [ ] **Step 1: Write the failing test**

Create `backend/apps/orders/tests/test_payment_flow.py`:

```python
import pytest
from decimal import Decimal
from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem
from apps.orders.services import add_payment
from rest_framework.exceptions import ValidationError

pytestmark = pytest.mark.django_db


def _order(boss, status="shipped"):
    p = Product.objects.create(name="P", color="Red", weight_kg="50", price="100.00")
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    o = Order.objects.create(client=c, status=status)
    OrderItem.objects.create(order=o, product=p, quantity=2)  # total 200
    return o


def test_payment_blocked_before_shipped(boss):
    o = _order(boss, status="loading")
    with pytest.raises(ValidationError) as e:
        add_payment(o, "100", boss)
    assert e.value.detail["code"] == "payment_not_open"


def test_partial_then_full_payment(boss):
    o = _order(boss, status="shipped")
    add_payment(o, "100", boss)
    o.refresh_from_db()
    assert o.payment_status == "partial"
    add_payment(o, "100", boss)
    o.refresh_from_db()
    assert o.payment_status == "settled"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest apps/orders/tests/test_payment_flow.py -v`
Expected: FAIL (payment not blocked / payment_status not updated).

- [ ] **Step 3: Update `ALLOWED_TRANSITIONS` and `add_payment`/`_apply_payment_status`**

In `backend/apps/orders/services.py` replace `ALLOWED_TRANSITIONS` and the `_maybe_mark_paid` block, and gate `add_payment`:

```python
# Логистика: подтверждение → въезд → загрузка → отгрузка. Оплата — отдельно, после shipped.
ALLOWED_TRANSITIONS = {
    "draft": {"pending", "confirmed", "cancelled"},
    "pending": {"confirmed", "rejected", "cancelled"},
    "confirmed": {"arrived", "cancelled"},
    "arrived": {"loading", "cancelled"},
    "loading": {"loaded", "cancelled"},
    "loaded": {"shipped", "cancelled"},
}
```

Replace `add_payment` so it gates on `shipped` and updates payment status:

```python
@transaction.atomic
def add_payment(order: Order, amount, user, method="cash", status="confirmed") -> Payment:
    if order.status != "shipped":
        raise ValidationError(
            {"detail": "Оплата доступна только после отгрузки", "code": "payment_not_open"}
        )
    if amount is None or Decimal(str(amount)) <= 0:
        raise ValidationError(
            {"detail": "Сумма оплаты должна быть больше нуля", "code": "invalid_amount"}
        )
    payment = Payment.objects.create(
        order=order, amount=amount, method=method, status=status, recorded_by=user)
    log_event("payment", f"Оплата {amount} ({method}/{status})", user=user, order=order,
              payload={"amount": str(amount), "method": method, "status": status})
    _apply_payment_status(order, user)
    return payment
```

Replace `_maybe_mark_paid` with `_apply_payment_status`:

```python
def _apply_payment_status(order: Order, user) -> None:
    order.refresh_from_db()
    paid = order.paid_total
    if paid <= 0:
        new = "unpaid"
    elif paid >= order.total_amount:
        new = "settled"
    else:
        new = "partial"
    if new != order.payment_status:
        order.payment_status = new
        order.save(update_fields=["payment_status"])
        log_event("payment", f"Статус оплаты: {new}", user=user, order=order,
                  payload={"payment_status": new})
```

Update `confirm_payment` to call `_apply_payment_status(payment.order, user)` instead of `_maybe_mark_paid`. Update `approve_debt` to drop the `transition(order, "paid", ...)` call — debt no longer changes logistics status; instead it just records the override:

```python
@transaction.atomic
def approve_debt(order: Order, user) -> Order:
    order.debt_override = True
    order.debt_override_by = user
    order.settlement_intent = "debt"
    order.save(update_fields=["debt_override", "debt_override_by", "settlement_intent"])
    log_event("debt_override", "Долг одобрен", user=user, order=order)
    return order
```

- [ ] **Step 4: Update shipments service to not require `paid`**

In `backend/apps/shipments/services.py`:

`start_loading` — require `arrived`:

```python
@transaction.atomic
def start_loading(order, user):
    if order.status != "arrived":
        raise ValidationError(
            {"detail": "Загрузку можно начать только после въезда машины", "code": "invalid_status"}
        )
    shipment = _require_shipment(order)
    order.status = "loading"
    order.save(update_fields=["status"])
    log_event("loading_start", "Начата загрузка", user=user, order=order)
    return shipment
```

`record_count` — accept `arrived`/`loading`, promote `arrived`→`loading`:

```python
@transaction.atomic
def record_count(order, bags, user):
    if order.status in ("arrived", "loading"):
        shipment = _require_shipment(order)
    else:
        raise ValidationError(
            {"detail": "Подсчёт мешков возможен только во время загрузки",
             "code": "invalid_status"}
        )
    if order.status == "arrived":
        order.status = "loading"
        order.save(update_fields=["status"])
        log_event("loading_start", "Начата загрузка", user=user, order=order)
    shipment.bags_loaded = bags
    shipment.save(update_fields=["bags_loaded"])
    log_event("loading", f"Посчитано {bags} мешков", user=user, order=order,
              payload={"bags": bags})
    return shipment
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && pytest apps/orders/tests/test_payment_flow.py -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
cd backend && git add apps/orders/services.py apps/shipments/services.py apps/orders/tests/test_payment_flow.py
git commit -m "refactor: payment only after shipped, loading no longer gated by payment"
```

---

### Task 3: record_shipment marks the order as debt (payment_status unpaid)

**Files:**
- Modify: `backend/apps/shipments/services.py`
- Test: `backend/apps/shipments/tests/test_shipment_debt.py`

**Interfaces:**
- Consumes: `_apply_payment_status` is NOT used here; we set `payment_status` directly.
- Produces: after `record_shipment(order, user)`, `order.status == "shipped"` and `order.payment_status == "unpaid"`.

- [ ] **Step 1: Write the failing test**

Create `backend/apps/shipments/tests/test_shipment_debt.py`:

```python
import pytest
from decimal import Decimal
from apps.catalog.models import Product
from apps.clients.models import Client
from apps.orders.models import Order, OrderItem
from apps.warehouse.services import receive_stock
from apps.shipments.services import record_arrival, record_count, finish_loading, record_shipment

pytestmark = pytest.mark.django_db


def test_shipment_sets_unpaid_debt(boss):
    p = Product.objects.create(name="P", color="Red", weight_kg="50", price="100.00")
    receive_stock(p, 100, boss)
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    o = Order.objects.create(client=c, status="confirmed", truck_number="01A1")
    OrderItem.objects.create(order=o, product=p, quantity=2)
    record_arrival(o, Decimal("8000"), boss)
    record_count(o, 2, boss)
    finish_loading(o, boss)
    record_shipment(o, boss)
    o.refresh_from_db()
    assert o.status == "shipped"
    assert o.payment_status == "unpaid"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest apps/shipments/tests/test_shipment_debt.py -v`
Expected: FAIL only if payment_status not set — it defaults to `unpaid`, so this may PASS already. If it PASSES, still add the explicit set in Step 3 for clarity and an event-log entry, then keep the test.

- [ ] **Step 3: Set payment_status explicitly on shipment**

In `backend/apps/shipments/services.py` `record_shipment`, after `order.status = "shipped"`:

```python
    order.status = "shipped"
    order.payment_status = "unpaid"
    order.save(update_fields=["status", "payment_status"])
    log_event("debt", f"Заказ отгружен в долг: {order.total_amount}", user=user, order=order,
              payload={"amount": str(order.total_amount), "intent": order.settlement_intent})
```

(Replace the existing `order.status = "shipped"` / `order.save(update_fields=["status"])` lines.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest apps/shipments/tests/test_shipment_debt.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
cd backend && git add apps/shipments/services.py apps/shipments/tests/test_shipment_debt.py
git commit -m "feat(shipments): record_shipment fixes order as unpaid debt"
```

---

### Task 4: Notifications app

**Files:**
- Create: `backend/apps/notifications/__init__.py`
- Create: `backend/apps/notifications/apps.py`
- Create: `backend/apps/notifications/models.py`
- Create: `backend/apps/notifications/services.py`
- Create: `backend/apps/notifications/migrations/__init__.py`
- Modify: `backend/config/settings.py` (add `"apps.notifications"` to INSTALLED_APPS)
- Test: `backend/apps/notifications/tests/__init__.py`, `backend/apps/notifications/tests/test_notify.py`

**Interfaces:**
- Produces: `Notification` model (`client` FK → clients.Client, `text` str, `is_read` bool default False, `created_at`); `notify(client, text)` service returning the created `Notification`.

- [ ] **Step 1: Write the failing test**

Create `backend/apps/notifications/tests/__init__.py` (empty) and `backend/apps/notifications/tests/test_notify.py`:

```python
import pytest
from apps.clients.models import Client
from apps.notifications.services import notify
from apps.notifications.models import Notification

pytestmark = pytest.mark.django_db


def test_notify_creates_unread():
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    n = notify(c, "КАМАЗ 01A123 выехал")
    assert n.is_read is False
    assert Notification.objects.filter(client=c, text__icontains="01A123").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest apps/notifications/tests/test_notify.py -v`
Expected: FAIL (ModuleNotFoundError apps.notifications).

- [ ] **Step 3: Create the app files**

`backend/apps/notifications/__init__.py` — empty.
`backend/apps/notifications/migrations/__init__.py` — empty.

`backend/apps/notifications/apps.py`:

```python
from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.notifications"
```

`backend/apps/notifications/models.py`:

```python
from django.db import models


class Notification(models.Model):
    client = models.ForeignKey(
        "clients.Client", on_delete=models.CASCADE, related_name="notifications"
    )
    text = models.CharField(max_length=500)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
```

`backend/apps/notifications/services.py`:

```python
from .models import Notification


def notify(client, text: str) -> Notification:
    return Notification.objects.create(client=client, text=text)
```

In `backend/config/settings.py`, add `"apps.notifications",` to the INSTALLED_APPS list (after `"apps.portal",`).

- [ ] **Step 4: Make migration**

Run: `cd backend && python manage.py makemigrations notifications`
Expected: migration creating `Notification`.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && pytest apps/notifications/tests/test_notify.py -v`
Expected: PASS (1 passed).

- [ ] **Step 6: Commit**

```bash
cd backend && git add apps/notifications config/settings.py
git commit -m "feat(notifications): add Notification model + notify service"
```

---

### Task 5: set_truck_number sends a client notification

**Files:**
- Modify: `backend/apps/orders/services.py`
- Test: `backend/apps/orders/tests/test_truck_notify.py`

**Interfaces:**
- Consumes: `notify(client, text)` (Task 4).
- Produces: `set_truck_number(order, value, user)` creates a `Notification` for `order.client` mentioning the truck number.

- [ ] **Step 1: Write the failing test**

Create `backend/apps/orders/tests/test_truck_notify.py`:

```python
import pytest
from apps.clients.models import Client
from apps.orders.models import Order
from apps.orders.services import set_truck_number
from apps.notifications.models import Notification

pytestmark = pytest.mark.django_db


def test_set_truck_number_notifies_client(boss):
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    o = Order.objects.create(client=c, status="confirmed")
    set_truck_number(o, "01A123", boss)
    assert Notification.objects.filter(client=c, text__icontains="01A123").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest apps/orders/tests/test_truck_notify.py -v`
Expected: FAIL (no notification created).

- [ ] **Step 3: Add notify call to set_truck_number**

In `backend/apps/orders/services.py`, add the import at top:

```python
from apps.notifications.services import notify
```

In `set_truck_number`, after the existing `log_event(...)` call and before `return order`:

```python
    notify(order.client, f"Ваш КАМАЗ {value} отправляется")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest apps/orders/tests/test_truck_notify.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
cd backend && git add apps/orders/services.py apps/orders/tests/test_truck_notify.py
git commit -m "feat(orders): notify client when truck number is set"
```

---

### Task 6: Store model belonging to a Client

**Files:**
- Modify: `backend/apps/clients/models.py`
- Create: `backend/apps/clients/migrations/00XX_store.py` (via makemigrations)
- Test: `backend/apps/clients/tests/test_store_model.py`

**Interfaces:**
- Produces: `Store` model: `client` FK → Client (`related_name="stores"`), `name` str, `address` str (blank), `phone` str (blank), `payment_schedule_type` in `{none, monthly, weekly}` default `none`, `payment_days` JSONField (list of ints) default `list`, `contract_signed_at` DateField nullable. Constants `Store.SCHEDULE_TYPES = ["none", "monthly", "weekly"]`.

- [ ] **Step 1: Write the failing test**

Create `backend/apps/clients/tests/test_store_model.py`:

```python
import pytest
from apps.clients.models import Client, Store

pytestmark = pytest.mark.django_db


def test_store_belongs_to_client_with_schedule():
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    s = Store.objects.create(client=c, name="Магазин №1",
                             payment_schedule_type="monthly", payment_days=[5, 20])
    assert s in c.stores.all()
    assert s.payment_days == [5, 20]
    assert "monthly" in Store.SCHEDULE_TYPES
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest apps/clients/tests/test_store_model.py -v`
Expected: FAIL (ImportError Store).

- [ ] **Step 3: Add the Store model**

Append to `backend/apps/clients/models.py`:

```python
class Store(models.Model):
    SCHEDULE_TYPES = ["none", "monthly", "weekly"]

    client = models.ForeignKey(
        Client, on_delete=models.CASCADE, related_name="stores"
    )
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=300, blank=True, default="")
    phone = models.CharField(max_length=50, blank=True, default="")
    payment_schedule_type = models.CharField(max_length=20, default="none")
    payment_days = models.JSONField(default=list, blank=True)
    contract_signed_at = models.DateField(null=True, blank=True)

    def __str__(self):
        return self.name
```

- [ ] **Step 4: Make migration**

Run: `cd backend && python manage.py makemigrations clients`
Expected: migration creating `Store`.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && pytest apps/clients/tests/test_store_model.py -v`
Expected: PASS (1 passed).

- [ ] **Step 6: Commit**

```bash
cd backend && git add apps/clients/models.py apps/clients/migrations apps/clients/tests/test_store_model.py
git commit -m "feat(clients): add Store model with payment schedule"
```

---

### Task 7: Store payment-window service + overdue detector

**Files:**
- Create: `backend/apps/clients/services.py`
- Test: `backend/apps/clients/tests/test_store_schedule.py`

**Interfaces:**
- Consumes: `Store` (Task 6), `notify` (Task 4), `Order.store` (Task 8 — but this service only reads `store.payment_*`; it does not import Order, so order independence holds).
- Produces:
  - `is_payment_window_open(store, on_date) -> bool` — `True` when `payment_schedule_type == "none"`; for `monthly` when `on_date.day in payment_days`; for `weekly` when `on_date.isoweekday() in payment_days` (1=Mon..7=Sun).
  - `detect_overdue(store, on_date)` — when today is a payment day and the store has unpaid shipped orders, calls `notify(...)` once and returns the count of overdue orders. (Order import is done lazily inside the function to avoid an app-load cycle.)

- [ ] **Step 1: Write the failing test**

Create `backend/apps/clients/tests/test_store_schedule.py`:

```python
import pytest
from datetime import date
from apps.clients.models import Client, Store
from apps.clients.services import is_payment_window_open

pytestmark = pytest.mark.django_db


def test_window_none_always_open():
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    s = Store.objects.create(client=c, name="S", payment_schedule_type="none")
    assert is_payment_window_open(s, date(2026, 6, 24)) is True


def test_window_monthly():
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    s = Store.objects.create(client=c, name="S",
                             payment_schedule_type="monthly", payment_days=[5, 20])
    assert is_payment_window_open(s, date(2026, 6, 5)) is True
    assert is_payment_window_open(s, date(2026, 6, 6)) is False


def test_window_weekly():
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    s = Store.objects.create(client=c, name="S",
                             payment_schedule_type="weekly", payment_days=[1, 5])
    assert is_payment_window_open(s, date(2026, 6, 22)) is True   # Monday
    assert is_payment_window_open(s, date(2026, 6, 23)) is False  # Tuesday
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest apps/clients/tests/test_store_schedule.py -v`
Expected: FAIL (no module apps.clients.services).

- [ ] **Step 3: Implement the service**

Create `backend/apps/clients/services.py`:

```python
from apps.notifications.services import notify


def is_payment_window_open(store, on_date) -> bool:
    t = store.payment_schedule_type
    if t == "none":
        return True
    if t == "monthly":
        return on_date.day in (store.payment_days or [])
    if t == "weekly":
        return on_date.isoweekday() in (store.payment_days or [])
    return True


def detect_overdue(store, on_date) -> int:
    """On a payment day, notify about the store's unpaid shipped orders."""
    if not is_payment_window_open(store, on_date) or store.payment_schedule_type == "none":
        return 0
    from apps.orders.models import Order
    overdue = Order.objects.filter(
        store=store, status="shipped"
    ).exclude(payment_status="settled")
    count = overdue.count()
    if count:
        notify(store.client,
               f"Просрочка оплаты по магазину «{store.name}»: {count} заказ(ов)")
    return count
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest apps/clients/tests/test_store_schedule.py -v`
Expected: PASS (3 passed). (`detect_overdue` is covered in Task 8 after `Order.store` exists.)

- [ ] **Step 5: Commit**

```bash
cd backend && git add apps/clients/services.py apps/clients/tests/test_store_schedule.py
git commit -m "feat(clients): payment-window check + overdue detector"
```

---

### Task 8: Order.store FK + payment-window enforcement in add_payment

**Files:**
- Modify: `backend/apps/orders/models.py`
- Modify: `backend/apps/orders/services.py`
- Create: `backend/apps/orders/migrations/00XX_order_store.py` (via makemigrations)
- Test: `backend/apps/orders/tests/test_store_payment_window.py`

**Interfaces:**
- Consumes: `Store` (Task 6), `is_payment_window_open` (Task 7), `add_payment` (Task 2).
- Produces: `Order.store` FK → clients.Store, nullable. `add_payment` raises `ValidationError` code `payment_window_closed` when the order's store has a schedule and today is outside the window.

- [ ] **Step 1: Write the failing test**

Create `backend/apps/orders/tests/test_store_payment_window.py`:

```python
import pytest
from datetime import date
from unittest.mock import patch
from apps.catalog.models import Product
from apps.clients.models import Client, Store
from apps.orders.models import Order, OrderItem
from apps.orders.services import add_payment
from apps.clients.services import detect_overdue
from rest_framework.exceptions import ValidationError

pytestmark = pytest.mark.django_db


def _shipped_store_order():
    p = Product.objects.create(name="P", color="Red", weight_kg="50", price="100.00")
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    s = Store.objects.create(client=c, name="S",
                             payment_schedule_type="monthly", payment_days=[5])
    o = Order.objects.create(client=c, store=s, status="shipped")
    OrderItem.objects.create(order=o, product=p, quantity=1)  # total 100
    return o, s


def test_payment_blocked_outside_window(boss):
    o, s = _shipped_store_order()
    with patch("apps.orders.services.date") as d:
        d.today.return_value = date(2026, 6, 6)  # not the 5th
        with pytest.raises(ValidationError) as e:
            add_payment(o, "100", boss)
    assert e.value.detail["code"] == "payment_window_closed"


def test_payment_allowed_inside_window(boss):
    o, s = _shipped_store_order()
    with patch("apps.orders.services.date") as d:
        d.today.return_value = date(2026, 6, 5)
        add_payment(o, "100", boss)
    o.refresh_from_db()
    assert o.payment_status == "settled"


def test_detect_overdue_notifies(boss):
    o, s = _shipped_store_order()
    from apps.notifications.models import Notification
    assert detect_overdue(s, date(2026, 6, 5)) == 1
    assert Notification.objects.filter(client=s.client, text__icontains="Просрочка").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest apps/orders/tests/test_store_payment_window.py -v`
Expected: FAIL (Order has no `store`; window not enforced).

- [ ] **Step 3: Add Order.store**

In `backend/apps/orders/models.py`, add to `Order` (after `client`):

```python
    store = models.ForeignKey(
        "clients.Store", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="orders",
    )
```

- [ ] **Step 4: Enforce window in add_payment**

In `backend/apps/orders/services.py`, add imports at top:

```python
from datetime import date
from apps.clients.services import is_payment_window_open
```

In `add_payment`, right after the `if order.status != "shipped":` block:

```python
    if order.store and not is_payment_window_open(order.store, date.today()):
        raise ValidationError(
            {"detail": f"Оплата для магазина «{order.store.name}» сегодня недоступна",
             "code": "payment_window_closed"}
        )
```

- [ ] **Step 5: Make migration**

Run: `cd backend && python manage.py makemigrations orders`
Expected: migration adding `store` FK.

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && pytest apps/orders/tests/test_store_payment_window.py -v`
Expected: PASS (3 passed).

- [ ] **Step 7: Commit**

```bash
cd backend && git add apps/orders/models.py apps/orders/services.py apps/orders/migrations apps/orders/tests/test_store_payment_window.py
git commit -m "feat(orders): store FK + payment-window enforcement"
```

---

### Task 9: Store CRUD API (operator-managed) + Notifications read API

**Files:**
- Create: `backend/apps/clients/serializers.py` (add StoreSerializer — modify existing file)
- Modify: `backend/apps/clients/views.py`
- Modify: `backend/apps/clients/urls.py`
- Create: `backend/apps/notifications/serializers.py`
- Create: `backend/apps/notifications/views.py`
- Create: `backend/apps/notifications/urls.py`
- Modify: `backend/config/urls.py` (include notifications urls)
- Test: `backend/apps/clients/tests/test_store_api.py`

**Interfaces:**
- Consumes: `Store` (Task 6), `Notification` (Task 4), existing `PermViewSetMixin`.
- Produces: `StoreViewSet` at `/api/stores/` with perms keyed to `clients.*`; `NotificationViewSet` (list + mark-read) for client users at `/api/portal/notifications/`.

- [ ] **Step 1: Write the failing test**

Create `backend/apps/clients/tests/test_store_api.py`:

```python
import pytest
from rest_framework.test import APIClient
from apps.clients.models import Client

pytestmark = pytest.mark.django_db


def _api(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def test_create_store_via_api(manager):
    client = Client.objects.create(first_name="A", last_name="B", phone="x")
    r = _api(manager).post("/api/stores/", {
        "client": client.id, "name": "Магазин №1",
        "payment_schedule_type": "monthly", "payment_days": [5, 20],
    }, format="json")
    assert r.status_code == 201
    assert r.data["name"] == "Магазин №1"
    assert r.data["payment_days"] == [5, 20]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest apps/clients/tests/test_store_api.py -v`
Expected: FAIL (404 — no /api/stores/ route).

- [ ] **Step 3: Add StoreSerializer**

Append to `backend/apps/clients/serializers.py`:

```python
from .models import Store


class StoreSerializer(serializers.ModelSerializer):
    class Meta:
        model = Store
        fields = ["id", "client", "name", "address", "phone",
                  "payment_schedule_type", "payment_days", "contract_signed_at"]
```

- [ ] **Step 4: Add StoreViewSet and route**

In `backend/apps/clients/views.py`, append:

```python
from .models import Store
from .serializers import StoreSerializer


class StoreViewSet(PermViewSetMixin, viewsets.ModelViewSet):
    queryset = Store.objects.select_related("client").all()
    serializer_class = StoreSerializer
    required_perms = {
        "list": "clients.view", "retrieve": "clients.view",
        "create": "clients.create", "update": "clients.edit",
        "partial_update": "clients.edit", "destroy": "clients.delete",
    }
```

In `backend/apps/clients/urls.py`:

```python
from rest_framework.routers import DefaultRouter
from .views import ClientViewSet, StoreViewSet

router = DefaultRouter()
router.register("clients", ClientViewSet)
router.register("stores", StoreViewSet)
urlpatterns = router.urls
```

- [ ] **Step 5: Add Notifications API**

`backend/apps/notifications/serializers.py`:

```python
from rest_framework import serializers
from .models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ["id", "text", "is_read", "created_at"]
        read_only_fields = fields
```

`backend/apps/notifications/views.py`:

```python
from rest_framework import viewsets, mixins
from rest_framework.decorators import action
from rest_framework.response import Response
from apps.accounts.permissions import IsClientUser
from .models import Notification
from .serializers import NotificationSerializer


class NotificationViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = NotificationSerializer
    permission_classes = [IsClientUser]

    def get_queryset(self):
        return Notification.objects.filter(client__user=self.request.user)

    @action(detail=True, methods=["post"], url_path="read")
    def read(self, request, pk=None):
        n = self.get_object()
        n.is_read = True
        n.save(update_fields=["is_read"])
        return Response(self.get_serializer(n).data)
```

`backend/apps/notifications/urls.py`:

```python
from rest_framework.routers import DefaultRouter
from .views import NotificationViewSet

router = DefaultRouter()
router.register("portal/notifications", NotificationViewSet, basename="portal-notifications")
urlpatterns = router.urls
```

In `backend/config/urls.py`, add `path("api/", include("apps.notifications.urls"))` alongside the other app includes (match the existing include style in that file).

- [ ] **Step 6: Run test to verify it passes**

Run: `cd backend && pytest apps/clients/tests/test_store_api.py -v`
Expected: PASS (1 passed).

- [ ] **Step 7: Commit**

```bash
cd backend && git add apps/clients apps/notifications config/urls.py
git commit -m "feat: Store CRUD API + client notifications API"
```

---

### Task 10: Expose new order fields + fix existing tests/serializer; full suite green

**Files:**
- Modify: `backend/apps/orders/serializers.py`
- Modify: `backend/apps/shipments/tests/test_endpoints.py`
- Test: run the whole suite

**Interfaces:**
- Consumes: all prior tasks.
- Produces: `OrderSerializer` exposes `payment_status`, `settlement_intent`, `store`, `remaining_amount`; legacy shipment tests updated to the new flow (no `paid` status).

- [ ] **Step 1: Update existing shipment tests to the new flow**

Rewrite the helpers/tests in `backend/apps/shipments/tests/test_endpoints.py` that depend on `paid`:

- In `_order`, drop the `status in ("paid", ...)` Payment auto-create branch; payments now only happen after shipped.
- `test_finish_loading_endpoint`: confirmed → arrival → record_count → finish (no payment, no `_maybe_mark_paid`):

```python
def test_finish_loading_endpoint(boss):
    o = _order(boss, status="confirmed")
    record_arrival(o, Decimal("8000"), boss)
    record_count(o, 50, boss)
    r = _client(boss).post(f"/api/orders/{o.id}/finish-loading/")
    assert r.status_code == 200
    o.refresh_from_db()
    assert o.status == "loaded"
```

- `test_finish_loading_wrong_status_400`: use `status="arrived"` (loading not started):

```python
def test_finish_loading_wrong_status_400(boss):
    o = _order(boss, status="arrived")
    r = _client(boss).post(f"/api/orders/{o.id}/finish-loading/")
    assert r.status_code == 400
```

- `test_load_without_shipment_returns_400_and_keeps_status`: use `status="arrived"` with no shipment row. Since `record_arrival` creates the shipment, construct the bad state directly: create the order at `arrived` without calling `record_arrival`, expect 400 and unchanged status:

```python
def test_load_without_shipment_returns_400_and_keeps_status(boss):
    o = _order(boss, status="arrived")  # arrived but no Shipment row
    r = _client(boss).post(f"/api/orders/{o.id}/load/", {"bags": 10})
    assert r.status_code == 400
    o.refresh_from_db()
    assert o.status == "arrived"
```

Remove the now-unused `Payment` import if it is no longer referenced.

- [ ] **Step 2: Run the shipment endpoint tests**

Run: `cd backend && pytest apps/shipments/tests/test_endpoints.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 3: Expose new fields in OrderSerializer**

In `backend/apps/orders/serializers.py` `OrderSerializer`:

- Add read-only field declarations near the existing ones:

```python
    payment_status = serializers.CharField(read_only=True)
    settlement_intent = serializers.CharField()
    remaining_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
```

- Add `"payment_status"`, `"settlement_intent"`, `"store"`, `"remaining_amount"` to `Meta.fields`.
- In `Meta.extra_kwargs`, add `"store": {"required": False, "allow_null": True}`.

- [ ] **Step 4: Add a serializer test**

Create `backend/apps/orders/tests/test_order_serializer.py`:

```python
import pytest
from apps.clients.models import Client
from apps.orders.models import Order
from apps.orders.serializers import OrderSerializer

pytestmark = pytest.mark.django_db


def test_serializer_exposes_payment_fields():
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    o = Order.objects.create(client=c)
    data = OrderSerializer(o).data
    assert data["payment_status"] == "unpaid"
    assert data["settlement_intent"] == "debt"
    assert "remaining_amount" in data
```

- [ ] **Step 5: Run the full suite**

Run: `cd backend && pytest -q`
Expected: all tests pass. Fix any remaining `paid`-status references surfaced by failures (search: `grep -rn '"paid"' apps`).

- [ ] **Step 6: Commit**

```bash
cd backend && git add apps/orders/serializers.py apps/orders/tests/test_order_serializer.py apps/shipments/tests/test_endpoints.py
git commit -m "feat(orders): expose payment_status/settlement_intent/store; update flow tests"
```

---

## Notes for the implementer

- The frontend `debt_total` in `ClientSerializer.get_debt_total` filters on `o.is_fully_paid` and `o.status`; it keeps working since `is_fully_paid` is unchanged. No change required, but verify it still excludes settled orders sensibly after the suite is green.
- `create_client_payment` and portal `pay`/`request-debt` still reference `status == "arrived"`. These are client-facing instant-payment stubs; out of scope for this plan (payment moves to after shipped). Leave them but note in the PR that the portal pay flow needs a follow-up to align with the new `shipped`-gated payment. Do NOT delete them in this plan.
- Bank integration for `instant` settlement is a stub — no real bank call.
- Overdue detector cron wiring is out of scope; `detect_overdue` is callable/tested but not scheduled.
