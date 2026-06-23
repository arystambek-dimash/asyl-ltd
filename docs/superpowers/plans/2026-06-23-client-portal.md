# Client Portal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let clients self-register, place flour orders, wait for staff approval/rejection, pay (card / Kaspi QR / debt), then dispatch their own KAMAZ — all via a self-service portal.

**Architecture:** Django REST backend with all status transitions and permission checks centralized in `orders/services.py` (single source of truth, shared by portal and staff views). Next.js App Router frontend reusing existing UI components, with client-action logic in shared `lib/` helpers.

**Tech Stack:** Django, DRF, SimpleJWT, pytest; Next.js 15 App Router, TypeScript, Tailwind, Zustand, axios.

## Global Constraints

- Status set is fixed in `Order.STATUSES` (orders/models.py:7) — every new status must be added there.
- No logic duplication: status transitions, payment confirmation, and truck-number permission live ONLY in `orders/services.py` and are called by both portal and staff views.
- Backend is the source of truth for permissions; UI only reflects them.
- Money is `Decimal`; serialize as string.
- All user-facing copy in Russian, matching existing tone.
- Portal endpoints require `IsClientUser` and filter by `client__user=request.user`.
- Run backend tests with `cd backend && python -m pytest`.

---

## Task 1: Order model — new statuses & fields

**Files:**
- Modify: `backend/orders/models.py:7` (STATUSES), `:6-24` (Order fields)
- Test: `backend/orders/tests/test_models.py` (create)

**Interfaces:**
- Produces: `Order.STATUSES` includes `"pending"`, `"rejected"`. New fields: `Order.debt_requested: bool`, `Order.truck_number_set_by` (FK User, nullable). `Payment.method: str`, `Payment.status: str`, `Payment.confirmed_by` (FK, nullable), `Payment.confirmed_at` (datetime, nullable). `Order.paid_total` counts only `status="confirmed"` payments.

- [ ] **Step 1: Write the failing test**

Create `backend/orders/tests/__init__.py` (empty) and `backend/orders/tests/test_models.py`:

```python
import pytest
from decimal import Decimal
from clients.models import Client
from catalog.models import Product
from orders.models import Order, OrderItem, Payment


@pytest.fixture
def order(db):
    client = Client.objects.create(first_name="A", last_name="B", phone="1")
    p = Product.objects.create(name="Flour", color="Red", weight_kg=Decimal("50"), price=Decimal("100"))
    o = Order.objects.create(client=client, status="confirmed")
    OrderItem.objects.create(order=o, product=p, quantity=2)
    return o


def test_new_statuses_present():
    assert "pending" in Order.STATUSES
    assert "rejected" in Order.STATUSES


def test_paid_total_counts_only_confirmed(order):
    Payment.objects.create(order=order, amount=Decimal("50"), status="pending")
    Payment.objects.create(order=order, amount=Decimal("100"), status="confirmed")
    assert order.paid_total == Decimal("100")


def test_new_order_defaults(order):
    assert order.debt_requested is False
    assert order.truck_number_set_by is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest orders/tests/test_models.py -v`
Expected: FAIL (`pending` not in STATUSES / fields don't exist).

- [ ] **Step 3: Implement model changes**

In `backend/orders/models.py`, replace the `STATUSES` line and add fields:

```python
class Order(models.Model):
    STATUSES = ["draft", "pending", "confirmed", "paid", "arrived",
                "loading", "loaded", "shipped", "rejected", "cancelled"]

    client = models.ForeignKey(
        "clients.Client", on_delete=models.PROTECT, related_name="orders"
    )
    status = models.CharField(max_length=20, default="draft")
    truck_number = models.CharField(max_length=30, blank=True, default="")
    truck_number_set_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="truck_numbers_set",
    )
    arrival_date = models.DateField(null=True, blank=True)
    debt_requested = models.BooleanField(default=False)
    debt_override = models.BooleanField(default=False)
    debt_override_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="debt_overrides",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="created_orders",
    )
    created_at = models.DateTimeField(auto_now_add=True)
```

Update `paid_total` to count only confirmed payments:

```python
    @property
    def paid_total(self) -> Decimal:
        return sum((p.amount for p in self.payments.all() if p.status == "confirmed"), Decimal("0"))
```

Replace the `Payment` class:

```python
class Payment(models.Model):
    METHODS = ["cash", "card", "kaspi", "debt"]
    STATUSES = ["pending", "confirmed", "rejected"]

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="payments")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=10, default="cash")
    status = models.CharField(max_length=10, default="confirmed")
    paid_at = models.DateTimeField(auto_now_add=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="recorded_payments",
    )
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="confirmed_payments",
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
```

- [ ] **Step 4: Make migration and run tests**

Run: `cd backend && python manage.py makemigrations orders && python -m pytest orders/tests/test_models.py -v`
Expected: migration created; all 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/orders/models.py backend/orders/tests/ backend/orders/migrations/
git commit -m "feat(orders): add pending/rejected statuses and payment method/status fields"
```

---

## Task 2: Status transition state machine (services)

**Files:**
- Modify: `backend/orders/services.py`
- Test: `backend/orders/tests/test_transitions.py` (create)

**Interfaces:**
- Consumes: `Order` from Task 1.
- Produces:
  - `transition(order, to_status, user, message=None)` — validates the transition is allowed, sets status, logs event, returns order. Raises `ValidationError` on illegal transition.
  - `ALLOWED_TRANSITIONS: dict[str, set[str]]`
  - `confirm_order(order, user)` — now `pending|draft → confirmed`
  - `reject_order(order, user)` — `pending → rejected`

- [ ] **Step 1: Write the failing test**

Create `backend/orders/tests/test_transitions.py`:

```python
import pytest
from rest_framework.exceptions import ValidationError
from clients.models import Client
from orders.models import Order
from orders import services


@pytest.fixture
def make_order(db, make_user):
    def _make(status="pending"):
        c = Client.objects.create(first_name="A", last_name="B", phone="1")
        return Order.objects.create(client=c, status=status)
    return _make


def test_confirm_from_pending(make_order, make_user):
    o = make_order("pending")
    services.confirm_order(o, make_user())
    assert o.status == "confirmed"


def test_reject_from_pending(make_order, make_user):
    o = make_order("pending")
    services.reject_order(o, make_user())
    assert o.status == "rejected"


def test_cannot_reject_confirmed(make_order, make_user):
    o = make_order("confirmed")
    with pytest.raises(ValidationError):
        services.reject_order(o, make_user())


def test_transition_rejects_illegal(make_order, make_user):
    o = make_order("pending")
    with pytest.raises(ValidationError):
        services.transition(o, "shipped", make_user())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest orders/tests/test_transitions.py -v`
Expected: FAIL (`transition`/`reject_order` missing).

- [ ] **Step 3: Implement transitions**

Replace `confirm_order` and add new functions in `backend/orders/services.py` (keep `add_payment` for now; it is refactored in Task 3):

```python
ALLOWED_TRANSITIONS = {
    "draft": {"pending", "confirmed", "cancelled"},
    "pending": {"confirmed", "rejected", "cancelled"},
    "confirmed": {"paid", "cancelled"},
    "paid": {"arrived", "cancelled"},
    "arrived": {"loading", "cancelled"},
    "loading": {"loaded", "cancelled"},
    "loaded": {"shipped", "cancelled"},
}


@transaction.atomic
def transition(order: Order, to_status: str, user, message: str | None = None) -> Order:
    allowed = ALLOWED_TRANSITIONS.get(order.status, set())
    if to_status not in allowed:
        raise ValidationError(
            {"detail": f"Недопустимый переход: {order.status} → {to_status}",
             "code": "invalid_transition"})
    old = order.status
    order.status = to_status
    order.save(update_fields=["status"])
    log_event("status", message or f"Статус: {old} → {to_status}",
              user=user, order=order, payload={"from": old, "to": to_status})
    return order


@transaction.atomic
def confirm_order(order: Order, user) -> Order:
    if order.status not in ("draft", "pending"):
        raise ValidationError(
            {"detail": "Подтвердить можно только новый заказ", "code": "invalid_status"})
    return transition(order, "confirmed", user, "Заказ подтверждён")


@transaction.atomic
def reject_order(order: Order, user) -> Order:
    if order.status != "pending":
        raise ValidationError(
            {"detail": "Отклонить можно только заказ на рассмотрении", "code": "invalid_status"})
    return transition(order, "rejected", user, "Заказ отклонён")
```

- [ ] **Step 4: Run tests**

Run: `cd backend && python -m pytest orders/tests/test_transitions.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/orders/services.py backend/orders/tests/test_transitions.py
git commit -m "feat(orders): centralized status transition state machine"
```

---

## Task 3: Payment services (create / confirm / reject / approve debt)

**Files:**
- Modify: `backend/orders/services.py`
- Test: `backend/orders/tests/test_payments.py` (create)

**Interfaces:**
- Consumes: `transition`, `Order`, `Payment`.
- Produces:
  - `add_payment(order, amount, user, method="cash", status="confirmed")` — creates Payment; if it becomes confirmed and order fully paid while `confirmed`, transitions to `paid`.
  - `create_client_payment(order, method, user)` — creates `Payment(method, status="pending", amount=remaining)`; order must be `confirmed`.
  - `confirm_payment(payment, user)` — sets `status="confirmed"`, `confirmed_by/at`; auto-transitions order to `paid` if now fully paid.
  - `reject_payment(payment, user)` — sets `status="rejected"`.
  - `approve_debt(order, user)` — sets `debt_override=True`, `debt_override_by`, transitions `confirmed → paid`.

- [ ] **Step 1: Write the failing test**

Create `backend/orders/tests/test_payments.py`:

```python
import pytest
from decimal import Decimal
from rest_framework.exceptions import ValidationError
from clients.models import Client
from catalog.models import Product
from orders.models import Order, OrderItem, Payment
from orders import services


@pytest.fixture
def order(db):
    c = Client.objects.create(first_name="A", last_name="B", phone="1")
    p = Product.objects.create(name="F", color="Red", weight_kg=Decimal("50"), price=Decimal("100"))
    o = Order.objects.create(client=c, status="confirmed")
    OrderItem.objects.create(order=o, product=p, quantity=2)  # total 200
    return o


def test_client_payment_is_pending_and_does_not_pay(order, make_user):
    pay = services.create_client_payment(order, "kaspi", make_user(client=True))
    assert pay.status == "pending"
    assert pay.amount == Decimal("200")
    order.refresh_from_db()
    assert order.status == "confirmed"


def test_confirm_payment_marks_order_paid(order, make_user):
    pay = services.create_client_payment(order, "card", make_user(client=True))
    services.confirm_payment(pay, make_user(username="staff"))
    order.refresh_from_db()
    assert order.status == "paid"


def test_reject_payment_keeps_confirmed(order, make_user):
    pay = services.create_client_payment(order, "card", make_user(client=True))
    services.reject_payment(pay, make_user(username="staff"))
    pay.refresh_from_db(); order.refresh_from_db()
    assert pay.status == "rejected"
    assert order.status == "confirmed"


def test_approve_debt_marks_paid(order, make_user):
    services.approve_debt(order, make_user(username="boss"))
    order.refresh_from_db()
    assert order.status == "paid"
    assert order.debt_override is True


def test_client_payment_requires_confirmed(order, make_user):
    order.status = "pending"; order.save()
    with pytest.raises(ValidationError):
        services.create_client_payment(order, "card", make_user(client=True))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest orders/tests/test_payments.py -v`
Expected: FAIL (functions missing).

- [ ] **Step 3: Implement payment services**

Replace `add_payment` and add functions in `backend/orders/services.py`:

```python
from django.utils import timezone


@transaction.atomic
def add_payment(order: Order, amount, user, method="cash", status="confirmed") -> Payment:
    if amount is None or Decimal(str(amount)) <= 0:
        raise ValidationError(
            {"detail": "Сумма оплаты должна быть больше нуля", "code": "invalid_amount"})
    payment = Payment.objects.create(
        order=order, amount=amount, method=method, status=status, recorded_by=user)
    log_event("payment", f"Оплата {amount} ({method}/{status})", user=user, order=order,
              payload={"amount": str(amount), "method": method, "status": status})
    _maybe_mark_paid(order, user)
    return payment


@transaction.atomic
def create_client_payment(order: Order, method: str, user) -> Payment:
    if order.status != "confirmed":
        raise ValidationError(
            {"detail": "Оплата доступна только для подтверждённого заказа", "code": "invalid_status"})
    if method not in ("card", "kaspi"):
        raise ValidationError({"detail": "Недопустимый способ оплаты", "code": "bad_method"})
    remaining = order.total_amount - order.paid_total
    if remaining <= 0:
        raise ValidationError({"detail": "Заказ уже оплачен", "code": "already_paid"})
    payment = Payment.objects.create(
        order=order, amount=remaining, method=method, status="pending", recorded_by=user)
    log_event("payment", f"Клиент инициировал оплату {remaining} ({method})",
              user=user, order=order, payload={"amount": str(remaining), "method": method})
    return payment


@transaction.atomic
def confirm_payment(payment: Payment, user) -> Payment:
    payment.status = "confirmed"
    payment.confirmed_by = user
    payment.confirmed_at = timezone.now()
    payment.save(update_fields=["status", "confirmed_by", "confirmed_at"])
    log_event("payment", f"Оплата подтверждена {payment.amount}", user=user, order=payment.order,
              payload={"payment_id": payment.id, "amount": str(payment.amount)})
    _maybe_mark_paid(payment.order, user)
    return payment


@transaction.atomic
def reject_payment(payment: Payment, user) -> Payment:
    payment.status = "rejected"
    payment.save(update_fields=["status"])
    log_event("payment", f"Оплата отклонена {payment.amount}", user=user, order=payment.order,
              payload={"payment_id": payment.id})
    return payment


@transaction.atomic
def approve_debt(order: Order, user) -> Order:
    order.debt_override = True
    order.debt_override_by = user
    order.save(update_fields=["debt_override", "debt_override_by"])
    log_event("debt_override", "Долг одобрен", user=user, order=order)
    return transition(order, "paid", user, "Заказ готов (в долг)")


def _maybe_mark_paid(order: Order, user) -> None:
    order.refresh_from_db()
    if order.status == "confirmed" and order.is_fully_paid:
        transition(order, "paid", user, "Заказ оплачен")
```

- [ ] **Step 4: Run tests**

Run: `cd backend && python -m pytest orders/tests/test_payments.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/orders/services.py backend/orders/tests/test_payments.py
git commit -m "feat(orders): payment confirm/reject and debt-approval services"
```

---

## Task 4: Truck-number permission helper

**Files:**
- Modify: `backend/orders/services.py`
- Test: `backend/orders/tests/test_truck.py` (create)

**Interfaces:**
- Produces:
  - `can_set_truck_number(order, user) -> bool` — True if number unset, OR set by this same user, OR (set by staff AND user is staff).
  - `set_truck_number(order, value, user)` — validates via `can_set_truck_number`, raises `ValidationError(code="forbidden")` if not allowed; sets `truck_number` + `truck_number_set_by`, logs.

- [ ] **Step 1: Write the failing test**

Create `backend/orders/tests/test_truck.py`:

```python
import pytest
from rest_framework.exceptions import ValidationError
from clients.models import Client
from orders.models import Order
from orders import services


@pytest.fixture
def order(db):
    c = Client.objects.create(first_name="A", last_name="B", phone="1")
    return Order.objects.create(client=c, status="paid")


def test_client_sets_then_only_client_can_change(order, make_user):
    client = make_user(username="cli", client=True)
    staff = make_user(username="stf")
    services.set_truck_number(order, "777ABC", client)
    assert order.truck_number == "777ABC"
    # staff cannot overwrite client's number
    assert services.can_set_truck_number(order, staff) is False
    with pytest.raises(ValidationError):
        services.set_truck_number(order, "111XXX", staff)
    # same client can
    services.set_truck_number(order, "222YYY", client)
    assert order.truck_number == "222YYY"


def test_staff_set_can_be_changed_by_staff(order, make_user):
    s1 = make_user(username="s1")
    s2 = make_user(username="s2")
    services.set_truck_number(order, "AAA", s1)
    assert services.can_set_truck_number(order, s2) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest orders/tests/test_truck.py -v`
Expected: FAIL (functions missing).

- [ ] **Step 3: Implement helper**

Add to `backend/orders/services.py`:

```python
def can_set_truck_number(order: Order, user) -> bool:
    setter = order.truck_number_set_by
    if setter is None or not order.truck_number:
        return True
    if setter_id_matches(setter, user):
        return True
    # number owned by a client → only that client may change it
    if setter.is_client:
        return False
    # number set by staff → any staff may change it
    return not user.is_client


def setter_id_matches(setter, user) -> bool:
    return setter.id == user.id


@transaction.atomic
def set_truck_number(order: Order, value: str, user) -> Order:
    if not can_set_truck_number(order, user):
        raise ValidationError(
            {"detail": "Номер КАМАЗа задан другим пользователем", "code": "forbidden"})
    order.truck_number = value
    order.truck_number_set_by = user
    order.save(update_fields=["truck_number", "truck_number_set_by"])
    log_event("status", f"Номер КАМАЗа: {value}", user=user, order=order,
              payload={"truck_number": value})
    return order
```

- [ ] **Step 4: Run tests**

Run: `cd backend && python -m pytest orders/tests/test_truck.py -v`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/orders/services.py backend/orders/tests/test_truck.py
git commit -m "feat(orders): truck-number ownership permission helper"
```

---

## Task 5: RBAC permission `payments.confirm`

**Files:**
- Modify: `backend/rbac/perms.py:8` (payments actions), `:43-53` (presets)
- Test: `backend/rbac/tests/test_perms.py` (create)

**Interfaces:**
- Produces: code `payments.confirm` in `ALL_CODES`; present in Бухгалтер, Менеджер, Начальник presets.

- [ ] **Step 1: Write the failing test**

Create `backend/rbac/tests/__init__.py` (empty) and `backend/rbac/tests/test_perms.py`:

```python
from rbac.perms import ALL_CODES, PRESETS


def test_payments_confirm_exists():
    assert "payments.confirm" in ALL_CODES


def test_payments_confirm_in_presets():
    for role in ("Бухгалтер", "Менеджер", "Начальник"):
        assert "payments.confirm" in PRESETS[role]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest rbac/tests/test_perms.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `backend/rbac/perms.py`, change the payments section line:

```python
    "payments": ("Оплаты", ["view", "create", "confirm"]),
```

Update presets to include `payments.confirm`:

```python
PRESETS = {
    "Менеджер": _codes("catalog", "clients", "orders",
                       "payments.view", "payments.confirm", "reports.view", "events.view"),
    "Бухгалтер": _codes("payments.view", "payments.create", "payments.confirm",
                        "orders.view", "clients.view", "reports.view", "events.view"),
    "Оператор": _codes("shipping.view", "shipping.arrive", "shipping.load",
                       "shipping.ship", "orders.view", "warehouse.view", "events.view"),
    "Начальник": _codes("catalog", "clients", "orders", "payments.view",
                        "payments.create", "payments.confirm", "warehouse", "shipping",
                        "cameras", "reports.view", "events.view"),
}
```

- [ ] **Step 4: Run tests**

Run: `cd backend && python -m pytest rbac/tests/test_perms.py -v`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/rbac/perms.py backend/rbac/tests/
git commit -m "feat(rbac): add payments.confirm permission"
```

---

## Task 6: Staff order endpoints (reject / payment confirm-reject / approve-debt)

**Files:**
- Modify: `backend/orders/views.py`, `backend/orders/serializers.py:14-18` (PaymentSerializer fields)
- Test: `backend/orders/tests/test_staff_api.py` (create)

**Interfaces:**
- Consumes: services from Tasks 2-3; `manager`, `boss`, `accountant`, `auth_client` fixtures (conftest.py).
- Produces endpoints:
  - `POST /api/orders/{id}/reject/` (`orders.confirm`)
  - `POST /api/orders/{id}/payments/{pid}/confirm/` (`payments.confirm`)
  - `POST /api/orders/{id}/payments/{pid}/reject/` (`payments.confirm`)
  - `POST /api/orders/{id}/approve-debt/` (`shipping.debt_override`)
- `PaymentSerializer` exposes `method`, `status`, `confirmed_by`.

- [ ] **Step 1: Write the failing test**

Create `backend/orders/tests/test_staff_api.py`:

```python
import pytest
from decimal import Decimal
from clients.models import Client
from catalog.models import Product
from orders.models import Order, OrderItem
from orders import services


@pytest.fixture
def confirmed_order(db, make_user):
    c = Client.objects.create(first_name="A", last_name="B", phone="1")
    p = Product.objects.create(name="F", color="Red", weight_kg=Decimal("50"), price=Decimal("100"))
    o = Order.objects.create(client=c, status="confirmed")
    OrderItem.objects.create(order=o, product=p, quantity=1)
    return o


def test_reject_endpoint(db, manager, auth_client):
    c = Client.objects.create(first_name="A", last_name="B", phone="1")
    o = Order.objects.create(client=c, status="pending")
    r = auth_client(manager).post(f"/api/orders/{o.id}/reject/")
    assert r.status_code == 200
    o.refresh_from_db(); assert o.status == "rejected"


def test_confirm_payment_endpoint(confirmed_order, accountant, auth_client, make_user):
    pay = services.create_client_payment(confirmed_order, "card", make_user(client=True))
    r = auth_client(accountant).post(
        f"/api/orders/{confirmed_order.id}/payments/{pay.id}/confirm/")
    assert r.status_code == 200
    confirmed_order.refresh_from_db(); assert confirmed_order.status == "paid"


def test_approve_debt_endpoint(confirmed_order, boss, auth_client):
    r = auth_client(boss).post(f"/api/orders/{confirmed_order.id}/approve-debt/")
    assert r.status_code == 200
    confirmed_order.refresh_from_db()
    assert confirmed_order.status == "paid" and confirmed_order.debt_override is True
```

Note: `accountant` and `boss` fixtures need the new codes. Update conftest.py: add `"payments.confirm"` to the `accountant` fixture codes, and `boss` already has `shipping.debt_override`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest orders/tests/test_staff_api.py -v`
Expected: FAIL (404 on new routes).

- [ ] **Step 3: Implement**

Update `accountant` fixture in `backend/conftest.py`:

```python
@pytest.fixture
def accountant(user_with_perms):
    return user_with_perms("accountant", codes=["payments.view", "payments.create",
                                                "payments.confirm", "orders.view"])
```

In `backend/orders/serializers.py`, expand `PaymentSerializer`:

```python
class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = ["id", "order", "amount", "method", "status",
                  "paid_at", "recorded_by", "confirmed_by"]
        read_only_fields = ["order", "paid_at", "recorded_by", "confirmed_by"]
```

In `backend/orders/views.py`, update imports, `required_perms`, and add actions:

```python
from .models import Order, Payment
from .serializers import OrderSerializer, PaymentSerializer
from .services import (add_payment, confirm_order, reject_order,
                       confirm_payment, reject_payment, approve_debt)
```

Add to `required_perms`:

```python
        "reject": "orders.confirm",
        "confirm_payment": "payments.confirm",
        "reject_payment": "payments.confirm",
        "approve_debt": "shipping.debt_override",
```

Add actions to `OrderViewSet`:

```python
    @action(detail=True, methods=["post"], url_path="reject")
    def reject(self, request, pk=None):
        order = reject_order(self.get_object(), request.user)
        return Response(OrderSerializer(order, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="payments/(?P<pid>[^/.]+)/confirm")
    def confirm_payment(self, request, pk=None, pid=None):
        payment = Payment.objects.get(pk=pid, order=self.get_object())
        confirm_payment(payment, request.user)
        return Response(OrderSerializer(payment.order, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="payments/(?P<pid>[^/.]+)/reject")
    def reject_payment(self, request, pk=None, pid=None):
        payment = Payment.objects.get(pk=pid, order=self.get_object())
        reject_payment(payment, request.user)
        return Response(OrderSerializer(payment.order, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="approve-debt")
    def approve_debt(self, request, pk=None):
        order = approve_debt(self.get_object(), request.user)
        return Response(OrderSerializer(order, context={"request": request}).data)
```

- [ ] **Step 4: Run tests**

Run: `cd backend && python -m pytest orders/tests/test_staff_api.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/orders/views.py backend/orders/serializers.py backend/conftest.py backend/orders/tests/test_staff_api.py
git commit -m "feat(orders): staff reject / payment confirm-reject / approve-debt endpoints"
```

---

## Task 7: Public client registration endpoint

**Files:**
- Create: `backend/portal/registration.py` (serializer + view)
- Modify: `backend/portal/urls.py`
- Test: `backend/portal/tests/test_register.py` (create)

**Interfaces:**
- Produces: `POST /api/portal/register/` (AllowAny) → `{access, refresh}`. Creates `User(is_client=True)` + `Client(user=...)` in a transaction.

- [ ] **Step 1: Write the failing test**

Create `backend/portal/tests/__init__.py` (empty) and `backend/portal/tests/test_register.py`:

```python
import pytest
from django.contrib.auth import get_user_model
from clients.models import Client


@pytest.mark.django_db
def test_register_creates_client_user_and_returns_tokens(api_client):
    payload = {"username": "newcli", "password": "secret12345",
               "first_name": "Иван", "last_name": "Петров",
               "phone": "+77001112233", "iin": "990101300123"}
    r = api_client.post("/api/portal/register/", payload, format="json")
    assert r.status_code == 201
    assert "access" in r.data and "refresh" in r.data
    user = get_user_model().objects.get(username="newcli")
    assert user.is_client is True
    assert Client.objects.filter(user=user, first_name="Иван").exists()


@pytest.mark.django_db
def test_register_duplicate_username_rejected(api_client, make_user):
    make_user(username="taken")
    r = api_client.post("/api/portal/register/",
                        {"username": "taken", "password": "secret12345",
                         "first_name": "A", "last_name": "B", "phone": "1"},
                        format="json")
    assert r.status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest portal/tests/test_register.py -v`
Expected: FAIL (404).

- [ ] **Step 3: Implement**

Create `backend/portal/registration.py`:

```python
from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework import serializers, generics
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from clients.models import Client

User = get_user_model()


class RegisterSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(min_length=8, write_only=True)
    first_name = serializers.CharField(max_length=100)
    last_name = serializers.CharField(max_length=100)
    phone = serializers.CharField(max_length=50)
    iin = serializers.CharField(max_length=20, required=False, allow_blank=True)

    def validate_username(self, value):
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("Это имя пользователя уже занято")
        return value

    @transaction.atomic
    def create(self, data):
        user = User.objects.create_user(
            username=data["username"], password=data["password"], is_client=True)
        Client.objects.create(
            user=user, first_name=data["first_name"], last_name=data["last_name"],
            phone=data["phone"], iin=data.get("iin", ""))
        return user


class RegisterView(generics.CreateAPIView):
    permission_classes = [AllowAny]
    serializer_class = RegisterSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        refresh = RefreshToken.for_user(user)
        return Response(
            {"access": str(refresh.access_token), "refresh": str(refresh)}, status=201)
```

Modify `backend/portal/urls.py`:

```python
from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import PortalCatalogViewSet, PortalOrderViewSet
from .registration import RegisterView

router = DefaultRouter()
router.register("portal/catalog", PortalCatalogViewSet, basename="portal-catalog")
router.register("portal/orders", PortalOrderViewSet, basename="portal-orders")
urlpatterns = router.urls + [
    path("portal/register/", RegisterView.as_view(), name="portal-register"),
]
```

- [ ] **Step 4: Run tests**

Run: `cd backend && python -m pytest portal/tests/test_register.py -v`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/portal/registration.py backend/portal/urls.py backend/portal/tests/
git commit -m "feat(portal): public client registration with auto-login"
```

---

## Task 8: Portal order actions (create→pending, pay, request-debt, truck, payment-info)

**Files:**
- Modify: `backend/portal/views.py`, `backend/portal/serializers.py`
- Modify: `backend/config/settings.py` (add `KASPI_QR` / payment info constants)
- Test: `backend/portal/tests/test_portal_actions.py` (create)

**Interfaces:**
- Consumes: services `create_client_payment`, `set_truck_number`, `transition`.
- Produces:
  - `POST /api/portal/orders/` → status `pending`
  - `POST /api/portal/orders/{id}/pay/` `{method}` (owner, confirmed)
  - `POST /api/portal/orders/{id}/request-debt/` (owner, confirmed)
  - `PATCH /api/portal/orders/{id}/truck/` `{truck_number}` (owner, paid)
  - `GET /api/portal/payment-info/` → `{kaspi_qr, bank, account, instructions}`
  - `PortalOrderSerializer` exposes `truck_number`, `debt_requested`, `debt_override`.

- [ ] **Step 1: Write the failing test**

Create `backend/portal/tests/test_portal_actions.py`:

```python
import pytest
from decimal import Decimal
from clients.models import Client
from catalog.models import Product
from orders.models import Order, OrderItem


@pytest.fixture
def client_and_order(db, make_user):
    user = make_user(username="cli", client=True)
    c = Client.objects.create(user=user, first_name="A", last_name="B", phone="1")
    p = Product.objects.create(name="F", color="Red", weight_kg=Decimal("50"), price=Decimal("100"))
    o = Order.objects.create(client=c, status="confirmed")
    OrderItem.objects.create(order=o, product=p, quantity=1)
    return user, o


def test_create_order_is_pending(db, make_user, auth_client):
    user = make_user(username="cli", client=True)
    Client.objects.create(user=user, first_name="A", last_name="B", phone="1")
    p = Product.objects.create(name="F", color="Red", weight_kg=Decimal("50"), price=Decimal("100"))
    r = auth_client(user).post("/api/portal/orders/",
                               {"items": [{"product": p.id, "quantity": 2}]}, format="json")
    assert r.status_code == 201
    assert Order.objects.get(id=r.data["id"]).status == "pending"


def test_pay_creates_pending_payment(client_and_order, auth_client):
    user, o = client_and_order
    r = auth_client(user).post(f"/api/portal/orders/{o.id}/pay/", {"method": "kaspi"}, format="json")
    assert r.status_code == 201
    assert o.payments.filter(status="pending", method="kaspi").exists()


def test_request_debt(client_and_order, auth_client):
    user, o = client_and_order
    r = auth_client(user).post(f"/api/portal/orders/{o.id}/request-debt/")
    assert r.status_code == 200
    o.refresh_from_db(); assert o.debt_requested is True


def test_truck_only_after_paid(client_and_order, auth_client):
    user, o = client_and_order  # status confirmed, not paid
    r = auth_client(user).patch(f"/api/portal/orders/{o.id}/truck/",
                                {"truck_number": "777"}, format="json")
    assert r.status_code == 409


def test_truck_set_when_paid(client_and_order, auth_client):
    user, o = client_and_order
    o.status = "paid"; o.save()
    r = auth_client(user).patch(f"/api/portal/orders/{o.id}/truck/",
                                {"truck_number": "777ABC"}, format="json")
    assert r.status_code == 200
    o.refresh_from_db(); assert o.truck_number == "777ABC"


def test_cannot_touch_other_clients_order(db, make_user, auth_client):
    owner = make_user(username="owner", client=True)
    Client.objects.create(user=owner, first_name="O", last_name="W", phone="1")
    other = make_user(username="other", client=True)
    Client.objects.create(user=other, first_name="X", last_name="Y", phone="2")
    c = Client.objects.get(user=owner)
    o = Order.objects.create(client=c, status="confirmed")
    r = auth_client(other).post(f"/api/portal/orders/{o.id}/pay/", {"method": "card"}, format="json")
    assert r.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest portal/tests/test_portal_actions.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `backend/config/settings.py`, append payment-info constants near the end:

```python
PORTAL_PAYMENT_INFO = {
    "kaspi_qr": "",  # static QR image URL or payload string, fill in production
    "bank": "Kaspi Bank",
    "account": "",
    "instructions": "Отсканируйте QR в приложении Kaspi и переведите сумму к оплате.",
}
```

In `backend/portal/serializers.py`, update `PortalOrderSerializer` Meta fields and create method:

```python
    class Meta:
        model = Order
        fields = ["id", "status", "items", "total_amount", "paid_total",
                  "truck_number", "debt_requested", "debt_override", "created_at"]
        read_only_fields = ["status", "truck_number", "debt_requested", "debt_override"]

    def create(self, validated_data):
        items = validated_data.pop("items")
        client = self.context["request"].user.client_profile
        order = Order.objects.create(client=client, status="pending")
        for item in items:
            OrderItem.objects.create(order=order, **item)
        return order
```

Rewrite `backend/portal/views.py`:

```python
from django.conf import settings
from rest_framework import viewsets, mixins
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from accounts.permissions import IsClientUser
from catalog.models import Product
from orders.models import Order
from orders.services import create_client_payment, set_truck_number
from eventlog.services import log_event
from .serializers import CatalogProductSerializer, PortalOrderSerializer


class PortalCatalogViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = CatalogProductSerializer
    permission_classes = [IsClientUser]
    queryset = Product.objects.filter(is_active=True)


class PortalOrderViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin,
                         mixins.CreateModelMixin, viewsets.GenericViewSet):
    serializer_class = PortalOrderSerializer
    permission_classes = [IsClientUser]

    def get_queryset(self):
        return Order.objects.filter(
            client__user=self.request.user
        ).prefetch_related("items__product")

    @action(detail=True, methods=["post"], url_path="pay")
    def pay(self, request, pk=None):
        order = self.get_object()
        create_client_payment(order, request.data.get("method"), request.user)
        return Response(self.get_serializer(order).data, status=201)

    @action(detail=True, methods=["post"], url_path="request-debt")
    def request_debt(self, request, pk=None):
        order = self.get_object()
        if order.status != "confirmed":
            raise ValidationError({"detail": "Долг доступен только для подтверждённого заказа",
                                   "code": "invalid_status"})
        order.debt_requested = True
        order.save(update_fields=["debt_requested"])
        log_event("debt_override", "Клиент запросил долг", user=request.user, order=order)
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=["patch"], url_path="truck")
    def truck(self, request, pk=None):
        order = self.get_object()
        if order.status != "paid":
            raise ValidationError({"detail": "Номер КАМАЗа доступен после оплаты",
                                   "code": "invalid_status"})
        value = (request.data.get("truck_number") or "").strip()
        if not value:
            raise ValidationError({"detail": "Введите номер КАМАЗа", "code": "empty"})
        set_truck_number(order, value, request.user)
        return Response(self.get_serializer(order).data)


@api_view(["GET"])
@permission_classes([IsClientUser])
def payment_info(request):
    return Response(settings.PORTAL_PAYMENT_INFO)
```

The `ValidationError` with `code="invalid_status"` returns HTTP 400 by default, but the test expects 409 for truck-before-paid. Add a small exception class. In `backend/portal/views.py` add at top:

```python
from rest_framework.exceptions import APIException


class Conflict(APIException):
    status_code = 409
    default_code = "conflict"
```

And in the `truck` action replace the status check raise with:

```python
        if order.status != "paid":
            raise Conflict({"detail": "Номер КАМАЗа доступен после оплаты",
                            "code": "invalid_status"})
```

Register the `payment_info` route in `backend/portal/urls.py`:

```python
from .views import PortalCatalogViewSet, PortalOrderViewSet, payment_info
...
urlpatterns = router.urls + [
    path("portal/register/", RegisterView.as_view(), name="portal-register"),
    path("portal/payment-info/", payment_info, name="portal-payment-info"),
]
```

- [ ] **Step 4: Run tests**

Run: `cd backend && python -m pytest portal/tests/ orders/tests/ -v`
Expected: all PASS (full backend suite green).

- [ ] **Step 5: Commit**

```bash
git add backend/portal/ backend/config/settings.py
git commit -m "feat(portal): order pay/request-debt/truck actions + payment-info"
```

---

## Task 9: Frontend — constants, types, API helpers

**Files:**
- Modify: `frontend/src/lib/constants.ts`, `frontend/src/lib/types.ts`
- Create: `frontend/src/lib/portal-actions.ts`

**Interfaces:**
- Produces:
  - `ORDER_STATUS_LABELS`/`ORDER_STATUS_TONE` gain `pending`, `rejected`.
  - `Order` type gains `debt_requested: boolean`, `truck_number_set_by?: number | null`.
  - `Payment` type gains `method: string`, `status: string`.
  - `portal-actions.ts`: `payOrder(id, method)`, `requestDebt(id)`, `setTruck(id, truck_number)`, `getPaymentInfo()`, `registerClient(payload)`; and `clientStep(status): "pending"|"pay"|"rejected"|"truck"|"shipping"|"done"`.

- [ ] **Step 1: Add constants**

In `frontend/src/lib/constants.ts`, extend the two maps:

```typescript
export const ORDER_STATUS_LABELS: Record<string, string> = {
  draft: "Черновик",
  pending: "На рассмотрении",
  confirmed: "Ожидает оплаты",
  paid: "Оплачен",
  arrived: "Прибыл",
  loading: "Загрузка",
  loaded: "Загружен",
  shipped: "Отгружен",
  rejected: "Отклонён",
  cancelled: "Отменён",
};

export const ORDER_STATUS_TONE: Record<string, "muted" | "primary" | "success" | "warning" | "destructive"> = {
  draft: "muted",
  pending: "warning",
  confirmed: "warning",
  paid: "primary",
  arrived: "warning",
  loading: "warning",
  loaded: "primary",
  shipped: "success",
  rejected: "destructive",
  cancelled: "destructive",
};
```

- [ ] **Step 2: Extend types**

In `frontend/src/lib/types.ts`, update `Order` and `Payment`:

```typescript
export interface Order {
  id: number; client: number; client_name?: string; client_phone?: string;
  status: string; truck_number: string; truck_number_set_by?: number | null;
  arrival_date?: string | null;
  items: OrderItem[]; total_amount: string; paid_total: string;
  is_fully_paid: boolean; debt_override: boolean; debt_requested?: boolean;
  weigh_in_kg?: string | null; weigh_out_kg?: string | null; net_weight_kg?: string | null;
  bags_loaded?: number; bag_estimate_kg?: string;
  bag_weight_kg?: string; debt_override_by_name?: string | null;
  created_at: string;
}
export interface Payment {
  id: number; order: number; amount: string; method: string; status: string;
  paid_at: string; recorded_by: number | null;
}
```

- [ ] **Step 3: Create portal-actions helper**

Create `frontend/src/lib/portal-actions.ts`:

```typescript
import { api } from "@/lib/api";
import type { Order } from "@/lib/types";

export interface PaymentInfo {
  kaspi_qr: string; bank: string; account: string; instructions: string;
}
export interface RegisterPayload {
  username: string; password: string; first_name: string;
  last_name: string; phone: string; iin?: string;
}

export const payOrder = (id: number, method: "card" | "kaspi") =>
  api.post<Order>(`/portal/orders/${id}/pay/`, { method }).then((r) => r.data);

export const requestDebt = (id: number) =>
  api.post<Order>(`/portal/orders/${id}/request-debt/`).then((r) => r.data);

export const setTruck = (id: number, truck_number: string) =>
  api.patch<Order>(`/portal/orders/${id}/truck/`, { truck_number }).then((r) => r.data);

export const getPaymentInfo = () =>
  api.get<PaymentInfo>("/portal/payment-info/").then((r) => r.data);

export const registerClient = (payload: RegisterPayload) =>
  api.post<{ access: string; refresh: string }>("/portal/register/", payload).then((r) => r.data);

export type ClientStep = "pending" | "pay" | "rejected" | "truck" | "shipping" | "done";

export function clientStep(status: string): ClientStep {
  if (status === "pending" || status === "draft") return "pending";
  if (status === "confirmed") return "pay";
  if (status === "rejected" || status === "cancelled") return "rejected";
  if (status === "paid") return "truck";
  if (status === "shipped") return "done";
  return "shipping"; // arrived/loading/loaded
}
```

- [ ] **Step 4: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/constants.ts frontend/src/lib/types.ts frontend/src/lib/portal-actions.ts
git commit -m "feat(portal-fe): status constants, types, action helpers"
```

---

## Task 10: Frontend — registration page

**Files:**
- Create: `frontend/src/app/register/page.tsx`
- Modify: `frontend/src/app/login/page.tsx` (add link to register)

**Interfaces:**
- Consumes: `registerClient` (Task 9), `useAuth` store, `setTokens`.

- [ ] **Step 1: Create the page**

Create `frontend/src/app/register/page.tsx`:

```tsx
"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { setTokens } from "@/lib/api";
import { apiError } from "@/lib/api";
import { useAuth } from "@/store/auth";
import { registerClient } from "@/lib/portal-actions";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export default function RegisterPage() {
  const router = useRouter();
  const { loadMe } = useAuth();
  const [f, setF] = useState({ username: "", password: "", first_name: "",
    last_name: "", phone: "", iin: "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const upd = (k: string) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setF({ ...f, [k]: e.target.value });

  async function submit(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    try {
      const { access, refresh } = await registerClient(f);
      setTokens(access, refresh);
      await loadMe();
      router.replace("/portal/catalog");
    } catch (err) { setError(apiError(err)); }
    finally { setBusy(false); }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--muted)]/40 p-4">
      <div className="w-full max-w-sm animate-fade-up">
        <h1 className="mb-6 text-center text-xl font-bold">Регистрация клиента</h1>
        <div className="rounded-xl border bg-[var(--card)] p-6 shadow-sm">
          <form onSubmit={submit} className="flex flex-col gap-3">
            <div className="flex flex-col gap-1.5"><Label>Имя</Label>
              <Input value={f.first_name} onChange={upd("first_name")} required /></div>
            <div className="flex flex-col gap-1.5"><Label>Фамилия</Label>
              <Input value={f.last_name} onChange={upd("last_name")} required /></div>
            <div className="flex flex-col gap-1.5"><Label>Телефон</Label>
              <Input value={f.phone} onChange={upd("phone")} required /></div>
            <div className="flex flex-col gap-1.5"><Label>ИИН/БИН</Label>
              <Input value={f.iin} onChange={upd("iin")} /></div>
            <div className="flex flex-col gap-1.5"><Label>Логин</Label>
              <Input value={f.username} onChange={upd("username")} required /></div>
            <div className="flex flex-col gap-1.5"><Label>Пароль</Label>
              <Input type="password" value={f.password} onChange={upd("password")}
                minLength={8} required /></div>
            {error && <p className="rounded-md bg-[var(--destructive)]/10 px-3 py-2 text-sm text-[var(--destructive)]">{error}</p>}
            <Button type="submit" disabled={busy} className="mt-1">
              {busy ? "Регистрация…" : "Зарегистрироваться"}</Button>
            <Link href="/login" className="text-center text-sm text-[var(--muted-foreground)] underline">
              Уже есть аккаунт? Войти</Link>
          </form>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Add register link to login page**

In `frontend/src/app/login/page.tsx`, after the closing `</form>` (line 73) and before `</div>` add:

```tsx
            <Link href="/register" className="mt-4 block text-center text-sm text-[var(--muted-foreground)] underline">
              Нет аккаунта? Регистрация
            </Link>
```

And add the import at top: `import Link from "next/link";`

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/app/register/page.tsx frontend/src/app/login/page.tsx
git commit -m "feat(portal-fe): client registration page"
```

---

## Task 11: Frontend — client order detail page with contextual actions

**Files:**
- Create: `frontend/src/app/portal/orders/[id]/page.tsx`
- Modify: `frontend/src/app/portal/orders/page.tsx` (link rows to detail)

**Interfaces:**
- Consumes: `useApi`, `clientStep`, `payOrder`, `requestDebt`, `setTruck`, `getPaymentInfo`, `ORDER_STATUS_LABELS`, `StatusBadge`.

- [ ] **Step 1: Make order rows link to detail**

In `frontend/src/app/portal/orders/page.tsx`, wrap the order number cell in a link. Replace the `<TD className="font-medium">#{o.id}</TD>` line with:

```tsx
                    <TD className="font-medium">
                      <Link href={`/portal/orders/${o.id}`} className="underline">#{o.id}</Link>
                    </TD>
```

(`Link` is already imported.)

- [ ] **Step 2: Create the detail page**

Create `frontend/src/app/portal/orders/[id]/page.tsx`:

```tsx
"use client";
import { use, useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { StatusBadge } from "@/components/status-badge";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { useApi } from "@/lib/use-api";
import { apiError } from "@/lib/api";
import { formatMoney } from "@/lib/utils";
import { clientStep, payOrder, requestDebt, setTruck, getPaymentInfo, type PaymentInfo } from "@/lib/portal-actions";
import type { Order } from "@/lib/types";

export default function PortalOrderDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data: order, loading, reload } = useApi<Order>(`/portal/orders/${id}/`);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [truck, setTruckVal] = useState("");
  const [info, setInfo] = useState<PaymentInfo | null>(null);

  async function run(fn: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await fn(); reload(); } catch (e) { setError(apiError(e)); } finally { setBusy(false); }
  }

  if (loading || !order) return <AppShell title="Заказ" portal><p className="py-6 text-center text-sm text-[var(--muted-foreground)]">Загрузка…</p></AppShell>;

  const step = clientStep(order.status);
  const remaining = Number(order.total_amount) - Number(order.paid_total);

  return (
    <AppShell title={`Заказ #${order.id}`} portal>
      <div className="flex flex-col gap-4 max-w-2xl">
        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle>Заказ #{order.id}</CardTitle>
            <StatusBadge status={order.status} />
          </CardHeader>
          <CardContent>
            <Table>
              <THead><TR><TH>Товар</TH><TH>Мешков</TH></TR></THead>
              <TBody>{order.items.map((it) => (
                <TR key={it.id}><TD>{it.product_label}</TD><TD>{it.quantity}</TD></TR>
              ))}</TBody>
            </Table>
            <div className="mt-4 flex justify-between border-t pt-3 text-sm">
              <span className="text-[var(--muted-foreground)]">Итого</span>
              <span className="font-bold tabular-nums">{formatMoney(order.total_amount)} ₸</span>
            </div>
          </CardContent>
        </Card>

        {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}

        {step === "pending" && (
          <Card><CardContent className="py-6 text-center text-sm text-[var(--muted-foreground)]">
            Заказ на рассмотрении. Ожидайте решения.</CardContent></Card>
        )}

        {step === "rejected" && (
          <Card><CardContent className="py-6 text-center text-sm text-[var(--destructive)]">
            Заказ отклонён.</CardContent></Card>
        )}

        {step === "pay" && (
          <Card>
            <CardHeader><CardTitle>Оплата · к оплате {formatMoney(remaining)} ₸</CardTitle></CardHeader>
            <CardContent className="flex flex-col gap-3">
              <div className="flex flex-wrap gap-2">
                <Button disabled={busy} onClick={() => run(() => payOrder(order.id, "card"))}>Оплатил картой</Button>
                <Button disabled={busy} variant="outline"
                  onClick={() => run(async () => { setInfo(await getPaymentInfo()); await payOrder(order.id, "kaspi"); })}>
                  Оплатить Kaspi QR</Button>
                <Button disabled={busy} variant="secondary" onClick={() => run(() => requestDebt(order.id))}>Взять в долг</Button>
              </div>
              {info && (
                <div className="rounded-md border p-3 text-sm">
                  <p>{info.instructions}</p>
                  {info.kaspi_qr && <img src={info.kaspi_qr} alt="Kaspi QR" className="mt-2 size-40" />}
                  {info.account && <p className="mt-1">Счёт: {info.account}</p>}
                </div>
              )}
              {order.debt_requested && <p className="text-sm text-[var(--warning)]">Запрос на долг отправлен, ожидайте одобрения.</p>}
              <p className="text-xs text-[var(--muted-foreground)]">Оплата картой/Kaspi подтверждается сотрудником.</p>
            </CardContent>
          </Card>
        )}

        {step === "truck" && (
          <Card>
            <CardHeader><CardTitle>Отправка КАМАЗа</CardTitle></CardHeader>
            <CardContent className="flex flex-col gap-3">
              {order.truck_number && <p className="text-sm">Текущий номер: <b>{order.truck_number}</b></p>}
              <div className="flex gap-2">
                <Input placeholder="Номер КАМАЗа" value={truck} onChange={(e) => setTruckVal(e.target.value)} />
                <Button disabled={busy || !truck} onClick={() => run(() => setTruck(order.id, truck))}>Сохранить</Button>
              </div>
            </CardContent>
          </Card>
        )}

        {(step === "shipping" || step === "done") && (
          <Card><CardContent className="py-6 text-center text-sm text-[var(--muted-foreground)]">
            {order.truck_number && <p className="mb-1">КАМАЗ: <b>{order.truck_number}</b></p>}
            {step === "done" ? "Заказ отгружен." : "Заказ в обработке на складе."}</CardContent></Card>
        )}
      </div>
    </AppShell>
  );
}
```

- [ ] **Step 3: Typecheck and build**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: no type errors; build succeeds.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/app/portal/orders/
git commit -m "feat(portal-fe): client order detail with contextual pay/debt/truck actions"
```

---

## Task 12: Guard staff truck-number overwrite

**Files:**
- Modify: `backend/orders/serializers.py` (OrderSerializer — add `update`)
- Test: `backend/orders/tests/test_staff_truck_guard.py` (create)

**Context:** The staff `OrderSerializer` allows writing `truck_number` via `PATCH /api/orders/{id}/`. Spec Sec 3 requires staff CANNOT overwrite a truck number that a client set. We route the staff write through the same `set_truck_number` service helper (Task 4), which raises on a forbidden overwrite.

**Interfaces:**
- Consumes: `set_truck_number`, `can_set_truck_number` (Task 4).
- Produces: `OrderSerializer.update` that, when `truck_number` changes, delegates to `set_truck_number` (raising `ValidationError(code="forbidden")` → HTTP 400 if the number belongs to a client).

- [ ] **Step 1: Write the failing test**

Create `backend/orders/tests/test_staff_truck_guard.py`:

```python
import pytest
from clients.models import Client
from orders.models import Order
from orders import services


@pytest.mark.django_db
def test_staff_cannot_overwrite_client_truck(manager, auth_client, make_user):
    cli = make_user(username="cli", client=True)
    c = Client.objects.create(user=cli, first_name="A", last_name="B", phone="1")
    o = Order.objects.create(client=c, status="paid")
    services.set_truck_number(o, "CLIENT777", cli)  # client owns the number
    r = auth_client(manager).patch(f"/api/orders/{o.id}/",
                                   {"truck_number": "STAFF111"}, format="json")
    assert r.status_code == 400
    o.refresh_from_db()
    assert o.truck_number == "CLIENT777"


@pytest.mark.django_db
def test_staff_can_set_unset_truck(manager, auth_client):
    c = Client.objects.create(first_name="A", last_name="B", phone="1")
    o = Order.objects.create(client=c, status="paid")
    r = auth_client(manager).patch(f"/api/orders/{o.id}/",
                                   {"truck_number": "STAFF111"}, format="json")
    assert r.status_code == 200
    o.refresh_from_db()
    assert o.truck_number == "STAFF111"
```

Note: `manager` fixture has `orders.edit` (needed for `partial_update`).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest orders/tests/test_staff_truck_guard.py -v`
Expected: FAIL (staff overwrites the client number; first test gets 200 + wrong value).

- [ ] **Step 3: Implement `update` in OrderSerializer**

In `backend/orders/serializers.py`, add an import and an `update` method to `OrderSerializer`:

```python
from .services import set_truck_number
```

Add this method to `OrderSerializer` (after `create`):

```python
    def update(self, instance, validated_data):
        new_truck = validated_data.pop("truck_number", None)
        user = self.context["request"].user
        if new_truck is not None and new_truck != instance.truck_number:
            set_truck_number(instance, new_truck, user)
            instance.refresh_from_db()
        return super().update(instance, validated_data)
```

`set_truck_number` raises `ValidationError(code="forbidden")` when the number belongs to a client, which DRF renders as HTTP 400 — matching the test.

- [ ] **Step 4: Run tests**

Run: `cd backend && python -m pytest orders/tests/test_staff_truck_guard.py -v`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/orders/serializers.py backend/orders/tests/test_staff_truck_guard.py
git commit -m "feat(orders): guard staff overwrite of client-set truck number"
```

---

## Task 13: Full verification pass

**Files:** none (verification only)

- [ ] **Step 1: Run full backend test suite**

Run: `cd backend && python -m pytest -q`
Expected: all tests pass (including pre-existing shipments/orders tests — verify the `paid_total` confirmed-only change and new statuses didn't break them).

- [ ] **Step 2: Check existing shipment flow still works**

Run: `cd backend && python -m pytest shipments/ -v`
Expected: PASS. If `record_arrival` references `order.status` transitions that now go through `ALLOWED_TRANSITIONS`, confirm `paid → arrived` is allowed (it is, per Task 2). If shipments set status directly (not via `transition`), they are unaffected.

- [ ] **Step 3: Frontend build**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: success.

- [ ] **Step 4: Manual smoke (optional, document result)**

Start backend + frontend, register a client, place order, approve as staff, pay, confirm payment as staff, enter truck number. Confirm staff cannot overwrite the client's truck number.

- [ ] **Step 5: Commit any fixes**

```bash
git add -A
git commit -m "test: full verification pass for client portal"
```

---

## Notes on existing-code interactions

- **shipments/services.py** sets order status directly during arrive/load/ship. This plan does NOT route those through `transition()` to avoid scope creep; they continue to work. A future cleanup could migrate them to `transition()`.
- **`paid_total` change** (confirmed-only) affects `is_fully_paid` and the staff order dashboard. Staff-recorded payments default to `status="confirmed"`, so existing behavior is preserved. Verify in Task 13 Step 1.
- **Staff truck-number write** goes through `OrderSerializer.update` (Task 12), which delegates to `set_truck_number` — so staff cannot overwrite a client-set number. The `arrive` path only reads `order.truck_number` (shipments/services.py:28), it does not write it, so no extra guard is needed there.
