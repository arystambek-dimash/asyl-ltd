# Архивирование товаров — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Дать товарам такую же корзину/архив, как у заказов, переиспользуя существующий `Product.is_active` вместо нового `deleted_at`; архивный товар скрыт из выбора новых заказов, но остаётся в старых заказах и отчётах.

**Architecture:** `is_active=False` = «в архиве». Логика архива/восстановления централизована в `apps/catalog/services.py` (по образцу `orders/services.py`), проброшена через экшены `archive`/`restore` и `destroy` в `ProductViewSet`. Дефолтный список `/products/` отдаёт только активные (это же чинит латентный баг — скрытые товары сейчас видны в выпадашке заказа); `?archived=1` отдаёт архив. Фронт получает вкладки [Товары][Архив] по паттерну заказов [Заказы][Корзина].

**Tech Stack:** Django REST Framework, pytest, Next.js App Router, TypeScript, Tailwind.

## Global Constraints

- НЕ коммитить и НЕ пушить до явной команды пользователя «пушни» (накопительный режим). Внутри плана `git commit` выполняем локально по шагам, но `git push` — никогда без команды.
- Стейджить явными путями, НЕ `git add -A` (в дереве есть `.claude/`, `ui-walk/`, `.DS_Store`).
- Право на архив/восстановление товара — `catalog.edit`; на `destroy` — `catalog.delete` (как сейчас).
- НЕ добавлять товару `deleted_at`/`deleted_by` — состояние выражает `is_active`.
- НЕ менять аналитику/отчёты и НЕ трогать существующие `OrderItem` — архив товара на них не влияет.
- Терминология на фронте — «Архив» (вкладка «Архив», кнопка «В архив»/«Восстановить», бейдж «В архиве»).
- `log_event(event_type, message, *, user=None, order=None, payload=None)` — `order` необязателен (nullable FK), для товара вызываем без `order`.

---

### Task 1: Сервис архива/восстановления товара

**Files:**
- Create: `backend/apps/catalog/services.py`
- Test: `backend/apps/catalog/tests/test_archive.py`

**Interfaces:**
- Produces: `archive_product(product: Product, user) -> Product` (ставит `is_active=False`, пишет `log_event`, идемпотентный: повторный вызов на уже архивном — `ValidationError` с кодом `already_archived`); `restore_product(product: Product, user) -> Product` (ставит `is_active=True`, `log_event`, на активном — `ValidationError` код `not_archived`).

- [ ] **Step 1: Write the failing test**

Create `backend/apps/catalog/tests/test_archive.py`:

```python
import pytest
from rest_framework.exceptions import ValidationError
from apps.catalog.models import Product
from apps.catalog.services import archive_product, restore_product

pytestmark = pytest.mark.django_db


def _product(name="Премиум"):
    return Product.objects.create(name=name, color="Red", weight_kg="50", price="100.00")


def test_archive_sets_inactive(manager):
    p = _product()
    assert p.is_active is True
    archive_product(p, manager)
    p.refresh_from_db()
    assert p.is_active is False


def test_archive_twice_raises(manager):
    p = _product()
    archive_product(p, manager)
    with pytest.raises(ValidationError):
        archive_product(p, manager)


def test_restore_sets_active(manager):
    p = _product()
    archive_product(p, manager)
    restore_product(p, manager)
    p.refresh_from_db()
    assert p.is_active is True


def test_restore_active_raises(manager):
    p = _product()
    with pytest.raises(ValidationError):
        restore_product(p, manager)
```

Note: fixture `manager` is provided by the shared `conftest.py` (used across `apps/catalog/tests`). If import of `services` fails, that is the expected initial failure.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest apps/catalog/tests/test_archive.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'apps.catalog.services'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/apps/catalog/services.py`:

```python
from django.db import transaction
from rest_framework.exceptions import ValidationError
from apps.eventlog.services import log_event
from .models import Product


@transaction.atomic
def archive_product(product: Product, user) -> Product:
    """Архивирование товара: is_active=False. Товар исчезает из выбора новых
    заказов и прайс-листов, но остаётся в старых заказах и отчётах."""
    if not product.is_active:
        raise ValidationError({"detail": "Товар уже в архиве", "code": "already_archived"})
    product.is_active = False
    product.save(update_fields=["is_active"])
    log_event("catalog", "Товар отправлен в архив", user=user,
              payload={"product_id": product.id})
    return product


@transaction.atomic
def restore_product(product: Product, user) -> Product:
    """Восстановление товара из архива — снова доступен в новых заказах."""
    if product.is_active:
        raise ValidationError({"detail": "Товар не в архиве", "code": "not_archived"})
    product.is_active = True
    product.save(update_fields=["is_active"])
    log_event("catalog", "Товар восстановлен из архива", user=user,
              payload={"product_id": product.id})
    return product
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest apps/catalog/tests/test_archive.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/apps/catalog/services.py backend/apps/catalog/tests/test_archive.py
git commit -m "feat(catalog): сервис архива/восстановления товара"
```

---

### Task 2: ViewSet — фильтр списка, экшены archive/restore, destroy→архив

**Files:**
- Modify: `backend/apps/catalog/views.py:9-20`
- Test: `backend/apps/catalog/tests/test_archive_api.py`

**Interfaces:**
- Consumes: `archive_product`, `restore_product` from Task 1.
- Produces: `GET /api/products/` → только `is_active=True`; `GET /api/products/?archived=1` → только `is_active=False`; `POST /api/products/{id}/archive/` (право `catalog.edit`); `POST /api/products/{id}/restore/` (право `catalog.edit`); `DELETE /api/products/{id}/` → архивирует (204), НЕ hard-delete.

- [ ] **Step 1: Write the failing test**

Create `backend/apps/catalog/tests/test_archive_api.py`:

```python
import pytest
from apps.catalog.models import Product

pytestmark = pytest.mark.django_db


def _product(name="Премиум", active=True):
    return Product.objects.create(
        name=name, color="Red", weight_kg="50", price="100.00", is_active=active)


def test_list_hides_archived(auth_client, manager):
    _product(name="Активный")
    _product(name="Архивный", active=False)
    resp = auth_client(manager).get("/api/products/")
    assert resp.status_code == 200
    names = {row["name"] for row in resp.data}
    assert "Активный" in names
    assert "Архивный" not in names


def test_list_archived_param(auth_client, manager):
    _product(name="Активный")
    _product(name="Архивный", active=False)
    resp = auth_client(manager).get("/api/products/?archived=1")
    assert resp.status_code == 200
    names = {row["name"] for row in resp.data}
    assert names == {"Архивный"}


def test_archive_action(auth_client, manager):
    p = _product()
    resp = auth_client(manager).post(f"/api/products/{p.id}/archive/")
    assert resp.status_code == 200
    p.refresh_from_db()
    assert p.is_active is False


def test_restore_action(auth_client, manager):
    p = _product(active=False)
    resp = auth_client(manager).post(f"/api/products/{p.id}/restore/")
    assert resp.status_code == 200
    p.refresh_from_db()
    assert p.is_active is True


def test_delete_archives_not_hard_delete(auth_client, manager):
    p = _product()
    resp = auth_client(manager).delete(f"/api/products/{p.id}/")
    assert resp.status_code == 204
    p.refresh_from_db()
    assert p.is_active is False


def test_operator_cannot_archive(auth_client, operator):
    p = _product()
    resp = auth_client(operator).post(f"/api/products/{p.id}/archive/")
    assert resp.status_code == 403
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest apps/catalog/tests/test_archive_api.py -v`
Expected: FAIL — `test_list_hides_archived` fails (archived product still listed); `archive`/`restore` return 404 (no such action).

- [ ] **Step 3: Write minimal implementation**

Replace the whole `ProductViewSet` block and imports in `backend/apps/catalog/views.py`. New file content for lines 1-20 (imports + `_PERMS` + viewset):

```python
from rest_framework import viewsets, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.decorators import action
from apps.rbac.permissions import HasPerm
from apps.rbac.permissions import PermViewSetMixin
from .models import Product, ClientPrice
from .serializers import ProductSerializer
from .services import archive_product, restore_product

_PERMS = {
    # Просмотр товаров нужен и менеджеру Отдела 2 для составления заявки.
    "list": ("catalog.view", "dept2.view"), "retrieve": ("catalog.view", "dept2.view"),
    "create": "catalog.create", "update": "catalog.edit",
    "partial_update": "catalog.edit", "destroy": "catalog.delete",
    "archive": "catalog.edit", "restore": "catalog.edit",
}


class ProductViewSet(PermViewSetMixin, viewsets.ModelViewSet):
    serializer_class = ProductSerializer
    required_perms = _PERMS

    def get_queryset(self):
        # По умолчанию — только активные товары: архивные не мешают в списке
        # и не подставляются в новые заказы. ?archived=1 — вкладка «Архив».
        qs = Product.objects.select_related("stock")
        if self.request.query_params.get("archived") in ("1", "true"):
            return qs.filter(is_active=False)
        return qs.filter(is_active=True)

    def destroy(self, request, *args, **kwargs):
        """Удаление = отправка в архив (soft). Товар защищён PROTECT от заказов;
        архив безопаснее hard-delete и сохраняет связи со старыми заказами."""
        archive_product(self.get_object(), request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"], url_path="archive")
    def archive(self, request, pk=None):
        product = archive_product(self.get_object(), request.user)
        return Response(ProductSerializer(product, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="restore")
    def restore(self, request, pk=None):
        product = restore_product(self.get_object(), request.user)
        return Response(ProductSerializer(product, context={"request": request}).data)
```

Note: `get_object()` uses `get_queryset()`, so `restore` on an archived product still resolves — because `?archived=1` isn't required for detail routes; but `get_queryset()` without the param filters to active-only, which would 404 an archived product on `restore`. To avoid that, `restore`/`archive` must look up across both states. Replace `get_object()` inside `archive`/`restore` with an explicit lookup:

```python
    @action(detail=True, methods=["post"], url_path="archive")
    def archive(self, request, pk=None):
        product = archive_product(self._any_product(pk), request.user)
        return Response(ProductSerializer(product, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="restore")
    def restore(self, request, pk=None):
        product = restore_product(self._any_product(pk), request.user)
        return Response(ProductSerializer(product, context={"request": request}).data)

    def _any_product(self, pk):
        from django.shortcuts import get_object_or_404
        obj = get_object_or_404(Product, pk=pk)
        self.check_object_permissions(self.request, obj)
        return obj
```

(Keep `destroy` using `self.get_object()` — deletion always targets an active product, which the default queryset resolves.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest apps/catalog/tests/test_archive_api.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Run full catalog + orders suite (no regressions)**

Run: `cd backend && python -m pytest apps/catalog apps/orders -q`
Expected: PASS. Pay attention to `apps/catalog/tests/test_catalog_api.py::test_staff_can_list_products` — it creates an active product, so it still appears. If any test created inactive products and expected them in `/products/`, fix that test to use `?archived=1` (none currently do — verified in plan research).

- [ ] **Step 6: Commit**

```bash
git add backend/apps/catalog/views.py backend/apps/catalog/tests/test_archive_api.py
git commit -m "feat(catalog): archive/restore экшены, список без архива, destroy→архив"
```

---

### Task 3: Фронт — вкладки [Товары][Архив], кнопки «В архив»/«Восстановить»

**Files:**
- Modify: `frontend/src/app/catalog/products/page.tsx` (whole `ProductsPageInner`)

**Interfaces:**
- Consumes: `GET /products/`, `GET /products/?archived=1`, `POST /products/{id}/archive/`, `POST /products/{id}/restore/` from Task 2.
- Produces: UI only (no downstream code depends on it).

- [ ] **Step 1: Replace the products page**

Rewrite `frontend/src/app/catalog/products/page.tsx`. This mirrors the [Заказы][Корзина] pattern from `orders/page.tsx` (Tabs variant="bar", separate archived list component, ConfirmDialog for archive):

```tsx
"use client";
import { useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Field } from "@/components/ui/field";
import { Select } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Modal } from "@/components/ui/modal";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { StatCard } from "@/components/ui/stat-card";
import { SortableHeader, type SortDir } from "@/components/ui/sortable-header";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Tabs } from "@/components/ui/tabs";
import { ErrorAlert } from "@/components/ui/data-state";
import { useApi } from "@/lib/use-api";
import { useAuth } from "@/store/auth";
import { can } from "@/lib/can";
import { api, apiError } from "@/lib/api";
import { formatMoney } from "@/lib/utils";
import { Plus, Check, X, Pencil, Archive, ArchiveRestore } from "lucide-react";
import type { Product } from "@/lib/types";

function ProductsPageInner() {
  const { data: products, error: loadError, reload } = useApi<Product[]>("/products/");
  const { data: archived, reload: reloadArchived } = useApi<Product[]>("/products/?archived=1");
  const { me } = useAuth();
  const canEdit = can(me, "catalog.edit");

  const [tab, setTab] = useState<"active" | "archive">("active");
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Product | null>(null);
  const [name, setName] = useState("");
  const [color, setColor] = useState("Red");
  const [weight, setWeight] = useState("50");
  const [price, setPrice] = useState("");
  const [askWeight, setAskWeight] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [editId, setEditId] = useState<number | null>(null);
  const [editPrice, setEditPrice] = useState("");
  const [arcItem, setArcItem] = useState<Product | null>(null);
  const [arcError, setArcError] = useState("");
  const [arcBusy, setArcBusy] = useState(false);

  function openNew() {
    setEditing(null); setName(""); setColor("Red"); setWeight("50"); setPrice("");
    setAskWeight(false); setError(""); setOpen(true);
  }
  function openEdit(p: Product) {
    setEditing(p); setName(p.name); setColor(p.color);
    setWeight(String(Number(p.weight_kg))); setPrice(p.price);
    setAskWeight(p.ask_truck_weight ?? false);
    setError(""); setOpen(true);
  }

  async function save(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    try {
      const body = { name, color, weight_kg: weight, price, ask_truck_weight: askWeight };
      if (editing) await api.patch(`/products/${editing.id}/`, body);
      else await api.post("/products/", body);
      setOpen(false); reload();
    } catch (e) { setError(apiError(e)); } finally { setBusy(false); }
  }

  async function confirmArchive() {
    if (!arcItem) return;
    setArcBusy(true); setArcError("");
    try {
      await api.post(`/products/${arcItem.id}/archive/`);
      setArcItem(null); reload(); reloadArchived();
    } catch (e) { setArcError(apiError(e)); } finally { setArcBusy(false); }
  }

  async function restore(p: Product) {
    try { await api.post(`/products/${p.id}/restore/`); reload(); reloadArchived(); }
    catch (e) { setError(apiError(e)); }
  }

  async function savePrice(p: Product) {
    try { await api.patch(`/products/${p.id}/`, { price: editPrice }); setEditId(null); reload(); }
    catch (e) { setError(apiError(e)); }
  }

  const [sortKey, setSortKey] = useState("name");
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const list = products ?? [];
  const archiveList = archived ?? [];
  const toggleSort = (k: string) => {
    if (k === sortKey) setSortDir(sortDir === "asc" ? "desc" : "asc");
    else { setSortKey(k); setSortDir("asc"); }
  };
  const sorted = [...list].sort((a, b) => {
    let cmp: number;
    if (sortKey === "price") cmp = Number(a.price) - Number(b.price);
    else cmp = a.name.localeCompare(b.name, "ru");
    return sortDir === "asc" ? cmp : -cmp;
  });

  return (
    <AppShell title="Товары" section="Работа" description="Товары: сорт, цвет (тип) и фасовка. Управляйте ценами и архивом."
      actions={
        <Button size="sm" onClick={openNew} aria-label="Создать товар">
          <Plus className="size-4" /> <span className="hidden sm:inline">Создать товар</span>
        </Button>
      }>
      <div className="mb-4">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <StatCard label="Активных товаров" value={String(list.length)} accent />
          <StatCard label="В архиве" value={String(archiveList.length)} />
        </div>
      </div>

      <div className="mb-4">
        <Tabs variant="bar" active={tab} onChange={(k) => setTab(k as "active" | "archive")}
          items={[
            { key: "active", label: "Товары", icon: Check },
            { key: "archive", label: "Архив", icon: Archive },
          ]} />
      </div>

      {loadError && !products && <div className="mb-4"><ErrorAlert message={loadError} onRetry={reload} /></div>}

      {tab === "archive" ? (
        <Card>
          <CardContent className="pt-6">
            <Table>
              <THead><TR>
                <TH>Название</TH><TH>Цвет</TH><TH>Фасовка</TH><TH>Цена</TH><TH></TH>
              </TR></THead>
              <TBody>
                {archiveList.map((p) => (
                  <TR key={p.id}>
                    <TD className="font-medium">{p.name}</TD>
                    <TD>{p.color_label}</TD>
                    <TD className="tabular-nums">{Number(p.weight_kg)} кг</TD>
                    <TD className="tabular-nums">{formatMoney(p.price)} ₸</TD>
                    <TD>
                      <div className="flex items-center justify-end gap-1">
                        <Badge tone="muted">В архиве</Badge>
                        {canEdit && (
                          <Button size="sm" variant="outline" onClick={() => restore(p)}>
                            <ArchiveRestore className="size-4" /> Восстановить
                          </Button>
                        )}
                      </div>
                    </TD>
                  </TR>
                ))}
                {archiveList.length === 0 && (
                  <TR><TD colSpan={5} className="py-4 text-center text-[var(--muted-foreground)]">
                    Архив пуст.</TD></TR>
                )}
              </TBody>
            </Table>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardContent className="pt-6">
            <Table>
              <THead><TR>
                <SortableHeader label="Название" sortKey="name" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
                <TH>Цвет</TH>
                <TH>Фасовка</TH>
                <SortableHeader label="Цена" sortKey="price" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
                <TH></TH>
              </TR></THead>
              <TBody>
                {sorted.map((p) => (
                  <TR key={p.id}>
                    <TD className="font-medium">{p.name}</TD>
                    <TD>{p.color_label}</TD>
                    <TD className="tabular-nums">{Number(p.weight_kg)} кг</TD>
                    <TD className="tabular-nums">
                      {editId === p.id ? (
                        <div className="flex items-center gap-2">
                          <Input type="number" step="0.01" className="h-8 w-32"
                            value={editPrice} onChange={(e) => setEditPrice(e.target.value)} />
                          <Button size="sm" onClick={() => savePrice(p)}><Check className="size-4" /></Button>
                          <Button size="sm" variant="ghost" onClick={() => setEditId(null)}><X className="size-4" /></Button>
                        </div>
                      ) : (
                        <button className="hover:underline"
                          onClick={() => { setEditId(p.id); setEditPrice(p.price); }}>
                          {formatMoney(p.price)} ₸
                        </button>
                      )}
                    </TD>
                    <TD>
                      <div className="flex items-center justify-end gap-1">
                        {canEdit && (
                          <Button size="sm" variant="ghost" onClick={() => openEdit(p)} title="Изменить">
                            <Pencil className="size-4" />
                          </Button>
                        )}
                        {canEdit && (
                          <Button size="sm" variant="ghost"
                            className="text-[var(--muted-foreground)] hover:text-[var(--destructive)]"
                            onClick={() => { setArcError(""); setArcItem(p); }} title="В архив">
                            <Archive className="size-4" />
                          </Button>
                        )}
                      </div>
                    </TD>
                  </TR>
                ))}
                {sorted.length === 0 && (
                  <TR><TD colSpan={5} className="py-4 text-center text-[var(--muted-foreground)]">
                    Товаров пока нет.</TD></TR>
                )}
              </TBody>
            </Table>
          </CardContent>
        </Card>
      )}

      <Modal open={open} onClose={() => setOpen(false)}
        eyebrow={editing ? "Номенклатура · Изменение" : "Номенклатура · Товар"}
        title={editing ? "Изменить товар" : "Новый товар"}
        description="Сорт, цвет (тип) и фасовка."
        footer={
          <>
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>Отмена</Button>
            <Button type="submit" form="product-form" disabled={busy}>
              {busy ? "Сохранение…" : editing ? "Сохранить" : "Создать"}</Button>
          </>
        }>
        <form id="product-form" onSubmit={save} className="flex flex-col gap-4">
          <Field label="Название">
            <Input value={name} autoFocus placeholder="напр. Высший сорт"
              onChange={(e) => setName(e.target.value)} required />
          </Field>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label="Цвет (тип)">
              <Select value={color} onChange={(e) => setColor(e.target.value)}>
                <option value="Red">Красный</option>
                <option value="Green">Зелёный</option>
                <option value="Blue">Синий</option>
              </Select>
            </Field>
            <Field label="Фасовка">
              <Select value={weight} onChange={(e) => setWeight(e.target.value)}>
                <option value="50">50 кг</option>
                <option value="25">25 кг</option>
              </Select>
            </Field>
          </div>
          <Field label="Цена за мешок, ₸">
            <Input type="number" step="0.01" value={price}
              onChange={(e) => setPrice(e.target.value)} required />
          </Field>
          <label className="flex cursor-pointer items-start gap-2.5 rounded-lg border p-3">
            <input type="checkbox" className="mt-0.5 size-4 accent-[var(--primary)]"
              checked={askWeight} onChange={(e) => setAskWeight(e.target.checked)} />
            <span className="text-sm">
              <span className="font-medium">Спрашивать вес машины при въезде</span>
              <span className="block text-xs text-[var(--muted-foreground)]">
                Если выключено — вес не спрашивается, берётся расчётный по мешкам.
              </span>
            </span>
          </label>
          {error && (
            <p className="rounded-md border border-[var(--destructive)]/20 bg-[var(--destructive)]/10 px-3 py-2 text-sm text-[var(--destructive)]">
              {error}
            </p>
          )}
        </form>
      </Modal>

      <ConfirmDialog
        open={!!arcItem}
        onClose={() => setArcItem(null)}
        title="Отправить товар в архив?"
        description={arcItem ? `«${arcItem.label}» уйдёт в архив: пропадёт из выбора новых заказов. Старые заказы и отчёты не изменятся. Можно восстановить.` : ""}
        busy={arcBusy}
        error={arcError}
        onConfirm={confirmArchive}
      />
    </AppShell>
  );
}

export default function ProductsPage() {
  return <RequirePerm perm="catalog.view" title="Товары"><ProductsPageInner /></RequirePerm>;
}
```

- [ ] **Step 2: Verify Tabs API matches**

Run: `grep -n "items\|active\|onChange\|variant" frontend/src/components/ui/tabs.tsx | head`
Expected: confirm the `Tabs` component accepts props `variant`, `active`, `onChange`, `items` (array of `{key,label,icon}`). This is the same usage as `orders/page.tsx:116-120`. If the prop names differ, match them to what `orders/page.tsx` actually passes.

- [ ] **Step 3: Verify icons exist**

Run: `grep -rn "ArchiveRestore\|\"Archive\"\|Archive," frontend/src/ | head` and confirm `Archive` and `ArchiveRestore` are valid `lucide-react` exports (they are in current lucide). If `ArchiveRestore` is unavailable in the installed version, use `RotateCcw` instead (already used elsewhere for restore).

Run: `grep -rn "RotateCcw\|ArchiveRestore\|Undo2" frontend/src/app/orders/page.tsx` to see which restore icon the orders trash tab uses, and reuse that exact icon for consistency.

- [ ] **Step 4: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/catalog/products/page.tsx
git commit -m "feat(catalog): вкладки Товары/Архив, в архив/восстановить вместо тумблера"
```

---

### Task 4: Верификация — архивный товар исчезает из выбора заказа

**Files:**
- No code changes expected (verification only). If a form queries `/products/?all=1` or otherwise bypasses the active filter, fix it here.

**Interfaces:**
- Consumes: filtered `/products/` from Task 2.

- [ ] **Step 1: Confirm order forms use plain `/products/`**

Run: `grep -rn "useApi<Product\|/products/" frontend/src/components/order-form.tsx frontend/src/app/city/orders/page.tsx frontend/src/app/warehouse/page.tsx`
Expected: each fetches `/products/` with NO `?archived` / `?all` param. Since Task 2 filters `/products/` to active-only, archived products now automatically disappear from these dropdowns. No edit needed. If any uses a bypass param, remove it so it uses the active list.

- [ ] **Step 2: Backend end-to-end check via pytest**

Add to `backend/apps/catalog/tests/test_archive_api.py`:

```python
def test_archived_product_absent_from_default_list_for_order_form(auth_client, manager):
    _product(name="ДляЗаказа")
    archived = _product(name="Устаревший")
    resp = auth_client(manager).post(f"/api/products/{archived.id}/archive/")
    assert resp.status_code == 200
    listing = auth_client(manager).get("/api/products/")
    names = {row["name"] for row in listing.data}
    assert "ДляЗаказа" in names
    assert "Устаревший" not in names
```

Run: `cd backend && python -m pytest apps/catalog/tests/test_archive_api.py::test_archived_product_absent_from_default_list_for_order_form -v`
Expected: PASS.

- [ ] **Step 3: Browser verification (Playwright)**

Prereq: dev servers up (backend `127.0.0.1:8000`, front `localhost:3000`), users `admin_test`/`manager_test` (пароль `test12345`). Follow the [[verify-ui-in-preview]] / [[local-testing-setup]] approach.

1. Log in as `admin_test`, go to `/catalog/products`.
2. Confirm tabs [Товары][Архив]. Click «В архив» on a product → confirm dialog → товар исчезает из «Товары», появляется в «Архив». Screenshot both.
3. In «Архив» click «Восстановить» → товар возвращается в «Товары». Screenshot.
4. Go to order creation form (`/orders` → создать or `order-form`), open product dropdown → архивный товар отсутствует, активный присутствует. Screenshot.

- [ ] **Step 4: Commit verification test**

```bash
git add backend/apps/catalog/tests/test_archive_api.py
git commit -m "test(catalog): архивный товар отсутствует в списке для формы заказа"
```

---

## Self-Review

**1. Spec coverage:**
- «Товарам такую же корзину/архив» → Tasks 1-3 (сервис + экшены + вкладки/кнопки). ✓
- «Переиспользуем `is_active`, не плодим `deleted_at`» → Task 1 использует `is_active`, миграций нет. ✓
- «Архивный скрыт только из выбора новых заказов» → Task 2 фильтрует список, Task 4 верифицирует; старые `OrderItem` не трогаются. ✓
- «`destroy` → архив вместо hard-delete» → Task 2. ✓
- «Фикс латентного бага (скрытые товары в выпадашке)» → Task 2 фильтр + Task 4 верификация. ✓
- «Терминология Архив везде; убрать тумблер Скрыть/Включить» → Task 3 (тумблер `toggleActive` удалён, бейдж «Активен/Скрыт» убран). ✓
- «Тесты backend» → Tasks 1, 2, 4. ✓
- «Верификация в браузере» → Task 4. ✓

**2. Placeholder scan:** Нет TBD/TODO; весь код приведён целиком. ✓

**3. Type consistency:** `archive_product`/`restore_product` — одинаковые сигнатуры в Task 1 (определены) и Task 2 (импортированы/вызваны). Экшены `archive`/`restore` добавлены в `_PERMS`. Фронт зовёт `POST /products/{id}/archive/` и `/restore/` — совпадает с url_path в Task 2. `Product.is_active` — существующее поле, тип в `types.ts` менять не нужно (spec это отмечает). ✓

**Известная тонкость (зафиксирована в Task 2 Step 3):** детальные экшены `archive`/`restore` не должны падать 404 на архивном товаре из-за фильтрующего `get_queryset()` — используется явный `_any_product(pk)` с `get_object_or_404` + `check_object_permissions`.
