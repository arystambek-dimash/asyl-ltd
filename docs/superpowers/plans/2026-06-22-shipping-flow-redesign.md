# Shipping Flow Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the order/shipping flow to add an explicit `loaded` status, make arrival view-only (number+weight from camera/webhook), split loading into "video upload starts loading" + "operator confirms done", and show weight comparison at exit.

**Architecture:** Add status `loaded` to Order. Rework `shipments/services.py` into five guarded transition functions. Update all six `record_loading` call sites (video, webhook counter, manual load) to the new function names. Add a `finish-loading/` endpoint and frontend "Загрузка завершена" button. Exit weight comparison computed in backend, shown on frontend.

**Tech Stack:** Django 5 + DRF + PostgreSQL backend; Next.js 15 + Tailwind frontend; pytest + fakeredis.

## Global Constraints

- CV logic in `cv_service_handoff` / `integrations/video_worker.py` must NOT be modified — only the function it calls (`record_loading` → `record_count`).
- Truck number is the join key across entry/counter/exit webhooks — do not change `_find_order` / `normalize_plate`.
- Webhook must keep working (key-auth `process_webhook`), needed for real cameras.
- Payment is NOT a hard gate for MVP (debt_override path stays as-is for arrival).
- `docker compose up --build` must build and run; migrations apply automatically.
- All Russian UI copy stays Russian. Existing 101 backend tests must stay green after rename adjustments.

---

### Task 1: Add `loaded` status to Order

**Files:**
- Modify: `backend/orders/models.py:7`
- Create: `backend/orders/migrations/0003_order_loaded_status.py` (via makemigrations — there may be no schema change since STATUSES is a plain list; see step 3)
- Test: `backend/orders/tests/test_status.py` (create)

**Interfaces:**
- Produces: `Order.STATUSES` containing `"loaded"` between `"loading"` and `"shipped"`.

- [ ] **Step 1: Write the failing test**

Create `backend/orders/tests/__init__.py` (empty) if missing, then `backend/orders/tests/test_status.py`:

```python
from orders.models import Order


def test_loaded_status_between_loading_and_shipped():
    s = Order.STATUSES
    assert "loaded" in s
    assert s.index("loading") < s.index("loaded") < s.index("shipped")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest orders/tests/test_status.py -v`
Expected: FAIL — `"loaded" in s` is False.

- [ ] **Step 3: Implement**

In `backend/orders/models.py:7` change the list to:

```python
    STATUSES = ["draft", "confirmed", "paid", "arrived", "loading", "loaded", "shipped", "cancelled"]
```

`status` is a plain `CharField` (no `choices=`), so no DB schema change is required. Still run makemigrations to capture any state diff:

Run: `cd backend && python manage.py makemigrations orders`
Expected: "No changes detected" OR a no-op migration. If a migration file is created, keep it.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest orders/tests/test_status.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/orders/models.py backend/orders/tests/ backend/orders/migrations/ 2>/dev/null
git commit -m "feat: add loaded order status"
```

---

### Task 2: Rework shipment services into five guarded transitions

**Files:**
- Modify: `backend/shipments/services.py` (full rewrite of the three functions into five)
- Test: `backend/shipments/tests/test_transitions.py` (create)

**Interfaces:**
- Produces:
  - `record_arrival(order, weigh_in_kg, user, debt_override=False)` — guard `confirmed|paid` → `arrived`. **truck_number param removed** (uses `order.truck_number`).
  - `start_loading(order, user)` — guard `arrived` → `loading`. Returns shipment.
  - `record_count(order, bags, user)` — accepts status `arrived` or `loading`; if `arrived`, advances to `loading` first; writes `bags_loaded`; status ends `loading`. Returns shipment.
  - `finish_loading(order, user)` — guard `loading` → `loaded`. Returns shipment.
  - `record_shipment(order, weigh_out_kg, user)` — guard **`loaded`** → `shipped`; computes net, deducts stock, logs comparison.
- Consumes: `log_event`, `deduct_stock`, `Shipment` (unchanged).

- [ ] **Step 1: Write the failing tests**

Create `backend/shipments/tests/test_transitions.py`:

```python
import pytest
from decimal import Decimal
from catalog.models import Grade, Packaging, Product
from clients.models import Client
from orders.models import Order, OrderItem, Payment
from warehouse.services import receive_stock
from rest_framework.exceptions import ValidationError
from shipments.services import (record_arrival, start_loading, record_count,
                                finish_loading, record_shipment)

pytestmark = pytest.mark.django_db


def _order(boss, status="paid", qty=50):
    g = Grade.objects.create(name="Премиум")
    pk = Packaging.objects.create(name="50 кг", weight_kg="50.00")
    prod = Product.objects.create(grade=g, packaging=pk, price="100.00")
    receive_stock(prod, 100, boss)
    c = Client.objects.create(first_name="L", last_name="К", phone="x")
    o = Order.objects.create(client=c, status=status, truck_number="01A123")
    OrderItem.objects.create(order=o, product=prod, quantity=qty)
    if status == "paid":
        Payment.objects.create(order=o, amount=o.total_amount)
    return o, prod


def test_arrival_uses_order_truck_number(boss, operator):
    o, _ = _order(boss, status="paid")
    record_arrival(o, Decimal("8000"), operator)
    o.refresh_from_db()
    assert o.status == "arrived"
    assert o.shipment.weigh_in_kg == Decimal("8000")
    assert o.shipment.truck_number == "01A123"


def test_start_loading_requires_arrived(boss, operator):
    o, _ = _order(boss, status="paid")
    with pytest.raises(ValidationError):
        start_loading(o, operator)
    record_arrival(o, Decimal("8000"), operator)
    start_loading(o, operator)
    o.refresh_from_db()
    assert o.status == "loading"


def test_record_count_from_arrived_auto_advances(boss, operator):
    o, _ = _order(boss, status="paid")
    record_arrival(o, Decimal("8000"), operator)
    record_count(o, 50, operator)
    o.refresh_from_db()
    assert o.status == "loading"
    assert o.shipment.bags_loaded == 50


def test_record_count_does_not_reach_loaded(boss, operator):
    o, _ = _order(boss, status="paid")
    record_arrival(o, Decimal("8000"), operator)
    start_loading(o, operator)
    record_count(o, 42, operator)
    o.refresh_from_db()
    assert o.status == "loading"
    assert o.shipment.bags_loaded == 42


def test_finish_loading_requires_loading(boss, operator):
    o, _ = _order(boss, status="paid")
    record_arrival(o, Decimal("8000"), operator)
    with pytest.raises(ValidationError):
        finish_loading(o, operator)
    start_loading(o, operator)
    finish_loading(o, operator)
    o.refresh_from_db()
    assert o.status == "loaded"


def test_shipment_requires_loaded_and_computes_net(boss, operator):
    o, prod = _order(boss, status="paid")
    record_arrival(o, Decimal("8000"), operator)
    start_loading(o, operator)
    record_count(o, 50, operator)
    with pytest.raises(ValidationError):  # still loading, not loaded
        record_shipment(o, Decimal("10500"), operator)
    finish_loading(o, operator)
    record_shipment(o, Decimal("10500"), operator)
    o.refresh_from_db()
    assert o.status == "shipped"
    assert o.shipment.net_weight_kg == Decimal("2500")
    from warehouse.models import StockItem
    assert StockItem.objects.get(product=prod).bags == 50
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest shipments/tests/test_transitions.py -v`
Expected: FAIL — `ImportError: cannot import name 'start_loading'`.

- [ ] **Step 3: Implement — rewrite `backend/shipments/services.py`**

Replace the entire file with:

```python
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from eventlog.services import log_event
from warehouse.services import deduct_stock
from .models import Shipment


@transaction.atomic
def record_arrival(order, weigh_in_kg, user, debt_override=False):
    if order.status not in ("confirmed", "paid"):
        raise ValidationError(
            {"detail": "Машину можно принять только для подтверждённого заказа",
             "code": "invalid_status"}
        )
    if not order.is_fully_paid:
        may_override = user is not None and user.has_perm_code("shipping.debt_override")
        if not (debt_override and may_override):
            raise ValidationError(
                {"detail": "Заказ не оплачен — въезд запрещён", "code": "payment_required"}
            )
        order.debt_override = True
        order.debt_override_by = user
        log_event("debt_override",
                  f"Отгрузка в долг разрешена ({user.username})",
                  user=user, order=order)
    truck = order.truck_number
    order.status = "arrived"
    order.save(update_fields=["status", "debt_override", "debt_override_by"])
    shipment, _ = Shipment.objects.get_or_create(
        order=order, defaults={"truck_number": truck}
    )
    shipment.truck_number = truck
    shipment.weigh_in_kg = weigh_in_kg
    shipment.arrived_at = timezone.now()
    shipment.save()
    log_event("arrival", f"Машина {truck} прибыла", user=user, order=order,
              payload={"weigh_in_kg": str(weigh_in_kg)})
    return shipment


@transaction.atomic
def start_loading(order, user):
    if order.status != "arrived":
        raise ValidationError(
            {"detail": "Загрузку можно начать только после прибытия", "code": "invalid_status"}
        )
    order.status = "loading"
    order.save(update_fields=["status"])
    log_event("loading_start", "Начата загрузка", user=user, order=order)
    return order.shipment


@transaction.atomic
def record_count(order, bags, user):
    if order.status == "arrived":
        order.status = "loading"
        order.save(update_fields=["status"])
        log_event("loading_start", "Начата загрузка", user=user, order=order)
    elif order.status != "loading":
        raise ValidationError(
            {"detail": "Подсчёт мешков возможен только во время загрузки",
             "code": "invalid_status"}
        )
    shipment = order.shipment
    shipment.bags_loaded = bags
    shipment.save(update_fields=["bags_loaded"])
    log_event("loading", f"Посчитано {bags} мешков", user=user, order=order,
              payload={"bags": bags})
    return shipment


@transaction.atomic
def finish_loading(order, user):
    if order.status != "loading":
        raise ValidationError(
            {"detail": "Завершить можно только идущую загрузку", "code": "invalid_status"}
        )
    order.status = "loaded"
    order.save(update_fields=["status"])
    log_event("loading_done", "Загрузка завершена", user=user, order=order,
              payload={"bags": order.shipment.bags_loaded})
    return order.shipment


@transaction.atomic
def record_shipment(order, weigh_out_kg, user):
    if order.status != "loaded":
        raise ValidationError(
            {"detail": "Выезд возможен только после завершения загрузки",
             "code": "invalid_status"}
        )
    shipment = order.shipment
    net = abs(Decimal(weigh_out_kg) - shipment.weigh_in_kg)
    for item in order.items.select_related("product").all():
        deduct_stock(item.product, item.quantity, user)
    shipment.weigh_out_kg = weigh_out_kg
    shipment.net_weight_kg = net
    shipment.shipped_at = timezone.now()
    shipment.save()
    order.status = "shipped"
    order.save(update_fields=["status"])
    bag_estimate = sum(
        (i.quantity * i.product.weight_kg for i in order.items.all()), Decimal("0")
    )
    log_event("shipment", f"Выезд, нетто {net} кг", user=user, order=order,
              payload={"net_weight_kg": str(net),
                       "bag_estimate_kg": str(bag_estimate),
                       "discrepancy_kg": str(net - bag_estimate)})
    return shipment
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest shipments/tests/test_transitions.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/shipments/services.py backend/shipments/tests/test_transitions.py
git commit -m "feat: split shipment services into five guarded transitions"
```

---

### Task 3: Update existing lifecycle test to new flow

**Files:**
- Modify: `backend/shipments/tests/test_lifecycle.py`

**Interfaces:**
- Consumes: new service names from Task 2.

The old `test_lifecycle.py` imports `record_loading` and calls `record_arrival(o, "01A123", ...)` with the removed truck param — it will break. Update it.

- [ ] **Step 1: Run it to confirm it now fails**

Run: `cd backend && pytest shipments/tests/test_lifecycle.py -v`
Expected: FAIL — ImportError `record_loading` / TypeError on `record_arrival` signature.

- [ ] **Step 2: Rewrite `backend/shipments/tests/test_lifecycle.py`**

```python
import pytest
from decimal import Decimal
from catalog.models import Grade, Packaging, Product
from clients.models import Client
from orders.models import Order, OrderItem, Payment
from warehouse.services import receive_stock
from rest_framework.exceptions import ValidationError
from shipments.services import (record_arrival, start_loading, record_count,
                                finish_loading, record_shipment)

pytestmark = pytest.mark.django_db


def _paid_order(boss, status="paid", bags_in_stock=100, qty=50):
    g = Grade.objects.create(name="Премиум")
    pk = Packaging.objects.create(name="50 кг", weight_kg="50.00")
    prod = Product.objects.create(grade=g, packaging=pk, price="100.00")
    receive_stock(prod, bags_in_stock, boss)
    c = Client.objects.create(first_name="L", last_name="К", phone="x")
    o = Order.objects.create(client=c, status=status, truck_number="01A123")
    OrderItem.objects.create(order=o, product=prod, quantity=qty)
    if status == "paid":
        Payment.objects.create(order=o, amount=o.total_amount)
    return o, prod


def test_arrive_requires_payment(boss, operator):
    o, _ = _paid_order(boss, status="confirmed")
    with pytest.raises(ValidationError):
        record_arrival(o, Decimal("8000"), operator)


def test_boss_debt_override_allows_arrival(boss):
    o, _ = _paid_order(boss, status="confirmed")
    record_arrival(o, Decimal("8000"), boss, debt_override=True)
    o.refresh_from_db()
    assert o.status == "arrived"
    assert o.debt_override is True


def test_full_flow_deducts_stock_and_computes_net(boss, operator):
    o, prod = _paid_order(boss, status="paid", bags_in_stock=100, qty=50)
    record_arrival(o, Decimal("8000"), operator)
    start_loading(o, operator)
    record_count(o, 50, operator)
    finish_loading(o, operator)
    record_shipment(o, Decimal("10500"), operator)
    o.refresh_from_db()
    assert o.status == "shipped"
    assert o.shipment.net_weight_kg == Decimal("2500")
    from warehouse.models import StockItem
    assert StockItem.objects.get(product=prod).bags == 50


def test_double_ship_rejected(boss, operator):
    o, _ = _paid_order(boss)
    record_arrival(o, Decimal("8000"), operator)
    start_loading(o, operator)
    record_count(o, 50, operator)
    finish_loading(o, operator)
    record_shipment(o, Decimal("10500"), operator)
    with pytest.raises(ValidationError):
        record_shipment(o, Decimal("10500"), operator)
```

- [ ] **Step 3: Run to verify pass**

Run: `cd backend && pytest shipments/tests/test_lifecycle.py -v`
Expected: PASS (4 tests).

- [ ] **Step 4: Commit**

```bash
git add backend/shipments/tests/test_lifecycle.py
git commit -m "test: update lifecycle test to new transition flow"
```

---

### Task 4: Update shipment views + add finish-loading endpoint

**Files:**
- Modify: `backend/shipments/views.py`
- Modify: `backend/shipments/urls.py`
- Test: `backend/shipments/tests/test_endpoints.py` (create)

**Interfaces:**
- Consumes: `record_arrival(order, weigh_in_kg, user, debt_override=)`, `record_count`, `finish_loading`, `record_shipment` from Task 2.
- Produces endpoints:
  - `POST /orders/<pk>/arrive/` body `{weigh_in_kg, debt_override?}` (perm `shipping.arrive`)
  - `POST /orders/<pk>/load/` body `{bags}` → `record_count` (perm `shipping.load`)
  - `POST /orders/<pk>/finish-loading/` no body → `finish_loading` (perm `shipping.load`)
  - `POST /orders/<pk>/ship/` body `{weigh_out_kg}` (perm `shipping.ship`)

- [ ] **Step 1: Write the failing test**

Create `backend/shipments/tests/test_endpoints.py`:

```python
import pytest
from decimal import Decimal
from rest_framework.test import APIClient
from catalog.models import Grade, Packaging, Product
from clients.models import Client
from orders.models import Order, OrderItem, Payment
from warehouse.services import receive_stock
from shipments.services import record_arrival, start_loading, record_count

pytestmark = pytest.mark.django_db


def _order(boss):
    g = Grade.objects.create(name="Премиум")
    pk = Packaging.objects.create(name="50 кг", weight_kg="50.00")
    prod = Product.objects.create(grade=g, packaging=pk, price="100.00")
    receive_stock(prod, 100, boss)
    c = Client.objects.create(first_name="L", last_name="К", phone="x")
    o = Order.objects.create(client=c, status="paid", truck_number="01A123")
    OrderItem.objects.create(order=o, product=prod, quantity=50)
    Payment.objects.create(order=o, amount=o.total_amount)
    return o


def _client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def test_arrive_endpoint_no_truck_param(boss):
    o = _order(boss)
    r = _client(boss).post(f"/api/orders/{o.id}/arrive/", {"weigh_in_kg": "8000"})
    assert r.status_code == 200
    o.refresh_from_db()
    assert o.status == "arrived"


def test_finish_loading_endpoint(boss):
    o = _order(boss)
    record_arrival(o, Decimal("8000"), boss)
    start_loading(o, boss)
    record_count(o, 50, boss)
    r = _client(boss).post(f"/api/orders/{o.id}/finish-loading/")
    assert r.status_code == 200
    o.refresh_from_db()
    assert o.status == "loaded"


def test_finish_loading_wrong_status_400(boss):
    o = _order(boss)
    record_arrival(o, Decimal("8000"), boss)  # arrived, not loading
    r = _client(boss).post(f"/api/orders/{o.id}/finish-loading/")
    assert r.status_code == 400
```

Note: the `boss` fixture has all perms (Начальник role). If `/api/` prefix differs, check `backend/config/urls.py` for the shipments include prefix and adjust the URL in the test.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest shipments/tests/test_endpoints.py -v`
Expected: FAIL — `finish-loading/` 404, and arrive may still pass truck.

- [ ] **Step 3: Rewrite `backend/shipments/views.py`**

```python
from decimal import Decimal
from rest_framework.views import APIView
from rest_framework.response import Response
from rbac.permissions import HasPerm
from orders.models import Order
from .services import record_arrival, record_count, finish_loading, record_shipment
from .serializers import ShipmentSerializer


class _CanArrive(HasPerm):
    def __init__(self): super().__init__("shipping.arrive")


class _CanLoad(HasPerm):
    def __init__(self): super().__init__("shipping.load")


class _CanShip(HasPerm):
    def __init__(self): super().__init__("shipping.ship")


class _Base(APIView):
    def get_order(self, pk):
        return (Order.objects.select_related("shipment")
                .prefetch_related("items__product").get(pk=pk))


class ArriveView(_Base):
    permission_classes = [_CanArrive]

    def post(self, request, pk):
        order = self.get_order(pk)
        s = record_arrival(order, Decimal(str(request.data["weigh_in_kg"])),
                           request.user,
                           debt_override=bool(request.data.get("debt_override", False)))
        return Response(ShipmentSerializer(s).data)


class LoadView(_Base):
    permission_classes = [_CanLoad]

    def post(self, request, pk):
        s = record_count(self.get_order(pk), int(request.data["bags"]), request.user)
        return Response(ShipmentSerializer(s).data)


class FinishLoadingView(_Base):
    permission_classes = [_CanLoad]

    def post(self, request, pk):
        s = finish_loading(self.get_order(pk), request.user)
        return Response(ShipmentSerializer(s).data)


class ShipView(_Base):
    permission_classes = [_CanShip]

    def post(self, request, pk):
        s = record_shipment(self.get_order(pk), Decimal(str(request.data["weigh_out_kg"])), request.user)
        return Response(ShipmentSerializer(s).data)
```

- [ ] **Step 4: Update `backend/shipments/urls.py`**

```python
from django.urls import path
from .views import ArriveView, LoadView, FinishLoadingView, ShipView

urlpatterns = [
    path("orders/<int:pk>/arrive/", ArriveView.as_view()),
    path("orders/<int:pk>/load/", LoadView.as_view()),
    path("orders/<int:pk>/finish-loading/", FinishLoadingView.as_view()),
    path("orders/<int:pk>/ship/", ShipView.as_view()),
]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && pytest shipments/tests/test_endpoints.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/shipments/views.py backend/shipments/urls.py backend/shipments/tests/test_endpoints.py
git commit -m "feat: arrive without truck param, add finish-loading endpoint"
```

---

### Task 5: Update webhook + video call sites to new service names

**Files:**
- Modify: `backend/webhooks/services.py:7,59,62`
- Modify: `backend/webhooks/video_views.py:42,84`
- Modify: `backend/webhooks/views.py:177,216`

**Interfaces:**
- Consumes: `record_arrival(order, weigh_in_kg, user)`, `record_count(order, bags, user)`, `start_loading`, `record_shipment` from Task 2.

This task fixes the four remaining `record_loading` call sites and the changed `record_arrival` signature so the webhook + video flows compile and behave correctly. The video upload must also kick `start_loading`.

- [ ] **Step 1: Run the webhook + video tests to confirm breakage**

Run: `cd backend && pytest webhooks/tests/test_webhook.py webhooks/tests/test_video_jobs.py webhooks/tests/test_counter_api.py -v`
Expected: FAIL — ImportError `record_loading` and/or `record_arrival` arg mismatch.

- [ ] **Step 2: Fix `backend/webhooks/services.py`**

Change the import at line 7:

```python
from shipments.services import record_arrival, record_count, record_shipment
```

Change the `entry` branch (was line 58-59) to drop the truck arg:

```python
                if camera.kind == "entry":
                    record_arrival(order, Decimal(str(weight or 0)), user)
```

Change the `counter` branch (was line 62):

```python
                elif camera.kind == "counter":
                    record_count(order, int(bags or 0), user)
```

(`record_count` auto-advances `arrived → loading`, so the webhook counter event works whether or not video started loading.)

- [ ] **Step 3: Fix `backend/webhooks/video_views.py`**

Change the import at line 42:

```python
from shipments.services import start_loading, record_count
```

In `UploadVideoView.post`, after creating the job (line 32), start loading so the order moves `arrived → loading` when video is uploaded. Replace the job-create + return block:

```python
        job = VideoJob.objects.create(order=order, camera=camera, video=f, status="queued")
        try:
            if order.status == "arrived":
                start_loading(order, request.user)
        except Exception:
            pass
        return Response(VideoJobSerializer(job).data, status=201)
```

In `VideoCompleteView.post`, change the call (was line 84) from `record_loading` to `record_count`:

```python
                record_count(job.order, bags, None)
```

- [ ] **Step 4: Fix `backend/webhooks/views.py`**

At line 177 change the import:

```python
from shipments.services import record_count
```

At line 216 change the call:

```python
                record_count(order, bags, request.user)
```

- [ ] **Step 5: Run the affected tests**

Run: `cd backend && pytest webhooks/ -v`
Expected: PASS. If a webhook test asserted `order.status == "loading"` after a counter event from `arrived`, that still holds (record_count auto-advances). If any test asserted the old `record_arrival` truck-param behavior, update that assertion to read `order.truck_number` (already set on the order).

- [ ] **Step 6: Commit**

```bash
git add backend/webhooks/services.py backend/webhooks/video_views.py backend/webhooks/views.py
git commit -m "feat: point webhook+video flows at record_count/start_loading"
```

---

### Task 6: Full backend suite green

**Files:** none (verification + any straggler test fixes).

- [ ] **Step 1: Run the whole suite**

Run: `cd backend && pytest -q`
Expected: all pass. Previously 101; now ~110 with the new tests.

- [ ] **Step 2: Fix any stragglers**

If a test elsewhere constructs `record_arrival(order, "PLATE", weight, user)` (4-arg) or imports `record_loading`, update it to the new signatures: `record_arrival(order, weight, user)` and `record_count`. Re-run until green. Do not weaken assertions — fix the call, not the check.

- [ ] **Step 3: Commit (if any fixes)**

```bash
git add -A backend/
git commit -m "test: align remaining tests with new shipping flow"
```

---

### Task 7: Frontend — arrival view-only, finish-loading button, exit comparison

**Files:**
- Modify: `frontend/src/app/shipping/page.tsx`

**Interfaces:**
- Consumes endpoints: `POST /orders/{id}/arrive/` `{weigh_in_kg}` (no truck), `POST /orders/{id}/finish-loading/`, `POST /orders/{id}/ship/` `{weigh_out_kg}`.
- Order has new status `"loaded"`.

This task reads the current `page.tsx` and adjusts the per-status UI. Read the file first to get exact current line numbers (they shift as you edit).

- [ ] **Step 1: Read the current page**

Run: open `frontend/src/app/shipping/page.tsx`. Locate: the `Stepper` stage list, the `stepFor(status)` mapping, the arrival input block (status `paid`/`confirmed`), the `arrived` block, the `loading` block with `VideoCounter` + `weigh_out_kg`, and the `VideoCounter` component.

- [ ] **Step 2: Map `loaded` into the stepper**

In the `stepFor`/`statusToStep` mapping, ensure `loaded` maps to the same step index as `loading` is currently shown under "Загрузка" (step 2), and `shipped` maps to "Выезд" (step 3). Concretely, wherever statuses map to step numbers, add:

```ts
    loaded: 2,
```

(matching `loading: 2`). Keep `shipped: 3`.

- [ ] **Step 3: Arrival becomes view-only (drop truck input; arrive sends weight only)**

In the block rendered for `status === "paid" || status === "confirmed"` (the arrival action), remove the truck-number `LicensePlateInput`. The order already carries `truck_number` (set by the manager). Show it read-only, and keep only the weigh-in field + an "Принять (въезд)" button. The arrive call becomes:

```ts
await api(`/orders/${order.id}/arrive/`, {
  method: "POST",
  body: JSON.stringify({
    weigh_in_kg: weighIn,
    ...(isBoss ? { debt_override: true } : {}),
  }),
});
```

Show `order.truck_number` as a static line above the weight field, e.g.:

```tsx
<div className="text-sm text-gray-500">Номер: <b>{order.truck_number || "—"}</b></div>
```

- [ ] **Step 4: `arrived` block — start loading via video upload only**

For `status === "arrived"`: show the truck number, weigh-in, payment badge (all read-only), and render the `VideoCounter` upload control (uploading the video calls `start_loading` server-side, moving to `loading`). No manual buttons here besides the video upload. Keep the existing manual `load` (bags) fallback as a small secondary control if it already exists, but it is optional.

- [ ] **Step 5: `loading` block — live counter + "Загрузка завершена"**

For `status === "loading"`: keep `VideoCounter` (live bag count). When the video job status is `done` (or `bags_loaded > 0`), show a confirm button:

```tsx
<button
  onClick={async () => {
    await api(`/orders/${order.id}/finish-loading/`, { method: "POST" });
    await reload();
  }}
  className="mt-3 w-full rounded-lg bg-green-600 py-2 text-white font-medium"
>
  Загрузка завершена
</button>
```

(`reload` / `mutate` is whatever the page already uses to refresh the order list — reuse it.)

- [ ] **Step 6: `loaded` block — exit weight + comparison preview**

For `status === "loaded"`: render the "ВЫЕЗД" section — a `weigh_out_kg` number input, a live comparison preview, and the "Отгрузить (выезд)" button calling `/ship/`. Comparison preview (computed client-side from already-loaded data; `bagWeight` = the order item packaging weight, `bags` = `shipment.bags_loaded`):

```tsx
{weighOut && order.shipment?.weigh_in_kg && (() => {
  const cargo = Math.abs(Number(weighOut) - Number(order.shipment.weigh_in_kg));
  const estimate = Number(order.shipment.bags_loaded || 0) * bagWeightKg;
  const diff = cargo - estimate;
  const big = Math.abs(diff) > estimate * 0.05; // 5% порог
  return (
    <div className={`mt-2 text-sm ${big ? "text-red-600" : "text-gray-600"}`}>
      Вес груза: <b>{cargo} кг</b> · Ожидалось: <b>{estimate} кг</b> ·
      Расхождение: <b>{diff > 0 ? "+" : ""}{diff} кг</b>
      {big && " — большое расхождение"}
    </div>
  );
})()}
```

`bagWeightKg` derivation: read from the first order item's packaging weight if available in the order payload (`order.items[0]?.weight_kg` per the `Product.weight_kg` field). If not present in the list payload, default the multiplier to `0` so the preview shows estimate `0` rather than crashing — the backend still computes the authoritative comparison in the eventlog.

The "Отгрузить" button stays enabled regardless of `big` (warning, not a block).

- [ ] **Step 7: `shipped` block — final summary**

For `status === "shipped"`: show net weight and the comparison as a static summary (cargo / estimate / discrepancy), plus the "Отгружено" badge. Reuse the same computation as step 6 but read-only from `order.shipment.net_weight_kg`.

- [ ] **Step 8: Build to verify**

Run: `cd frontend && npm run build`
Expected: build succeeds, no type errors. Fix any TS errors (e.g. `order.shipment` possibly undefined — guard with `?.`).

- [ ] **Step 9: Commit**

```bash
git add frontend/src/app/shipping/page.tsx
git commit -m "feat: view-only arrival, finish-loading button, exit weight comparison"
```

---

### Task 8: Docs + Docker verification

**Files:**
- Modify: `integrations/README.md` (worker run command — note new flow, unchanged worker API except complete now records count)

**Interfaces:** none.

- [ ] **Step 1: Update `integrations/README.md`**

Add/confirm a "Запуск воркера на GPU-машине" section with the exact command (env vars: `ASYL_BASE_URL`, `ASYL_CAMERA_ID`, `ASYL_CAMERA_KEY`, `CV_DIR`, `CV_DEVICE`), and a note: "После загрузки видео в CRM заказ переходит в «Загрузка»; воркер считает мешки; оператор нажимает «Загрузка завершена», затем вводит вес выезда."

- [ ] **Step 2: Docker build + migrate check**

Run: `docker compose up --build -d`
Then: `docker compose exec -T backend python manage.py migrate --check`
Expected: services start; `migrate --check` exits 0 (no unapplied migrations). Then `docker compose down`.

- [ ] **Step 3: Commit**

```bash
git add integrations/README.md
git commit -m "docs: worker run + new shipping flow notes"
```

---

## Notes for the implementer

- The `boss` and `operator` pytest fixtures live in `backend/conftest.py` (or app-level conftest). `boss` = Начальник (all shipping perms incl debt_override), `operator` = Оператор. Reuse them.
- `record_count` is intentionally permissive (`arrived` OR `loading`) so all three entry points — video upload, webhook counter camera, manual `load/` — converge without ordering bugs.
- Do not touch `integrations/video_worker.py` or `cv_service_handoff` — CV is frozen.
- The webhook `exit` event (`record_shipment`) now requires status `loaded`. A real exit camera fires after `finish_loading`; for the webhook test, advance the order to `loaded` before the exit event (see Task 5 step 5).
