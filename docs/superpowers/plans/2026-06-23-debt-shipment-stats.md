# Debt Tracking + Shipment Stats Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show a post-shipment stats card on the order detail (weights + counted vs weight-derived bags), remove the load-stage comparison, and mark debt in orders list, reports, and clients.

**Architecture:** Add serializer fields (`bag_weight_kg`, `debt_override_by_name` on Order; `debt_total` on Client), fix `bag_estimate_kg` to use counted bags, then frontend: drop WeightCompare on exit, add a shipped-stats card on order detail, debt badges in orders list, a "who authorized" column + total in reports, and a debt column in clients.

**Tech Stack:** Django 5 + DRF, pytest; Next.js 15 frontend.

## Global Constraints

- No new reports app/endpoint — reuse `/orders/` and `/clients/`.
- "Отгружено по весу" computed on frontend = `net_weight_kg ÷ bag_weight_kg` (no DB field).
- Debt = `!is_fully_paid && status != "cancelled"`. `debt_override` distinguishes «В долг».
- Existing 123 backend tests stay green.
- Verify backend with `pytest -q`, frontend with `npm run build`, plus Docker visual.

---

### Task 1: Backend serializer fields + bag_estimate fix

**Files:**
- Modify: `backend/orders/serializers.py`
- Modify: `backend/clients/serializers.py`
- Test: `backend/orders/tests/test_serializer_fields.py` (create)

**Interfaces:**
- Produces: Order serializer exposes `bag_weight_kg`, `debt_override_by_name`; `bag_estimate_kg` from `bags_loaded`. Client serializer exposes `debt_total`.

- [ ] **Step 1: Write the failing test**

Create `backend/orders/tests/test_serializer_fields.py`:

```python
import pytest
from decimal import Decimal
from catalog.models import Product
from clients.models import Client
from orders.models import Order, OrderItem, Payment
from orders.serializers import OrderSerializer
from clients.serializers import ClientSerializer

pytestmark = pytest.mark.django_db


def _order(client, qty=200, paid=None):
    prod = Product.objects.create(name="Премиум", color="Red", weight_kg="50", price="100")
    o = Order.objects.create(client=client, status="draft")
    OrderItem.objects.create(order=o, product=prod, quantity=qty)
    if paid is not None:
        Payment.objects.create(order=o, amount=paid)
    return o, prod


def test_bag_estimate_uses_counted_bags(boss):
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    o, prod = _order(c, qty=200)
    # arrive + count 150 bags via shipment
    from shipments.models import Shipment
    Shipment.objects.create(order=o, truck_number="X", bags_loaded=150)
    data = OrderSerializer(o).data
    # 150 counted × 50 = 7500 (NOT 200 ordered × 50 = 10000)
    assert data["bag_estimate_kg"] == "7500.00"
    assert data["bag_weight_kg"] == "50.00"


def test_debt_override_by_name_present(boss):
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    o, _ = _order(c)
    o.debt_override = True
    o.debt_override_by = boss
    o.save()
    data = OrderSerializer(o).data
    assert data["debt_override_by_name"] == boss.username

    o2, _ = _order(c)
    assert OrderSerializer(o2).data["debt_override_by_name"] is None


def test_client_debt_total(boss):
    c = Client.objects.create(first_name="A", last_name="B", phone="x")
    o, _ = _order(c, qty=10, paid="500")  # total 1000, paid 500 → debt 500
    data = ClientSerializer(c).data
    assert data["debt_total"] == "500.00"

    # fully paid → 0
    c2 = Client.objects.create(first_name="C", last_name="D", phone="y")
    _order(c2, qty=10, paid="1000")
    assert ClientSerializer(c2).data["debt_total"] == "0.00"
```

(Note: `Product(weight_kg="50", price="100")` → bag total 200×100=20000; with qty=10 total=1000. `bag_estimate` uses `weight_kg`=50.)

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && pytest orders/tests/test_serializer_fields.py -v`
Expected: FAIL — fields missing / estimate uses ordered qty.

- [ ] **Step 3: Patch `backend/orders/serializers.py`**

Add `bag_weight_kg` and `debt_override_by_name` fields + methods, and fix
`get_bag_estimate_kg`. In `OrderSerializer`:

Add to the field declarations (after `bag_estimate_kg`):
```python
    bag_weight_kg = serializers.SerializerMethodField()
    debt_override_by_name = serializers.SerializerMethodField()
```

Add to `Meta.fields` (extend the list): add `"bag_weight_kg", "debt_override_by_name"`.

Replace `get_bag_estimate_kg` and add the two new methods:
```python
    def get_bag_estimate_kg(self, obj):
        # Ожидаемый вес по ФАКТУ камеры = посчитанные мешки × вес фасовки.
        from decimal import Decimal
        s = self._shipment(obj)
        bags = s.bags_loaded if s else 0
        per = obj.items.first().product.weight_kg if obj.items.exists() else Decimal("0")
        return str(bags * per)

    def get_bag_weight_kg(self, obj):
        from decimal import Decimal
        per = obj.items.first().product.weight_kg if obj.items.exists() else Decimal("0")
        return str(per)

    def get_debt_override_by_name(self, obj):
        u = obj.debt_override_by
        return u.username if u else None
```

- [ ] **Step 4: Patch `backend/clients/serializers.py`**

```python
from rest_framework import serializers
from decimal import Decimal
from .models import Client


class ClientSerializer(serializers.ModelSerializer):
    name = serializers.CharField(read_only=True)
    debt_total = serializers.SerializerMethodField()

    class Meta:
        model = Client
        fields = ["id", "first_name", "last_name", "phone", "name",
                  "country", "iin", "bank", "bank_account", "user", "debt_total"]

    def get_debt_total(self, obj):
        total = Decimal("0")
        for o in obj.orders.all():
            if o.status == "cancelled" or o.is_fully_paid:
                continue
            total += o.total_amount - o.paid_total
        return str(total)
```

(`obj.orders` is the reverse relation from Order.client — confirm related_name is `orders`; Order.client uses `related_name="orders"`.)

- [ ] **Step 5: Run tests**

Run: `cd backend && pytest orders/tests/test_serializer_fields.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Full suite (catch fallout from estimate change)**

Run: `cd backend && pytest -q`
Expected: all pass. If a shipping test asserted the old `bag_estimate` (ordered×weight) in eventlog, that's in `record_shipment` (separate code path computing its own `bag_estimate` — unaffected by serializer). Confirm green.

- [ ] **Step 7: Commit**

```bash
git add backend/orders/serializers.py backend/clients/serializers.py backend/orders/tests/test_serializer_fields.py
git commit -m "feat: bag_weight_kg, debt_override_by_name, client debt_total; bag_estimate from counted bags"
```

---

### Task 2: Frontend types

**Files:**
- Modify: `frontend/src/lib/types.ts`

**Interfaces:**
- Produces: `Order.bag_weight_kg`, `Order.debt_override_by_name`; `Client.debt_total`.

- [ ] **Step 1: Extend `Order` and `Client` interfaces**

In `frontend/src/lib/types.ts`, add to `Order`:
```ts
  bag_weight_kg?: string; debt_override_by_name?: string | null;
```
(place alongside `bags_loaded`/`bag_estimate_kg`).

Add to `Client`:
```ts
  debt_total?: string;
```

- [ ] **Step 2: Build**

Run: `cd frontend && npm run build`
Expected: succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/types.ts
git commit -m "feat: types for bag_weight_kg, debt fields"
```

---

### Task 3: Shipping — remove exit comparison

**Files:**
- Modify: `frontend/src/app/shipping/page.tsx`

- [ ] **Step 1: Remove the `<WeightCompare .../>` usage on the exit stage**

In `shipping/page.tsx`, in the `order.status === "loaded"` block, delete the
`<WeightCompare order={order} weighOut={weighOut} />` line (the exit weigh input
and «Отгрузить» button stay). Also remove the `<WeightCompare .../>` usage in the
`shipped` summary block if present (per spec, comparison moves to order detail).

- [ ] **Step 2: Remove the now-unused `WeightCompare` function**

Delete the `function WeightCompare({ order, weighOut }) { ... }` definition (no
longer referenced). If `cn` becomes unused after removal, leave other usages
intact — only remove the dead function.

- [ ] **Step 3: Build**

Run: `cd frontend && npm run build`
Expected: succeeds. Fix any unused-import error caused by the deletion.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/app/shipping/page.tsx
git commit -m "feat: drop weight comparison on exit stage"
```

---

### Task 4: Order detail — shipment stats card

**Files:**
- Modify: `frontend/src/app/orders/[id]/page.tsx`

**Interfaces:**
- Consumes: `order.weigh_in_kg`, `weigh_out_kg`, `net_weight_kg`, `bags_loaded`, `bag_weight_kg`, `items`.

- [ ] **Step 1: Add the stats card when shipped**

In `orders/[id]/page.tsx`, add a card inside the right column (after the «Действия»
card), rendered only when `order.status === "shipped"`:

```tsx
{order.status === "shipped" && (() => {
  const net = Number(order.net_weight_kg ?? 0);
  const per = Number(order.bag_weight_kg ?? 0);
  const byWeight = per > 0 ? Math.round(net / per) : null;
  const counted = order.bags_loaded ?? 0;
  const ordered = order.items.reduce((s, it) => s + Number(it.quantity), 0);
  const row = (label: string, value: React.ReactNode) => (
    <div className="flex justify-between text-sm">
      <span className="text-[var(--muted-foreground)]">{label}</span>
      <span className="tabular-nums font-medium">{value}</span>
    </div>
  );
  return (
    <Card>
      <CardHeader><CardTitle>Итог отгрузки</CardTitle></CardHeader>
      <CardContent className="flex flex-col gap-2">
        {row("Вес въезда", order.weigh_in_kg ? `${formatMoney(order.weigh_in_kg)} кг` : "—")}
        {row("Вес выезда", order.weigh_out_kg ? `${formatMoney(order.weigh_out_kg)} кг` : "—")}
        {row("Вес груза", `${formatMoney(String(net))} кг`)}
        <div className="my-1 border-t" />
        {row("Посчитано камерой", `${counted} меш.`)}
        {row("Отгружено по весу", byWeight !== null ? `${byWeight} меш.` : "—")}
        {row("Заказано", `${ordered} меш.`)}
      </CardContent>
    </Card>
  );
})()}
```

(`Card`, `CardHeader`, `CardTitle`, `CardContent`, `formatMoney` are already imported in this file.)

- [ ] **Step 2: Build**

Run: `cd frontend && npm run build`
Expected: succeeds.

- [ ] **Step 3: Commit**

```bash
git add "frontend/src/app/orders/[id]/page.tsx"
git commit -m "feat: shipment stats card on shipped order detail"
```

---

### Task 5: Orders list — debt badges

**Files:**
- Modify: `frontend/src/app/orders/page.tsx`

- [ ] **Step 1: Add a debt badge next to StatusBadge in the row**

In `orders/page.tsx`, the status cell currently is `<TD><StatusBadge status={o.status} dot /></TD>`. Replace with a flex that adds a debt badge when unpaid:

```tsx
                    <TD>
                      <div className="flex items-center gap-1.5">
                        <StatusBadge status={o.status} dot />
                        {!o.is_fully_paid && o.status !== "cancelled" && (
                          <Badge tone={o.debt_override ? "warning" : "destructive"}>
                            {o.debt_override ? "В долг" : "Долг"}
                          </Badge>
                        )}
                      </div>
                    </TD>
```

Add `import { Badge } from "@/components/ui/badge";` if not already imported.

- [ ] **Step 2: Build**

Run: `cd frontend && npm run build`
Expected: succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/app/orders/page.tsx
git commit -m "feat: debt badges in orders list"
```

---

### Task 6: Reports — "who authorized" column + total

**Files:**
- Modify: `frontend/src/app/reports/page.tsx`

- [ ] **Step 1: Add the column + total to the debt table**

In the «Дебиторская задолженность» table, add a «Разрешил долг» header and cell,
and a total row. Update the `<THead>` row to:

```tsx
<THead><TR><TH>Заказ</TH><TH>Клиент</TH><TH>Разрешил долг</TH><TH>Сумма</TH><TH>Оплачено</TH><TH>Остаток</TH></TR></THead>
```

Add the cell in the row map (after the client `<TD>`):
```tsx
              <TD className="text-[var(--muted-foreground)]">{o.debt_override_by_name || "—"}</TD>
```

After the `{debtors.map(...)}`, add a total row inside `<TBody>`:
```tsx
              {debtors.length > 0 && (
                <TR>
                  <TD colSpan={5} className="text-right font-medium">Итого долг</TD>
                  <TD className="tabular-nums font-bold text-[var(--destructive)]">
                    {formatMoney(String(debtors.reduce((s, o) => s + (Number(o.total_amount) - Number(o.paid_total)), 0)))} ₸
                  </TD>
                </TR>
              )}
```

- [ ] **Step 2: Build**

Run: `cd frontend && npm run build`
Expected: succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/app/reports/page.tsx
git commit -m "feat: reports debt — who authorized + total"
```

---

### Task 7: Clients — debt column

**Files:**
- Modify: `frontend/src/app/clients/page.tsx`

- [ ] **Step 1: Add a «Долг» column**

In the clients table `<THead>`, add `<TH>Долг</TH>` before the actions `<TH></TH>`.
In the row, add a cell before the actions cell:

```tsx
                  <TD className="tabular-nums">
                    {Number(c.debt_total ?? 0) > 0
                      ? <span className="font-medium text-[var(--destructive)]">{formatMoney(c.debt_total!)} ₸</span>
                      : <span className="text-[var(--muted-foreground)]">—</span>}
                  </TD>
```

Add `import { formatMoney } from "@/lib/utils";` if not already imported. Bump the
empty-state `colSpan` by 1 (it currently spans the table width).

- [ ] **Step 2: Build**

Run: `cd frontend && npm run build`
Expected: succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/app/clients/page.tsx
git commit -m "feat: client debt column"
```

---

### Task 8: Full verification

**Files:** none.

- [ ] **Step 1: Backend suite**

Run: `cd backend && pytest -q`
Expected: all pass (123 + 3 new = ~126).

- [ ] **Step 2: Frontend build**

Run: `cd frontend && npm run build`
Expected: succeeds.

- [ ] **Step 3: Docker visual**

Run: `docker compose up --build -d`, wait ~6s, `curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3000/login` → 200. Open: order detail (shipped) shows Итог отгрузки; orders list shows debt badges; reports shows «Разрешил долг» + total; clients shows Долг column; shipping exit stage has no comparison block. Toggle dark. Then `docker compose down`.

- [ ] **Step 4: No commit (verification only).**

---

## Notes for the implementer

- `bag_estimate_kg` change only affects the SERIALIZER. `record_shipment` computes its own `bag_estimate` for the eventlog (from ordered items) — leave it; the spec's "expected by camera" is a frontend/serializer concern.
- `ClientSerializer.debt_total` iterates `obj.orders.all()` — fine for the list size here; uses the same `is_fully_paid`/`total_amount`/`paid_total` properties already on Order.
- `formatMoney` accepts `number | string`.
- The order detail card uses an IIFE so the computed values stay local; `React` is in scope (file is a client component).
- No CV changes, no new endpoints, no DB migration.
