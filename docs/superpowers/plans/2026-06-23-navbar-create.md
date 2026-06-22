# Navbar (Topbar) Create Buttons Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move each page's create/action button from the page body into the topbar (contextual, qoima-style) via an `actions` slot threaded through AppShell → Topbar.

**Architecture:** Add an `actions?: React.ReactNode` prop to `Topbar` (rendered left of ThemeToggle) and to `AppShell` (passed through). Each page moves its existing create button JSX into `<AppShell actions={...}>`; the button's `onClick`/modal state/permission gate stay on the page unchanged.

**Tech Stack:** Next.js 15, React, Tailwind v4, lucide-react.

## Global Constraints

- The create button's logic (local `open` state, modal, `can(...)` gate, `disabled`) does NOT change — only the button element relocates to the topbar.
- `Topbar` renders `actions` in the right group, before `<ThemeToggle />`, with `gap-3` spacing already on the container.
- On narrow screens the button label may hide via `hidden sm:inline` (wrap label in a span) to avoid breaking the topbar — apply where labels are long.
- If removing a button leaves an empty body container (a flex row that only held the button), remove that container; if the row also holds search/filters/count, keep it and just drop the button.
- No backend changes. Verify each task with `cd frontend && npm run build`. No component unit tests exist.

---

### Task 1: Thread `actions` through AppShell + Topbar

**Files:**
- Modify: `frontend/src/components/layout/topbar.tsx`
- Modify: `frontend/src/components/layout/app-shell.tsx`

**Interfaces:**
- Produces: `Topbar` accepts `actions?: React.ReactNode`; `AppShell` accepts `actions?: React.ReactNode` and forwards it.

- [ ] **Step 1: Add `actions` to Topbar signature + render it**

In `frontend/src/components/layout/topbar.tsx`, change the `Topbar` signature and the right-group block:

```tsx
export function Topbar({ me, title, section, actions }: { me: Me; title: string; section?: string; actions?: React.ReactNode }) {
```

Then in the right group, add `actions` before `<ThemeToggle />`:

```tsx
      <div className="flex items-center gap-3">
        {actions}
        <ThemeToggle />
```

(Leave the rest — Bell, user, logout — unchanged.)

- [ ] **Step 2: Add `actions` to AppShell + forward to Topbar**

In `frontend/src/components/layout/app-shell.tsx`, add `actions` to the props and pass it through:

```tsx
export function AppShell({
  title,
  section,
  description,
  children,
  portal = false,
  actions,
}: {
  title: string;
  section?: string;
  description?: string;
  children: React.ReactNode;
  portal?: boolean;
  actions?: React.ReactNode;
}) {
```

And the Topbar render:

```tsx
        <Topbar me={me} title={title} section={section} actions={actions} />
```

- [ ] **Step 3: Build**

Run: `cd frontend && npm run build`
Expected: build succeeds (no page passes `actions` yet — backward compatible).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/layout/topbar.tsx frontend/src/components/layout/app-shell.tsx
git commit -m "feat: AppShell/Topbar actions slot for topbar buttons"
```

---

### Task 2: Orders — button to topbar

**Files:**
- Modify: `frontend/src/app/orders/page.tsx`

**Interfaces:**
- Consumes: AppShell `actions` prop.

The Orders page (after the earlier restyle) has the create button in a flex row alongside FilterPills. Move just the button into `actions`; keep the search + FilterPills row.

- [ ] **Step 1: Add `actions` to the AppShell open tag**

Change `<AppShell title="Заказы" section="Работа" description="...">` to include `actions`:

```tsx
    <AppShell title="Заказы" section="Работа" description="Заказы клиентов: позиции, оплаты, машина и плановая дата прибытия на отгрузку."
      actions={canCreate ? (
        <Button size="sm" onClick={() => setOpen(true)}>
          <Plus className="size-4" /> <span className="hidden sm:inline">Новый заказ</span>
        </Button>
      ) : undefined}>
```

- [ ] **Step 2: Remove the button from the body row**

In the filter row, remove the `{canCreate && (<Button ...>Новый заказ</Button>)}` block, leaving the `<FilterPills .../>` in its container. The row becomes just search + pills.

- [ ] **Step 3: Build**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/app/orders/page.tsx
git commit -m "feat: orders create button in topbar"
```

---

### Task 3: Clients — button to topbar

**Files:**
- Modify: `frontend/src/app/clients/page.tsx`

- [ ] **Step 1: Add `actions` to AppShell**

```tsx
    <AppShell title="Клиенты" section="Работа" description="Справочник клиентов: контакты, страна и платёжные реквизиты."
      actions={
        <Button size="sm" onClick={() => setOpen(true)}>
          <Plus className="size-4" /> <span className="hidden sm:inline">Добавить клиента</span>
        </Button>
      }>
```

- [ ] **Step 2: Remove the button from the search row**

In the `<div className="mb-4 flex ...">` that holds the search input and the "Добавить клиента" button, remove the `<Button>` so only the search box remains. Keep the search div.

- [ ] **Step 3: Build**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/app/clients/page.tsx
git commit -m "feat: clients create button in topbar"
```

---

### Task 4: Catalog grades/packagings/products — buttons to topbar

**Files:**
- Modify: `frontend/src/app/catalog/grades/page.tsx`
- Modify: `frontend/src/app/catalog/packagings/page.tsx`
- Modify: `frontend/src/app/catalog/products/page.tsx`

These three (after restyle) hold the button in a flex row beside the StatCard grid. Move the button to `actions`; the StatCard grid stays.

- [ ] **Step 1: Grades — AppShell actions + drop body button**

In `grades/page.tsx`, set:

```tsx
    <AppShell title="Сорта" section="Номенклатура" description="Справочник сортов муки."
      actions={
        <Button size="sm" onClick={() => { setError(""); setOpen(true); }}>
          <Plus className="size-4" /> <span className="hidden sm:inline">Добавить сорт</span>
        </Button>
      }>
```

Then change the header row that held StatCards + button so it only holds the StatCard grid:

```tsx
      <div className="mb-4">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <StatCard label="Всего сортов" value={String(list.length)} />
          <StatCard label="Активных" value={String(activeN)} accent />
        </div>
      </div>
```

- [ ] **Step 2: Packagings — same pattern**

In `packagings/page.tsx`:

```tsx
    <AppShell title="Фасовки" section="Номенклатура" description="Справочник фасовок с весом мешка."
      actions={
        <Button size="sm" onClick={() => { setError(""); setOpen(true); }}>
          <Plus className="size-4" /> <span className="hidden sm:inline">Добавить фасовку</span>
        </Button>
      }>
```
```tsx
      <div className="mb-4">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <StatCard label="Всего фасовок" value={String(list.length)} />
          <StatCard label="Активных" value={String(activeN)} accent />
        </div>
      </div>
```

- [ ] **Step 3: Products — same pattern (keep `disabled={!ready}` + the !ready hint)**

In `products/page.tsx`:

```tsx
    <AppShell title="Товары" section="Номенклатура" description="Товары = сорт × фасовка + цена. Управляйте ценами и активностью."
      actions={
        <Button size="sm" disabled={!ready} onClick={() => { setError(""); setOpen(true); }}>
          <Plus className="size-4" /> <span className="hidden sm:inline">Создать товар</span>
        </Button>
      }>
```
```tsx
      <div className="mb-4">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <StatCard label="Всего товаров" value={String(list.length)} />
          <StatCard label="Активных" value={String(activeN)} accent />
        </div>
      </div>
```

(The `{!ready && (<p>...</p>)}` hint paragraph below stays unchanged.)

- [ ] **Step 4: Build**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/catalog/grades/page.tsx frontend/src/app/catalog/packagings/page.tsx frontend/src/app/catalog/products/page.tsx
git commit -m "feat: catalog create buttons in topbar"
```

---

### Task 5: Employees — button to topbar

**Files:**
- Modify: `frontend/src/app/management/employees/page.tsx`

- [ ] **Step 1: AppShell actions**

```tsx
    <AppShell title="Сотрудники" section="Управление" description="Учётные записи сотрудников и их роли. Создавайте аккаунты и назначайте доступ."
      actions={canManage ? (
        <Button size="sm" onClick={() => { setError(""); setOpen(true); }}>
          <Plus className="size-4" /> <span className="hidden sm:inline">Добавить сотрудника</span>
        </Button>
      ) : undefined}>
```

- [ ] **Step 2: Remove the button from the search row**

In the `<div className="mb-4 flex ...">` holding the search input and the `{canManage && <Button>...}`, remove the button so only the search box remains.

- [ ] **Step 3: Build**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/app/management/employees/page.tsx
git commit -m "feat: employees create button in topbar"
```

---

### Task 6: Roles, Cameras, Warehouse, Portal orders — buttons to topbar

**Files:**
- Modify: `frontend/src/app/management/roles/page.tsx`
- Modify: `frontend/src/app/management/cameras/page.tsx`
- Modify: `frontend/src/app/warehouse/page.tsx`
- Modify: `frontend/src/app/portal/orders/page.tsx`

These pages were not restyled, so read each to find the exact button + its handler before moving it. Move the button JSX into `actions`, preserving its onClick/state/gate, and remove it from the body (delete the now-empty header row if it only held the button + a count `<p>`; keep the count `<p>` if present by leaving it, OR drop the whole `mb-4 flex justify-between` wrapper and keep the count as a standalone `<p className="mb-4 ...">`).

- [ ] **Step 1: Roles**

Open `roles/page.tsx`. Find the "Новая роль" button and its handler (e.g. `openNew` / `setOpen`). Set `actions` on its AppShell:

```tsx
      actions={
        <Button size="sm" onClick={/* the same handler the body button used, e.g. openNew or () => setOpen(true) */ undefined}>
          <Plus className="size-4" /> <span className="hidden sm:inline">Новая роль</span>
        </Button>
      }
```

Replace the placeholder `undefined` with the EXACT onClick from the existing body button (copy it verbatim). Then remove the body button. Ensure `Button` and `Plus` are imported (they are, since the body used them).

- [ ] **Step 2: Cameras**

Open `cameras/page.tsx`. Find the "Добавить камеру" button (gated by `cameras.manage` — there is likely a `canManage`/`can(me, "cameras.manage")`). Move it to `actions`, copying its exact onClick and gate:

```tsx
      actions={canManage ? (
        <Button size="sm" onClick={/* exact body handler */ undefined}>
          <Plus className="size-4" /> <span className="hidden sm:inline">Добавить камеру</span>
        </Button>
      ) : undefined}
```

Use the page's actual permission variable name (read the file; it may be `can(me, "cameras.manage")` inline — replicate exactly). Remove the body button.

- [ ] **Step 3: Warehouse — move "Изменить остаток"**

Open `warehouse/page.tsx`. The adjust button currently sits in the header row beside the StatCard grid (after the earlier restyle):

```tsx
        {canAdjust && (
          <Button size="sm" onClick={() => { setError(""); setOpen(true); }}>
            <SlidersHorizontal className="size-4" /> Изменить остаток
          </Button>
        )}
```

Move it to `actions`:

```tsx
      actions={canAdjust ? (
        <Button size="sm" onClick={() => { setError(""); setOpen(true); }}>
          <SlidersHorizontal className="size-4" /> <span className="hidden sm:inline">Изменить остаток</span>
        </Button>
      ) : undefined}
```

Then change the header row to only hold the StatCard grid:

```tsx
      <div className="mb-4">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <StatCard label="Позиций" value={String(filtered.length)} />
          <StatCard label="Мешков" value={formatMoney(totalBags)} />
          <StatCard label="Вес, т" value={totalTons.toFixed(2)} accent />
        </div>
      </div>
```

(`SlidersHorizontal` is already imported.)

- [ ] **Step 4: Portal orders — move "Новый заказ" link**

Open `portal/orders/page.tsx`. The button links to `/portal/orders/new`. Move it to `actions`. If it's a `<Link>`-wrapped button or a `<Button asChild>`/`router.push`, replicate exactly:

```tsx
      actions={
        <Button size="sm" asChild>
          <Link href="/portal/orders/new"><Plus className="size-4" /> <span className="hidden sm:inline">Новый заказ</span></Link>
        </Button>
      }
```

If the page's Button does not support `asChild`, instead use the page's existing approach (read it): e.g. `onClick={() => router.push("/portal/orders/new")}`. Replicate the working variant; remove the body button. Ensure `Link`/`Plus`/`Button` imports exist (they do if the body used them).

- [ ] **Step 5: Build**

Run: `cd frontend && npm run build`
Expected: build succeeds. Fix any import that was only used by the removed body button (unlikely — the moved button reuses the same imports).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/app/management/roles/page.tsx frontend/src/app/management/cameras/page.tsx frontend/src/app/warehouse/page.tsx frontend/src/app/portal/orders/page.tsx
git commit -m "feat: roles/cameras/warehouse/portal create buttons in topbar"
```

---

### Task 7: Visual verification in Docker

**Files:** none.

- [ ] **Step 1: Build and run**

Run: `docker compose up --build -d` then wait ~6s.

- [ ] **Step 2: Smoke-check serves**

Run: `curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3000/login`
Expected: 200.

- [ ] **Step 3: Visual check (manual)**

Log in. On each page (Orders, Clients, Catalog→Grades/Packagings/Products, Employees, Roles, Cameras, Warehouse) confirm: the create/action button now appears in the topbar (right side, before the theme toggle), opens the same modal/route, and the body no longer shows a duplicate button. Confirm permission-gated buttons (orders.create, employees.manage, cameras.manage, warehouse.adjust) are hidden for users without the perm (or visible as superuser). Check a narrow window — labels collapse, icons remain. Toggle dark theme.

- [ ] **Step 4: Tear down**

Run: `docker compose down`

- [ ] **Step 5: No commit (verification only).**

---

## Notes for the implementer

- Tasks 2–5 touch pages already restyled this session, so their button locations are known precisely (in a flex row beside search/StatCards). Task 6 touches un-restyled pages — READ each before editing and copy the exact onClick/gate.
- Don't invent handler names. Each moved button must use the SAME onClick the body button used (usually `() => setOpen(true)` or `() => { setError(""); setOpen(true); }`).
- The `actions` node is rendered inside the topbar's `flex items-center gap-3` — no extra wrapper needed; a single `<Button>` (or gate expression returning a Button or `undefined`) is correct.
- `undefined` (not `null` or `false`) is the clean "no button" value for gated cases, matching the prop type `React.ReactNode`.
- No backend, no tests. `npm run build` per task; Docker visual is the final gate.
