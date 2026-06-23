# Catalog Rework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse Grade+Packaging into a self-contained Product (name + color + weight_kg + price), drive `cv_class` from color+weight, reduce catalog UI to one Товары page, and deduct stock by actual CV counts at shipment.

**Architecture:** Add `name`/`color`/`weight_kg` to Product, data-migrate from grade/packaging/cv_class, drop the FKs + Grade/Packaging models. Update serializers (catalog, warehouse), `record_shipment` to deduct by `counts_by_class` with `deduct_stock(allow_negative=True)`. Rewrite the products page, delete grades/packagings pages, fix the sidebar, and migrate all test fixtures.

**Tech Stack:** Django 5 + DRF, PostgreSQL, pytest; Next.js 15 frontend.

## Global Constraints

- Base branch is `feat/weights` (has `cv_class` CharField, `VideoJob.counts_by_class`, per-class Redis counting). `cv_class` becomes a computed property — same `Red_50` format, so CV worker/webhooks unchanged.
- Product fields: `name` (CharField), `color` (choices Red/Green/Blue), `weight_kg` (DecimalField choices 25/50), `price`, `is_active`. `unique_together=(name,color,weight_kg)`.
- `cv_class` property: `f"{color}_{'50' if weight==50 else '25'}"`.
- Warehouse StockItem serializer keeps keys `grade`/`packaging` (frontend StockItem type unchanged) but sources them from `product.name` and `"{int(weight)} кг"`.
- Shipment deduction: primary by `counts_by_class` from the order's latest done VideoJob; fallback to OrderItem when none. Shortfall → negative stock + eventlog warning, never blocks.
- All ~13 test files creating Grade/Packaging must be updated to the new Product shape.
- Verify backend with `pytest -q`; frontend with `npm run build`; final Docker `migrate --check`.

---

### Task 1: New Product model + computed cv_class (schema add only)

**Files:**
- Modify: `backend/catalog/models.py`
- Create: `backend/catalog/migrations/0004_product_new_fields.py` (via makemigrations)
- Test: `backend/catalog/tests/test_product_model.py` (create)

**Interfaces:**
- Produces: `Product.color` (choices), `Product.weight_kg` (DecimalField), `Product.name`, `Product.cv_class` property, `Product.COLORS`.

This task ADDS the new fields (nullable) alongside the existing grade/packaging FKs so data migration (Task 2) can run. The old `weight_kg` property and `cv_class` CharField are removed in Task 3.

- [ ] **Step 1: Write the failing test**

Create `backend/catalog/tests/test_product_model.py`:

```python
import pytest
from decimal import Decimal
from catalog.models import Product

pytestmark = pytest.mark.django_db


def test_cv_class_computed_from_color_and_weight():
    p = Product(name="Высший", color="Red", weight_kg=Decimal("50"), price=Decimal("25000"))
    assert p.cv_class == "Red_50"
    p2 = Product(name="Высший", color="Blue", weight_kg=Decimal("25"), price=Decimal("13000"))
    assert p2.cv_class == "Blue_25"


def test_label_includes_name_color_weight():
    p = Product(name="Высший сорт", color="Green", weight_kg=Decimal("50"), price=Decimal("1"))
    assert str(p) == "Высший сорт · Зелёный 50 кг"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest catalog/tests/test_product_model.py -v`
Expected: FAIL — Product has no `name`/`color` writable field / cv_class is a CharField not property.

- [ ] **Step 3: Edit `backend/catalog/models.py`**

Add the new fields to `Product` (KEEP grade/packaging/cv_class for now — they're removed in Task 3). Add `from decimal import Decimal` at top. Insert new fields and the property/`__str__` additions:

```python
from decimal import Decimal
from django.db import models


class Grade(models.Model):
    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Packaging(models.Model):
    name = models.CharField(max_length=50, unique=True)
    weight_kg = models.DecimalField(max_digits=10, decimal_places=2)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Product(models.Model):
    COLORS = [("Red", "Красный"), ("Green", "Зелёный"), ("Blue", "Синий")]
    WEIGHTS = [(Decimal("25"), "25 кг"), (Decimal("50"), "50 кг")]

    # Старые поля (удаляются в миграции 0006 после переноса данных).
    grade = models.ForeignKey("Grade", on_delete=models.PROTECT, related_name="products", null=True, blank=True)
    packaging = models.ForeignKey("Packaging", on_delete=models.PROTECT, related_name="products", null=True, blank=True)
    cv_class_old = models.CharField(max_length=20, blank=True, default="", db_column="cv_class")

    # Новые поля (nullable до переноса данных, not-null в Task 3).
    name = models.CharField(max_length=100, null=True, blank=True)
    color = models.CharField(max_length=10, choices=COLORS, null=True, blank=True)
    new_weight_kg = models.DecimalField(max_digits=6, decimal_places=2, choices=WEIGHTS, null=True, blank=True)

    price = models.DecimalField(max_digits=12, decimal_places=2)
    is_active = models.BooleanField(default=True)

    @property
    def weight_kg(self):
        return self.new_weight_kg if self.new_weight_kg is not None else (self.packaging.weight_kg if self.packaging_id else None)

    @property
    def cv_class(self):
        if not self.color or self.weight_kg is None:
            return ""
        w = "50" if Decimal(self.weight_kg) == Decimal("50") else "25"
        return f"{self.color}_{w}"

    def __str__(self):
        if self.name and self.color:
            return f"{self.name} · {dict(self.COLORS)[self.color]} {int(self.weight_kg)} кг"
        if self.grade_id and self.packaging_id:
            return f"{self.grade.name} {self.packaging.name}"
        return f"Товар #{self.pk}"
```

Note: the old `cv_class` CharField is renamed to `cv_class_old` (same db_column) so the new `cv_class` PROPERTY name is free. The old `unique_together` on (grade, packaging) is dropped here (remove the `class Meta`).

- [ ] **Step 4: Make the migration**

Run: `cd backend && python manage.py makemigrations catalog`
Expected: creates `0004_...` adding `name`, `color`, `new_weight_kg`, renaming cv_class→cv_class_old, making grade/packaging nullable, dropping unique_together.

- [ ] **Step 5: Run tests**

Run: `cd backend && pytest catalog/tests/test_product_model.py -v`
Expected: PASS (cv_class property + label work for new-field products).

- [ ] **Step 6: Commit**

```bash
git add backend/catalog/models.py backend/catalog/migrations/ backend/catalog/tests/test_product_model.py
git commit -m "feat: add Product name/color/weight fields + cv_class property"
```

---

### Task 2: Data migration — copy grade/packaging/cv_class into new fields

**Files:**
- Create: `backend/catalog/migrations/0005_migrate_product_data.py` (hand-written data migration)
- Test: `backend/catalog/tests/test_data_migration.py` (create, uses migrator)

**Interfaces:**
- Consumes: Product with both old + new fields (Task 1).
- Produces: every Product has `name`, `color`, `new_weight_kg` populated.

- [ ] **Step 1: Write the data migration**

Create `backend/catalog/migrations/0005_migrate_product_data.py`:

```python
from decimal import Decimal
from django.db import migrations

GRADE_COLOR = {"Красный": "Red", "Зелёный": "Green", "Синий": "Blue",
               "Красная": "Red", "Зелёная": "Green", "Синяя": "Blue"}


def forward(apps, schema_editor):
    Product = apps.get_model("catalog", "Product")
    for p in Product.objects.select_related("grade", "packaging").all():
        # name from grade
        p.name = p.grade.name if p.grade_id else (p.name or "Товар")
        # weight from packaging
        if p.packaging_id:
            p.new_weight_kg = p.packaging.weight_kg
        elif p.new_weight_kg is None:
            p.new_weight_kg = Decimal("50")
        # color from cv_class_old (Red_50 → Red), else from grade name, else Red
        color = ""
        if p.cv_class_old:
            color = p.cv_class_old.split("_")[0]
        if color not in ("Red", "Green", "Blue"):
            color = GRADE_COLOR.get(p.grade.name if p.grade_id else "", "Red")
        p.color = color
        p.save(update_fields=["name", "new_weight_kg", "color"])


def backward(apps, schema_editor):
    pass  # one-way


class Migration(migrations.Migration):
    dependencies = [("catalog", "0004_product_new_fields")]
    operations = [migrations.RunPython(forward, backward)]
```

(Replace `0004_product_new_fields` with the actual Task-1 migration name if different.)

- [ ] **Step 2: Write a test that runs the migration on seeded data**

Create `backend/catalog/tests/test_data_migration.py`:

```python
import pytest
from decimal import Decimal
from catalog.models import Grade, Packaging, Product

pytestmark = pytest.mark.django_db


def test_existing_products_get_new_fields_after_save():
    # Simulate a pre-migration product (grade+packaging+cv_class_old) then
    # re-run the same transform logic the data migration applies.
    g = Grade.objects.create(name="Красный")
    pk = Packaging.objects.create(name="Мешок 50 кг", weight_kg="50.00")
    p = Product.objects.create(grade=g, packaging=pk, price="25000",
                               cv_class_old="Red_50")
    # apply transform inline (mirror of migration forward())
    p.name = p.grade.name
    p.new_weight_kg = p.packaging.weight_kg
    p.color = p.cv_class_old.split("_")[0]
    p.save()
    p.refresh_from_db()
    assert p.name == "Красный"
    assert p.new_weight_kg == Decimal("50.00")
    assert p.color == "Red"
    assert p.cv_class == "Red_50"
```

- [ ] **Step 3: Run the migration + test**

Run: `cd backend && python manage.py migrate catalog && pytest catalog/tests/test_data_migration.py -v`
Expected: migration applies; test passes.

- [ ] **Step 4: Commit**

```bash
git add backend/catalog/migrations/0005_migrate_product_data.py backend/catalog/tests/test_data_migration.py
git commit -m "feat: data-migrate products into name/color/weight"
```

---

### Task 3: Finalize schema — rename weight, not-null, drop FKs + Grade/Packaging

**Files:**
- Modify: `backend/catalog/models.py`
- Create: `backend/catalog/migrations/0006_finalize_product.py` (via makemigrations)

**Interfaces:**
- Produces: clean Product (`name`, `color`, `weight_kg`, `price`, `is_active`, `cv_class` property, `unique_together`). Grade/Packaging models gone.

- [ ] **Step 1: Rewrite `backend/catalog/models.py` to the final shape**

```python
from decimal import Decimal
from django.db import models


class Product(models.Model):
    COLORS = [("Red", "Красный"), ("Green", "Зелёный"), ("Blue", "Синий")]
    WEIGHTS = [(Decimal("25"), "25 кг"), (Decimal("50"), "50 кг")]

    name = models.CharField(max_length=100)
    color = models.CharField(max_length=10, choices=COLORS)
    weight_kg = models.DecimalField(max_digits=6, decimal_places=2, choices=WEIGHTS)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ("name", "color", "weight_kg")

    @property
    def cv_class(self):
        w = "50" if Decimal(self.weight_kg) == Decimal("50") else "25"
        return f"{self.color}_{w}"

    def __str__(self):
        return f"{self.name} · {dict(self.COLORS)[self.color]} {int(self.weight_kg)} кг"
```

- [ ] **Step 2: Make the finalize migration**

The model now has `weight_kg` (the field, replacing `new_weight_kg`) and no grade/packaging/cv_class_old. makemigrations will try to rename/drop — guide it. Run:

`cd backend && python manage.py makemigrations catalog --name finalize_product`

When prompted "Did you rename product.new_weight_kg to weight_kg?" answer **yes**. The migration should: rename `new_weight_kg`→`weight_kg`, set `name`/`color`/`weight_kg` not-null, remove `grade`, `packaging`, `cv_class_old`, add `unique_together`, and `DeleteModel` Grade + Packaging.

If makemigrations doesn't auto-delete Grade/Packaging (no model references left), it will emit a DeleteModel for each — confirm. If it misorders (drop FK before delete model), the FK removal precedes DeleteModel automatically.

- [ ] **Step 3: Apply + full catalog tests**

Run: `cd backend && python manage.py migrate catalog && pytest catalog/tests/test_product_model.py -v`
Expected: migrate OK; model tests pass.

- [ ] **Step 4: Commit**

```bash
git add backend/catalog/models.py backend/catalog/migrations/
git commit -m "feat: finalize Product schema, drop Grade/Packaging"
```

---

### Task 4: Catalog serializers, views, urls, admin

**Files:**
- Modify: `backend/catalog/serializers.py`
- Modify: `backend/catalog/views.py`
- Modify: `backend/catalog/urls.py`
- Modify: `backend/catalog/admin.py`
- Test: `backend/catalog/tests/test_catalog_api.py` (update)

**Interfaces:**
- Produces: `/api/products/` CRUD with fields `id, name, color, color_label, weight_kg, price, is_active, label, cv_class`. No `/grades/` or `/packagings/`.

- [ ] **Step 1: Rewrite `backend/catalog/serializers.py`**

```python
from rest_framework import serializers
from .models import Product


class ProductSerializer(serializers.ModelSerializer):
    label = serializers.CharField(source="__str__", read_only=True)
    color_label = serializers.CharField(source="get_color_display", read_only=True)
    cv_class = serializers.CharField(read_only=True)

    class Meta:
        model = Product
        fields = ["id", "name", "color", "color_label", "weight_kg",
                  "price", "is_active", "label", "cv_class"]
```

- [ ] **Step 2: Rewrite `backend/catalog/views.py`**

```python
from rest_framework import viewsets
from rbac.permissions import PermViewSetMixin
from .models import Product
from .serializers import ProductSerializer

_PERMS = {
    "list": "catalog.view", "retrieve": "catalog.view",
    "create": "catalog.create", "update": "catalog.edit",
    "partial_update": "catalog.edit", "destroy": "catalog.delete",
}


class ProductViewSet(PermViewSetMixin, viewsets.ModelViewSet):
    queryset = Product.objects.all()
    serializer_class = ProductSerializer
    required_perms = _PERMS
```

- [ ] **Step 3: Rewrite `backend/catalog/urls.py`**

```python
from rest_framework.routers import DefaultRouter
from .views import ProductViewSet

router = DefaultRouter()
router.register("products", ProductViewSet)
urlpatterns = router.urls
```

- [ ] **Step 4: Rewrite `backend/catalog/admin.py`**

```python
from django.contrib import admin
from .models import Product


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "color", "weight_kg", "price", "is_active")
    list_filter = ("color", "weight_kg", "is_active")
    list_editable = ("price", "is_active")
    search_fields = ("name",)
```

- [ ] **Step 5: Update `backend/catalog/tests/test_catalog_api.py`**

Open it; replace Grade/Packaging creation and `/grades/`,`/packagings/` endpoint calls. Products are created via `POST /api/products/` with `{name, color, weight_kg, price}`. Replace any `Product.objects.create(grade=..., packaging=...)` with `Product.objects.create(name="Высший", color="Red", weight_kg="50", price="25000")`. Remove tests asserting grades/packagings endpoints exist; assert `/api/products/` returns `name`,`color`,`color_label`,`cv_class`.

- [ ] **Step 6: Run**

Run: `cd backend && pytest catalog/ -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/catalog/
git commit -m "feat: product-only catalog serializer/views/urls/admin"
```

---

### Task 5: Warehouse serializer sources from product fields

**Files:**
- Modify: `backend/warehouse/serializers.py`
- Test: `backend/warehouse/tests/test_warehouse.py` (update fixtures)

**Interfaces:**
- Produces: StockItem serialized with `grade` (=product.name), `packaging` (="{int(weight)} кг"), `weight_kg`, plus `color`/`color_label`. Frontend StockItem keys `grade`/`packaging` preserved.

- [ ] **Step 1: Rewrite `StockItemSerializer`**

```python
class StockItemSerializer(serializers.ModelSerializer):
    product_label = serializers.CharField(source="product.__str__", read_only=True)
    grade = serializers.CharField(source="product.name", read_only=True)
    color = serializers.CharField(source="product.color", read_only=True)
    color_label = serializers.CharField(source="product.get_color_display", read_only=True)
    packaging = serializers.SerializerMethodField()
    weight_kg = serializers.DecimalField(
        source="product.weight_kg", max_digits=10, decimal_places=2, read_only=True)

    class Meta:
        model = StockItem
        fields = ["id", "product", "product_label", "grade", "color",
                  "color_label", "packaging", "weight_kg", "bags"]

    def get_packaging(self, obj):
        return f"{int(obj.product.weight_kg)} кг"
```

(Keep `StockReceiptSerializer`/`StockMovementSerializer` unchanged.)

- [ ] **Step 2: Update warehouse test fixtures**

In `backend/warehouse/tests/test_warehouse.py` (and `test_adjust.py`), replace Grade/Packaging+Product creation with the new Product shape. Use a shared helper:

```python
from catalog.models import Product
def _product(name="Высший", color="Red", weight="50", price="25000"):
    return Product.objects.create(name=name, color=color, weight_kg=weight, price=price)
```

Replace existing `Grade.objects.create(...)`, `Packaging.objects.create(...)`, `Product.objects.create(grade=, packaging=)` with `_product(...)`.

- [ ] **Step 3: Run**

Run: `cd backend && pytest warehouse/ -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/warehouse/
git commit -m "feat: warehouse serializer sources name/color/weight from product"
```

---

### Task 6: `deduct_stock(allow_negative=)` + CV-based shipment deduction

**Files:**
- Modify: `backend/warehouse/services.py`
- Modify: `backend/shipments/services.py` (`record_shipment`)
- Test: `backend/shipments/tests/test_cv_deduction.py` (create)

**Interfaces:**
- Consumes: `Product.cv_class`, `VideoJob.counts_by_class`.
- Produces: `deduct_stock(product, bags, user=None, allow_negative=False)`; `record_shipment` deducts by counts_by_class when present.

- [ ] **Step 1: Write the failing test**

Create `backend/shipments/tests/test_cv_deduction.py`:

```python
import pytest
from decimal import Decimal
from catalog.models import Product
from clients.models import Client
from orders.models import Order, OrderItem, Payment
from warehouse.services import receive_stock, deduct_stock
from warehouse.models import StockItem
from shipments.services import (record_arrival, start_loading, record_count,
                                finish_loading, record_shipment)
from webhooks.models import VideoJob

pytestmark = pytest.mark.django_db


def _setup(boss, bags_in_stock=100):
    red = Product.objects.create(name="Высший", color="Red", weight_kg="50", price="25000")
    receive_stock(red, bags_in_stock, boss)
    c = Client.objects.create(first_name="L", last_name="К", phone="x")
    o = Order.objects.create(client=c, status="paid", truck_number="01A123")
    OrderItem.objects.create(order=o, product=red, quantity=50)
    Payment.objects.create(order=o, amount=o.total_amount)
    return o, red


def test_deduct_allow_negative_goes_below_zero(boss):
    p = Product.objects.create(name="X", color="Blue", weight_kg="25", price="1")
    receive_stock(p, 5, boss)
    deduct_stock(p, 8, boss, allow_negative=True)
    assert StockItem.objects.get(product=p).bags == -3


def test_shipment_deducts_by_cv_counts(boss, operator):
    o, red = _setup(boss, bags_in_stock=100)
    record_arrival(o, Decimal("8000"), operator)
    start_loading(o, operator)
    record_count(o, 40, operator)
    finish_loading(o, operator)
    # a done VideoJob carrying counts_by_class for Red_50
    VideoJob.objects.create(order=o, status="done", bags_counted=40,
                            counts_by_class={"Red_50": 40})
    record_shipment(o, Decimal("10000"), operator)
    assert StockItem.objects.get(product=red).bags == 60  # 100 - 40 by CV


def test_shipment_fallback_to_orderitems_without_video(boss, operator):
    o, red = _setup(boss, bags_in_stock=100)
    record_arrival(o, Decimal("8000"), operator)
    start_loading(o, operator)
    record_count(o, 50, operator)
    finish_loading(o, operator)
    record_shipment(o, Decimal("10000"), operator)  # no VideoJob → OrderItem path
    assert StockItem.objects.get(product=red).bags == 50  # 100 - 50 ordered
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && pytest shipments/tests/test_cv_deduction.py -v`
Expected: FAIL — `deduct_stock` has no `allow_negative`; shipment ignores counts_by_class.

- [ ] **Step 3: Add `allow_negative` to `deduct_stock`**

In `backend/warehouse/services.py`, replace `deduct_stock`:

```python
def deduct_stock(product, bags, user=None, allow_negative=False):
    item = StockItem.objects.select_for_update().filter(product=product).first()
    if item is None:
        if not allow_negative:
            raise ValidationError({
                "detail": f"Недостаточно мешков на складе (есть 0, нужно {bags})",
                "code": "insufficient_stock",
            })
        item = StockItem.objects.create(product=product, bags=0)
    if item.bags < bags and not allow_negative:
        raise ValidationError({
            "detail": f"Недостаточно мешков на складе (есть {item.bags}, нужно {bags})",
            "code": "insufficient_stock",
        })
    if item.bags < bags and allow_negative:
        log_event("stock_negative",
                  f"Списание в минус: {product} — было {item.bags}, списано {bags}",
                  user=user, payload={"product": product.id, "had": item.bags, "deduct": bags})
    item.bags = F("bags") - bags
    item.save()
    item.refresh_from_db()
    _apply(item, -bags, "shipment", user)
    return item
```

Ensure `from eventlog.services import log_event` is imported in `warehouse/services.py` (add if missing).

- [ ] **Step 4: Rewrite `record_shipment` deduction block**

In `backend/shipments/services.py`, replace the OrderItem deduction loop with CV-first logic. Replace:

```python
    for item in order.items.select_related("product").all():
        deduct_stock(item.product, item.quantity, user)
```

with:

```python
    from catalog.models import Product as _Product
    job = (order.video_jobs.filter(status="done")
           .exclude(counts_by_class={}).order_by("-finished_at").first())
    counts = job.counts_by_class if job else None
    if counts:
        for cv_class, n in counts.items():
            if not n:
                continue
            color, _, w = cv_class.partition("_")
            weight = Decimal("50") if w == "50" else Decimal("25")
            prod = (_Product.objects.filter(color=color, weight_kg=weight)
                    .order_by("id").first())
            if prod is None:
                log_event("stock_negative",
                          f"Нет товара для класса {cv_class} ({n} меш.) — пропущено",
                          user=user, order=order)
                continue
            deduct_stock(prod, int(n), user, allow_negative=True)
    else:
        for item in order.items.select_related("product").all():
            deduct_stock(item.product, item.quantity, user)
```

(`Decimal` and `log_event` are already imported in shipments/services.py.)

- [ ] **Step 5: Run**

Run: `cd backend && pytest shipments/tests/test_cv_deduction.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/warehouse/services.py backend/shipments/services.py backend/shipments/tests/test_cv_deduction.py
git commit -m "feat: CV-count stock deduction at shipment + allow_negative"
```

---

### Task 7: Update all remaining test fixtures to new Product

**Files:**
- Modify: test files still creating Grade/Packaging:
  `backend/shipments/tests/test_endpoints.py`, `test_lifecycle.py`, `test_transitions.py`,
  `backend/portal/tests/test_portal.py`, `backend/catalog/tests/test_superuser_access.py`,
  `backend/orders/tests/test_order_truck.py`, `test_payments.py`, `test_orders_api.py`,
  `backend/webhooks/tests/test_video_jobs.py`, `test_webhook.py`

**Interfaces:** none (test maintenance).

- [ ] **Step 1: Find every Grade/Packaging usage**

Run: `cd backend && grep -rln "Grade\|Packaging" --include="*.py" . | grep -i test`
Expected: the list above.

- [ ] **Step 2: Replace the creation pattern in each**

In each file, the pattern
```python
g = Grade.objects.create(name="Премиум")
pk = Packaging.objects.create(name="50 кг", weight_kg="50.00")
prod = Product.objects.create(grade=g, packaging=pk, price="100.00")
```
becomes
```python
prod = Product.objects.create(name="Премиум", color="Red", weight_kg="50", price="100.00")
```
Drop the `Grade`/`Packaging` imports from those files. Where a test created two products needing distinct identities, vary `color` (Red/Blue/Green) or `name` to keep `unique_together` satisfied. Where a test referenced `superuser_access` for grades/packagings endpoints, drop those endpoint assertions (only `/products/` remains).

- [ ] **Step 3: Run the WHOLE suite**

Run: `cd backend && pytest -q`
Expected: all pass. Fix any remaining `grade=`/`packaging=` kwargs or `/grades/`/`/packagings/` calls until green.

- [ ] **Step 4: Commit**

```bash
git add backend/
git commit -m "test: migrate all fixtures to product name/color/weight"
```

---

### Task 8: Frontend — Product type, products page, delete grades/packagings, sidebar

**Files:**
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/app/catalog/products/page.tsx`
- Delete: `frontend/src/app/catalog/grades/`, `frontend/src/app/catalog/packagings/`
- Modify: `frontend/src/components/layout/sidebar.tsx`

**Interfaces:**
- Consumes: `/api/products/` returning `{id,name,color,color_label,weight_kg,price,is_active,label,cv_class}`.

- [ ] **Step 1: Update `Product` type in `lib/types.ts`**

Replace the `Product` interface:

```ts
export interface Product {
  id: number; name: string; color: "Red" | "Green" | "Blue"; color_label: string;
  weight_kg: string; price: string; is_active: boolean; label: string; cv_class: string;
}
```

(`StockItem` keeps `grade`/`packaging` string fields — unchanged; backend fills them.)

- [ ] **Step 2: Rewrite the products page create form + table**

In `frontend/src/app/catalog/products/page.tsx`: the create form now has `name` (text Input), `color` (Select Красный/Зелёный/Синий → value Red/Green/Blue), `weight_kg` (Select 25/50), `price`. Remove the grade/packaging selects and the `ready` gate (no longer depends on grades/packagings existing). Payload: `{ name, color, weight_kg, price }`. Table columns: Название (name), Цвет (color_label), Фасовка (`{weight_kg} кг`), Цена (price, keep inline edit), Статус (is_active, keep toggle). Drop the `useApi` calls for `/grades/` and `/packagings/`. Keep StatCard row + sort from the earlier restyle (sort by `name`/`price`). Keep the create button in the topbar `actions` slot.

Concretely, the create state becomes:

```tsx
  const [name, setName] = useState("");
  const [color, setColor] = useState("Red");
  const [weight, setWeight] = useState("50");
  const [price, setPrice] = useState("");
```
and `add`:
```tsx
      await api.post("/products/", { name, color, weight_kg: weight, price });
      setName(""); setColor("Red"); setWeight("50"); setPrice(""); setOpen(false); reload();
```
Modal body:
```tsx
        <div className="grid gap-2"><Label>Название</Label>
          <Input value={name} autoFocus onChange={(e) => setName(e.target.value)} required /></div>
        <div className="grid gap-2"><Label>Цвет</Label>
          <Select value={color} onChange={(e) => setColor(e.target.value)}>
            <option value="Red">Красный</option><option value="Green">Зелёный</option><option value="Blue">Синий</option>
          </Select></div>
        <div className="grid gap-2"><Label>Фасовка</Label>
          <Select value={weight} onChange={(e) => setWeight(e.target.value)}>
            <option value="50">50 кг</option><option value="25">25 кг</option>
          </Select></div>
        <div className="grid gap-2"><Label>Цена за мешок, ₸</Label>
          <Input type="number" step="0.01" value={price} onChange={(e) => setPrice(e.target.value)} required /></div>
```
Table row:
```tsx
                <TD className="font-medium">{p.name}</TD>
                <TD>{p.color_label}</TD>
                <TD className="tabular-nums">{p.weight_kg} кг</TD>
                {/* price cell: keep the existing inline-edit block as-is */}
                <TD><Badge tone={p.is_active ? "success" : "muted"}>{p.is_active ? "Активен" : "Скрыт"}</Badge></TD>
                <TD>{/* keep toggle button */}</TD>
```
THead: `Название · Цвет · Фасовка · Цена · Статус · (action)` with SortableHeader on Название (`name`) and Цена (`price`). Remove the `!ready` hint paragraph and `disabled={!ready}` (always allow creating).

- [ ] **Step 3: Delete grades/packagings pages**

Run: `rm -rf frontend/src/app/catalog/grades frontend/src/app/catalog/packagings`

- [ ] **Step 4: Fix the sidebar**

In `frontend/src/components/layout/sidebar.tsx`, replace the «Номенклатура» group (the `{ label: "Номенклатура", icon: Package, children: [...] }` entry) with a single leaf in the «Работа» section:

```tsx
      { href: "/catalog/products", label: "Товары", icon: Package, perm: "catalog.view" },
```

(Remove the `children` array referencing /catalog/grades and /catalog/packagings.)

- [ ] **Step 5: Build**

Run: `cd frontend && npm run build`
Expected: build succeeds. Fix any other file importing the deleted Product fields `grade`/`packaging` (search: `.grade`/`.packaging` on a Product — orders NewOrderForm and portal/orders/new use product `.label`, which still exists, so they're fine; verify).

- [ ] **Step 6: Commit**

```bash
git add -A frontend/
git commit -m "feat: product-only catalog UI (name/color/weight), drop grades/packagings"
```

---

### Task 9: Full verification

**Files:** none.

- [ ] **Step 1: Backend suite**

Run: `cd backend && pytest -q`
Expected: all pass.

- [ ] **Step 2: Frontend build**

Run: `cd frontend && npm run build`
Expected: succeeds.

- [ ] **Step 3: Docker build + migrate check**

Run: `docker compose up --build -d`, wait ~6s, then
`docker compose exec -T backend python manage.py migrate --check`
Expected: services up; migrate --check exit 0. Smoke: `curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3000/login` → 200. Then `docker compose down`.

- [ ] **Step 4: Manual visual check**

Open the app: Номенклатура group gone, one «Товары» item; create a product with name/color/weight; warehouse shows name + «50 кг»; (GPU-only) CV shipment deducts by class. Note CV deduction is verified by Task 6 tests since GPU isn't in Docker.

- [ ] **Step 5: No commit (verification only).**

---

## Notes for the implementer

- The migration sequence is the riskiest part: Task 1 adds nullable fields + renames cv_class→cv_class_old, Task 2 fills data, Task 3 renames new_weight_kg→weight_kg + drops old columns + deletes Grade/Packaging. Run `migrate` after each and keep the makemigrations answers as noted.
- `Product.weight_kg` flips from a property (Task 1, reading packaging) to a real field (Task 3). Code reading `product.weight_kg` (orders bag_estimate, warehouse) works in both because the property and the field share the name.
- `cv_class` is a property throughout — serializers expose it read-only; nothing writes it.
- CV deduction keys off `order.video_jobs` (related_name on VideoJob.order). Confirm that related_name is `video_jobs` (it is per the model).
- When matching a Product for a `cv_class`, there may be several products of the same color+weight differing by `name`; the plan takes the first by id. That's acceptable for MVP — log_event records the deduction so it's auditable.
- Don't touch the CV worker or webhooks — `cv_class` string format (`Red_50`) is unchanged.
