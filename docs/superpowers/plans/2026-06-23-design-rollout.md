# Design Rollout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply the existing StatCard / FilterPills / SortableHeader components and the Orders-page pattern to Clients, Warehouse, Catalog (grades/packagings/products), and Employees — adding a stat-card row, search, and sortable columns while preserving all existing logic.

**Architecture:** Each page gets a top StatCard row, optional client-side search state, and `sortKey`/`sortDir` state with a `toggleSort` helper; the rendered list becomes `sorted` (filtered + sorted). Existing toggle/adjust/inline-edit/modal/permission logic stays byte-for-byte. One task per page.

**Tech Stack:** Next.js 15, Tailwind v4, lucide-react. Components already exist: `@/components/ui/stat-card`, `@/components/ui/filter-pills`, `@/components/ui/sortable-header`.

## Global Constraints

- Reuse existing components `StatCard`, `SortableHeader` (and `FilterPills` only where a pill set makes sense — these pages mostly use search, not pills).
- PRESERVE all existing handlers and UI: `toggle`, `adjust`, inline price edit, modals, `canManage`/`canAdjust` permission gates, `ready` checks, `stockTone`.
- Client-side only: no backend changes, no new API calls.
- `formatMoney` takes `number | string`. `SortableHeader` renders a `TH`; `sortKey` is a string.
- Verify each task with `cd frontend && npm run build`. No component unit tests exist.

---

### Task 1: Clients page — stat card, search, sort

**Files:**
- Modify: `frontend/src/app/clients/page.tsx` (the `ClientsPage` export only; leave `ClientForm` untouched)

**Interfaces:**
- Consumes: `StatCard`, `SortableHeader`, `type SortDir`.

- [ ] **Step 1: Add imports**

In the imports block of `frontend/src/app/clients/page.tsx`, add after the existing `Table` import line:

```tsx
import { StatCard } from "@/components/ui/stat-card";
import { SortableHeader, type SortDir } from "@/components/ui/sortable-header";
import { Search } from "lucide-react";
```

- [ ] **Step 2: Replace the `ClientsPage` function**

Replace `export default function ClientsPage() { ... }` with:

```tsx
export default function ClientsPage() {
  const { data: clients, reload } = useApi<Client[]>("/clients/");
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [sortKey, setSortKey] = useState("name");
  const [sortDir, setSortDir] = useState<SortDir>("asc");

  const list = clients ?? [];
  const toggleSort = (k: string) => {
    if (k === sortKey) setSortDir(sortDir === "asc" ? "desc" : "asc");
    else { setSortKey(k); setSortDir("asc"); }
  };
  const filtered = list.filter((c) => {
    if (!q) return true;
    return `${c.name} ${c.phone} ${c.country ?? ""}`.toLowerCase().includes(q.toLowerCase());
  });
  const sorted = [...filtered].sort((a, b) => {
    const av = sortKey === "phone" ? a.phone : a.name;
    const bv = sortKey === "phone" ? b.phone : b.name;
    const cmp = String(av).localeCompare(String(bv), "ru");
    return sortDir === "asc" ? cmp : -cmp;
  });

  return (
    <AppShell title="Клиенты" section="Работа" description="Справочник клиентов: контакты, страна и платёжные реквизиты.">
      <section className="mb-5 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <StatCard label="Всего клиентов" value={String(list.length)} />
      </section>

      <div className="mb-4 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div className="relative max-w-md flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
          <Input className="pl-9" placeholder="Поиск по имени, телефону, стране"
            value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <Button size="sm" onClick={() => setOpen(true)}>
          <Plus className="size-4" /> Добавить клиента
        </Button>
      </div>

      <Card>
        <CardContent className="pt-6">
          <Table>
            <THead>
              <TR>
                <SortableHeader label="Имя" sortKey="name" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
                <SortableHeader label="Телефон" sortKey="phone" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
                <TH>Страна</TH>
              </TR>
            </THead>
            <TBody>
              {sorted.map((c) => (
                <TR key={c.id}>
                  <TD className="font-medium">{c.name}</TD>
                  <TD className="tabular-nums">{c.phone}</TD>
                  <TD>{c.country || "—"}</TD>
                </TR>
              ))}
              {sorted.length === 0 && (
                <TR><TD colSpan={3} className="py-4 text-center text-[var(--muted-foreground)]">
                  Клиентов пока нет.</TD></TR>
              )}
            </TBody>
          </Table>
        </CardContent>
      </Card>

      <Modal open={open} onClose={() => setOpen(false)} title="Новый клиент" className="max-w-xl">
        {open && (
          <ClientForm
            onCancel={() => setOpen(false)}
            onDone={() => { setOpen(false); reload(); }}
          />
        )}
      </Modal>
    </AppShell>
  );
}
```

- [ ] **Step 3: Build**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/app/clients/page.tsx
git commit -m "feat: clients page stat card + search + sort"
```

---

### Task 2: Warehouse page — stat cards + sort (keep existing filters/adjust)

**Files:**
- Modify: `frontend/src/app/warehouse/page.tsx` (replace the text summary with StatCards; add sort; keep search/grade/packaging filters and the adjust modal)

**Interfaces:**
- Consumes: `StatCard`, `SortableHeader`, `type SortDir`.

- [ ] **Step 1: Add imports**

Add after the existing `Table` import in `frontend/src/app/warehouse/page.tsx`:

```tsx
import { StatCard } from "@/components/ui/stat-card";
import { SortableHeader, type SortDir } from "@/components/ui/sortable-header";
```

- [ ] **Step 2: Add sort state + sorted list**

In `WarehousePage`, right after the `filtered` const (the `.filter(...)` block), add:

```tsx
  const [sortKey, setSortKey] = useState("product_label");
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const toggleSort = (k: string) => {
    if (k === sortKey) setSortDir(sortDir === "asc" ? "desc" : "asc");
    else { setSortKey(k); setSortDir("asc"); }
  };
  const sorted = [...filtered].sort((a, b) => {
    let cmp: number;
    if (sortKey === "bags") cmp = a.bags - b.bags;
    else cmp = String(a.product_label).localeCompare(String(b.product_label), "ru");
    return sortDir === "asc" ? cmp : -cmp;
  });
```

(Place this BEFORE the `totalBags`/`totalTons` lines so they still read from `filtered` — totals over the full filtered set, not page-sorted; sorting doesn't change totals.)

- [ ] **Step 3: Replace the text summary header with StatCards**

Find the header block (`<div className="mb-4 flex flex-wrap items-center justify-between gap-3">` containing the three `<span>` totals and the adjust button) and replace it with:

```tsx
      <div className="mb-4 flex items-center justify-between gap-3">
        <div className="grid flex-1 grid-cols-1 gap-3 sm:grid-cols-3">
          <StatCard label="Позиций" value={String(filtered.length)} />
          <StatCard label="Мешков" value={formatMoney(totalBags)} />
          <StatCard label="Вес, т" value={totalTons.toFixed(2)} accent />
        </div>
        {canAdjust && (
          <Button size="sm" onClick={() => { setError(""); setOpen(true); }}>
            <SlidersHorizontal className="size-4" /> Изменить остаток
          </Button>
        )}
      </div>
```

- [ ] **Step 4: Make Товар and Остаток sortable, render `sorted`**

In the table `THead`, replace `<TH>Товар</TH>` and `<TH>Остаток</TH>` with SortableHeaders, and keep the rest:

```tsx
              <TR>
                <TH>#</TH>
                <SortableHeader label="Товар" sortKey="product_label" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
                <TH>Сорт</TH><TH>Фасовка</TH>
                <SortableHeader label="Остаток" sortKey="bags" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
                <TH>Вес</TH><TH>Статус</TH>
              </TR>
```

Then change the rows map from `filtered.map(...)` to `sorted.map(...)` (only the `.map` source changes; the row JSX stays identical).

- [ ] **Step 5: Build**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/app/warehouse/page.tsx
git commit -m "feat: warehouse stat cards + sortable columns"
```

---

### Task 3: Grades page — stat cards + sort

**Files:**
- Modify: `frontend/src/app/catalog/grades/page.tsx`

**Interfaces:**
- Consumes: `StatCard`, `SortableHeader`, `type SortDir`.

- [ ] **Step 1: Add imports**

Add after the `Table` import:

```tsx
import { StatCard } from "@/components/ui/stat-card";
import { SortableHeader, type SortDir } from "@/components/ui/sortable-header";
```

- [ ] **Step 2: Add stat + sort, render sorted**

In `GradesPage`, after the existing state declarations (after `const [busy, setBusy] = useState(false);`), add:

```tsx
  const [sortKey, setSortKey] = useState("name");
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const list = grades ?? [];
  const activeN = list.filter((g) => g.is_active).length;
  const toggleSort = (k: string) => {
    if (k === sortKey) setSortDir(sortDir === "asc" ? "desc" : "asc");
    else { setSortKey(k); setSortDir("asc"); }
  };
  const sorted = [...list].sort((a, b) => {
    const cmp = a.name.localeCompare(b.name, "ru");
    return sortDir === "asc" ? cmp : -cmp;
  });
```

- [ ] **Step 3: Replace the count header with StatCards**

Replace the `<div className="mb-4 flex items-center justify-between">` block (the count `<p>` + add button) with:

```tsx
      <div className="mb-4 flex items-center justify-between gap-3">
        <div className="grid flex-1 grid-cols-1 gap-3 sm:grid-cols-2">
          <StatCard label="Всего сортов" value={String(list.length)} />
          <StatCard label="Активных" value={String(activeN)} accent />
        </div>
        <Button size="sm" onClick={() => { setError(""); setOpen(true); }}>
          <Plus className="size-4" /> Добавить сорт
        </Button>
      </div>
```

- [ ] **Step 4: Sortable header + render sorted**

Replace `<THead><TR><TH>Сорт</TH><TH>Статус</TH><TH></TH></TR></THead>` with:

```tsx
            <THead><TR>
              <SortableHeader label="Сорт" sortKey="name" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
              <TH>Статус</TH><TH></TH>
            </TR></THead>
```

Change `(grades ?? []).map((g) => ...)` to `sorted.map((g) => ...)`, and the empty-state check `(grades ?? []).length === 0` to `sorted.length === 0`.

- [ ] **Step 5: Build**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/app/catalog/grades/page.tsx
git commit -m "feat: grades stat cards + sort"
```

---

### Task 4: Packagings page — stat cards + sort

**Files:**
- Modify: `frontend/src/app/catalog/packagings/page.tsx`

**Interfaces:**
- Consumes: `StatCard`, `SortableHeader`, `type SortDir`.

- [ ] **Step 1: Add imports**

```tsx
import { StatCard } from "@/components/ui/stat-card";
import { SortableHeader, type SortDir } from "@/components/ui/sortable-header";
```

- [ ] **Step 2: Add stat + sort**

After `const [busy, setBusy] = useState(false);` in `PackagingsPage`:

```tsx
  const [sortKey, setSortKey] = useState("name");
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const list = packagings ?? [];
  const activeN = list.filter((p) => p.is_active).length;
  const toggleSort = (k: string) => {
    if (k === sortKey) setSortDir(sortDir === "asc" ? "desc" : "asc");
    else { setSortKey(k); setSortDir("asc"); }
  };
  const sorted = [...list].sort((a, b) => {
    let cmp: number;
    if (sortKey === "weight") cmp = Number(a.weight_kg) - Number(b.weight_kg);
    else cmp = a.name.localeCompare(b.name, "ru");
    return sortDir === "asc" ? cmp : -cmp;
  });
```

- [ ] **Step 3: Replace count header**

Replace the `<div className="mb-4 flex items-center justify-between">` block with:

```tsx
      <div className="mb-4 flex items-center justify-between gap-3">
        <div className="grid flex-1 grid-cols-1 gap-3 sm:grid-cols-2">
          <StatCard label="Всего фасовок" value={String(list.length)} />
          <StatCard label="Активных" value={String(activeN)} accent />
        </div>
        <Button size="sm" onClick={() => { setError(""); setOpen(true); }}>
          <Plus className="size-4" /> Добавить фасовку
        </Button>
      </div>
```

- [ ] **Step 4: Sortable headers + render sorted**

Replace `<THead><TR><TH>Фасовка</TH><TH>Вес</TH><TH>Статус</TH><TH></TH></TR></THead>` with:

```tsx
            <THead><TR>
              <SortableHeader label="Фасовка" sortKey="name" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
              <SortableHeader label="Вес" sortKey="weight" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
              <TH>Статус</TH><TH></TH>
            </TR></THead>
```

Change `(packagings ?? []).map((p) => ...)` to `sorted.map((p) => ...)`, and `(packagings ?? []).length === 0` to `sorted.length === 0`.

- [ ] **Step 5: Build**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/app/catalog/packagings/page.tsx
git commit -m "feat: packagings stat cards + sort"
```

---

### Task 5: Products page — stat cards + sort (keep inline edit + toggle)

**Files:**
- Modify: `frontend/src/app/catalog/products/page.tsx`

**Interfaces:**
- Consumes: `StatCard`, `SortableHeader`, `type SortDir`.

- [ ] **Step 1: Add imports**

```tsx
import { StatCard } from "@/components/ui/stat-card";
import { SortableHeader, type SortDir } from "@/components/ui/sortable-header";
```

- [ ] **Step 2: Add stat + sort**

After `const [editPrice, setEditPrice] = useState("");` in `ProductsPage`:

```tsx
  const [sortKey, setSortKey] = useState("label");
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const list = products ?? [];
  const activeN = list.filter((p) => p.is_active).length;
  const toggleSort = (k: string) => {
    if (k === sortKey) setSortDir(sortDir === "asc" ? "desc" : "asc");
    else { setSortKey(k); setSortDir("asc"); }
  };
  const sorted = [...list].sort((a, b) => {
    let cmp: number;
    if (sortKey === "price") cmp = Number(a.price) - Number(b.price);
    else cmp = a.label.localeCompare(b.label, "ru");
    return sortDir === "asc" ? cmp : -cmp;
  });
```

- [ ] **Step 3: Add StatCard row above the count line**

Find `<div className="mb-4 flex items-center justify-between">` (count `<p>` + create button). Replace it with:

```tsx
      <div className="mb-4 flex items-center justify-between gap-3">
        <div className="grid flex-1 grid-cols-1 gap-3 sm:grid-cols-2">
          <StatCard label="Всего товаров" value={String(list.length)} />
          <StatCard label="Активных" value={String(activeN)} accent />
        </div>
        <Button size="sm" disabled={!ready} onClick={() => { setError(""); setOpen(true); }}>
          <Plus className="size-4" /> Создать товар
        </Button>
      </div>
```

(The `!ready` hint paragraph below stays unchanged.)

- [ ] **Step 4: Sortable headers + render sorted**

Replace `<THead><TR><TH>Товар</TH><TH>Цена</TH><TH>Статус</TH><TH></TH></TR></THead>` with:

```tsx
            <THead><TR>
              <SortableHeader label="Товар" sortKey="label" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
              <SortableHeader label="Цена" sortKey="price" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
              <TH>Статус</TH><TH></TH>
            </TR></THead>
```

Change `(products ?? []).map((p) => ...)` to `sorted.map((p) => ...)`, and `(products ?? []).length === 0` to `sorted.length === 0`. The inline-edit JSX inside the row (editId/editPrice/savePrice) stays identical.

- [ ] **Step 5: Build**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/app/catalog/products/page.tsx
git commit -m "feat: products stat cards + sort"
```

---

### Task 6: Employees page — stat cards + search + sort

**Files:**
- Modify: `frontend/src/app/management/employees/page.tsx`

**Interfaces:**
- Consumes: `StatCard`, `SortableHeader`, `type SortDir`.

- [ ] **Step 1: Add imports**

```tsx
import { StatCard } from "@/components/ui/stat-card";
import { SortableHeader, type SortDir } from "@/components/ui/sortable-header";
import { Search } from "lucide-react";
```

(The file imports `Plus` already; just add the new line(s). `Search` is new.)

- [ ] **Step 2: Add state**

After `const [busy, setBusy] = useState(false);` in `EmployeesPage`:

```tsx
  const [q, setQ] = useState("");
  const [sortKey, setSortKey] = useState("name");
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const list = employees ?? [];
  const activeN = list.filter((e) => e.is_active).length;
  const toggleSort = (k: string) => {
    if (k === sortKey) setSortDir(sortDir === "asc" ? "desc" : "asc");
    else { setSortKey(k); setSortDir("asc"); }
  };
  const filtered = list.filter((e) => {
    if (!q) return true;
    return `${e.name} ${e.username} ${e.position ?? ""}`.toLowerCase().includes(q.toLowerCase());
  });
  const sorted = [...filtered].sort((a, b) => {
    const av = sortKey === "role" ? (a.role_name ?? "") : a.name;
    const bv = sortKey === "role" ? (b.role_name ?? "") : b.name;
    const cmp = String(av).localeCompare(String(bv), "ru");
    return sortDir === "asc" ? cmp : -cmp;
  });
```

- [ ] **Step 3: Replace count header with StatCards + search**

Replace the `<div className="mb-4 flex items-center justify-between">` block (count `<p>` + add button) with:

```tsx
      <section className="mb-5 grid grid-cols-1 gap-3 sm:grid-cols-2">
        <StatCard label="Всего сотрудников" value={String(list.length)} />
        <StatCard label="Активных" value={String(activeN)} accent />
      </section>
      <div className="mb-4 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div className="relative max-w-md flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
          <Input className="pl-9" placeholder="Поиск по имени, логину, должности"
            value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        {canManage && <Button size="sm" onClick={() => { setError(""); setOpen(true); }}>
          <Plus className="size-4" /> Добавить сотрудника</Button>}
      </div>
```

- [ ] **Step 4: Sortable headers + render sorted**

Replace `<THead><TR><TH>Имя</TH><TH>Логин</TH><TH>Должность</TH><TH>Роль</TH><TH>Статус</TH></TR></THead>` with:

```tsx
          <THead><TR>
            <SortableHeader label="Имя" sortKey="name" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
            <TH>Логин</TH><TH>Должность</TH>
            <SortableHeader label="Роль" sortKey="role" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
            <TH>Статус</TH>
          </TR></THead>
```

Change `(employees ?? []).map((e) => ...)` to `sorted.map((e) => ...)`, and `(employees ?? []).length === 0` to `sorted.length === 0`.

- [ ] **Step 5: Build**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/app/management/employees/page.tsx
git commit -m "feat: employees stat cards + search + sort"
```

---

### Task 7: Visual verification in Docker

**Files:** none (verification).

- [ ] **Step 1: Build and run**

Run: `docker compose up --build -d` then wait ~6s.

- [ ] **Step 2: Smoke-check serves**

Run: `curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3000/login`
Expected: 200.

- [ ] **Step 3: Visual check (manual)**

Log in. For each of Clients, Warehouse, Catalog→Grades/Packagings/Products, Employees: confirm a stat-card row on top, sortable column headers (arrows toggle), search filters (Clients/Employees), and that existing actions still work — Warehouse adjust modal, Grades/Packagings/Products toggle buttons, Products inline price edit. Toggle dark theme. No layout breakage.

- [ ] **Step 4: Tear down**

Run: `docker compose down`

- [ ] **Step 5: No commit (verification only).**

---

## Notes for the implementer

- Every page follows the same recipe; only field names and column sets differ. Read each page before editing — the surrounding modal/form code must remain untouched.
- The Warehouse `totalBags`/`totalTons` must still be computed from `filtered` (not `sorted`) — sorting doesn't change totals, but keep them reading `filtered` to avoid confusion.
- `formatMoney` accepts numbers; pass `totalBags` directly (it's a number).
- Don't remove `Link`, `Check`, `X`, `Select`, `Label`, permission imports — the modals/inline editors use them.
- No backend / no test files. `npm run build` per task is the gate; Docker visual is the final gate.
