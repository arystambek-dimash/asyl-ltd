# Qoima Components Restyle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restyle base UI components (Card, Table, Badge) to qoima's Notion look and add StatCard, ProgressBar, FilterPills, SortableHeader; apply them on the Orders page as the reference.

**Architecture:** Edit the three base components' internal Tailwind classes (keeping all export names and Badge's `tone` API), add four new components under `ui/`, add a `.shadow-card` utility to globals.css, then rebuild the Orders page to use StatCard row + FilterPills + sortable table + dot badges. All other list pages inherit the Card/Table/Badge restyle automatically.

**Tech Stack:** Next.js 15 App Router, Tailwind v4 (CSS variables), lucide-react, cva already present.

## Global Constraints

- Adapt qoima classes to asyl tokens: use `var(--card)`, `var(--muted-foreground)`, `var(--border)`, `var(--ring)`, `var(--muted)`, `var(--success)` — NOT qoima's `bg-canvas`/`text-ink-3`.
- Keep export names: `Card/CardHeader/CardTitle/CardDescription/CardContent/CardFooter`, `Table/THead/TBody/TR/TH/TD`, `Badge`.
- Keep Badge tones `muted/primary/success/warning/destructive/outline` (StatusBadge + `ORDER_STATUS_TONE` depend on them). Only ADD an optional `dot?: boolean` prop.
- No component test runner exists; verify each task with `npm run build` (frontend) + final visual check in Docker.
- No backend changes; `feat/weights` logic untouched.

---

### Task 1: shadow-card utility + Card restyle

**Files:**
- Modify: `frontend/src/app/globals.css` (add `.shadow-card` utility)
- Modify: `frontend/src/components/ui/card.tsx`

**Interfaces:**
- Produces: `Card` with soft qoima shadow; same exports `Card/CardHeader/CardTitle/CardDescription/CardContent/CardFooter`, same props (`className` passthrough).

- [ ] **Step 1: Add `.shadow-card` utility to globals.css**

Append to `frontend/src/app/globals.css` (after the existing animation utilities, end of file):

```css
.shadow-card {
  box-shadow: 0 1px 0 0 rgba(15, 15, 15, 0.04), 0 1px 3px 0 rgba(15, 15, 15, 0.06);
}
.dark .shadow-card {
  box-shadow: 0 1px 0 0 rgba(0, 0, 0, 0.4), 0 1px 3px 0 rgba(0, 0, 0, 0.5);
}
```

- [ ] **Step 2: Restyle the `Card` wrapper in card.tsx**

In `frontend/src/components/ui/card.tsx`, change ONLY the `Card` function's className (keep all other exports unchanged):

```tsx
export function Card({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "rounded-xl border border-[var(--border)] bg-[var(--card)] text-[var(--card-foreground)] shadow-card",
        className
      )}
      {...props}
    />
  );
}
```

- [ ] **Step 3: Build to verify**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/app/globals.css frontend/src/components/ui/card.tsx
git commit -m "style: soft qoima card shadow"
```

---

### Task 2: Table restyle

**Files:**
- Modify: `frontend/src/components/ui/table.tsx`

**Interfaces:**
- Produces: same exports `Table/THead/TBody/TR/TH/TD`, same props; denser qoima styling.

- [ ] **Step 1: Replace table.tsx body**

Replace the whole file with (keeps all exports + className passthrough):

```tsx
import * as React from "react";
import { cn } from "@/lib/utils";

export function Table({ className, ...props }: React.HTMLAttributes<HTMLTableElement>) {
  return (
    <div className="w-full overflow-x-auto">
      <table
        className={cn("w-full text-[14px] border-separate border-spacing-0", className)}
        {...props}
      />
    </div>
  );
}
export function THead({ className, ...props }: React.HTMLAttributes<HTMLTableSectionElement>) {
  return (
    <thead
      className={cn("text-[12px] font-medium text-[var(--muted-foreground)]", className)}
      {...props}
    />
  );
}
export function TBody({ className, ...props }: React.HTMLAttributes<HTMLTableSectionElement>) {
  return <tbody className={cn("[&>tr:last-child>td]:border-0", className)} {...props} />;
}
export function TR({ className, ...props }: React.HTMLAttributes<HTMLTableRowElement>) {
  return (
    <tr
      className={cn(
        "group transition-colors hover:bg-[var(--muted)]/50 [&>td]:border-b [&>td]:border-[var(--border)]",
        className
      )}
      {...props}
    />
  );
}
export function TH({ className, ...props }: React.ThHTMLAttributes<HTMLTableCellElement>) {
  return (
    <th
      className={cn(
        "h-9 px-3 sm:px-4 text-left align-middle font-medium text-[var(--muted-foreground)]",
        className
      )}
      {...props}
    />
  );
}
export function TD({ className, ...props }: React.TdHTMLAttributes<HTMLTableCellElement>) {
  return <td className={cn("h-12 px-3 sm:px-4 align-middle", className)} {...props} />;
}
```

- [ ] **Step 2: Build to verify**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/ui/table.tsx
git commit -m "style: denser qoima table with hover + hairlines"
```

---

### Task 3: Badge restyle + dot prop

**Files:**
- Modify: `frontend/src/components/ui/badge.tsx`

**Interfaces:**
- Produces: `Badge` with same tones + new `dot?: boolean`. `StatusBadge` keeps working.

- [ ] **Step 1: Replace badge.tsx**

```tsx
import * as React from "react";
import { cn } from "@/lib/utils";

type Tone = "muted" | "primary" | "success" | "warning" | "destructive" | "outline";

const toneClasses: Record<Tone, string> = {
  muted: "bg-[var(--muted)] text-[var(--muted-foreground)]",
  primary: "bg-[var(--ring)]/12 text-[var(--ring)]",
  success: "bg-[var(--success)]/12 text-[var(--success)]",
  warning: "bg-[var(--warning)]/15 text-[var(--warning)]",
  destructive: "bg-[var(--destructive)]/12 text-[var(--destructive)]",
  outline: "bg-transparent text-[var(--muted-foreground)] border border-[var(--border)]",
};

const dotColor: Record<Tone, string> = {
  muted: "var(--muted-foreground)",
  primary: "var(--ring)",
  success: "var(--success)",
  warning: "var(--warning)",
  destructive: "var(--destructive)",
  outline: "var(--muted-foreground)",
};

export function Badge({
  tone = "muted",
  dot,
  className,
  children,
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & { tone?: Tone; dot?: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 px-2 h-[22px] text-[12px] rounded-md font-medium leading-none whitespace-nowrap",
        toneClasses[tone],
        className
      )}
      {...props}
    >
      {dot && (
        <span
          className="h-1.5 w-1.5 rounded-full"
          style={{ background: dotColor[tone] }}
        />
      )}
      {children}
    </span>
  );
}
```

- [ ] **Step 2: Build to verify**

Run: `cd frontend && npm run build`
Expected: build succeeds (StatusBadge still imports `Badge` with `tone`, unchanged API).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/ui/badge.tsx
git commit -m "style: Notion-soft badges + optional dot"
```

---

### Task 4: StatCard + ProgressBar components

**Files:**
- Create: `frontend/src/components/ui/stat-card.tsx`
- Create: `frontend/src/components/ui/progress-bar.tsx`

**Interfaces:**
- Produces:
  - `StatCard({ label, value, accent?, caption? })`
  - `ProgressBar({ pct, className? })`

- [ ] **Step 1: Create stat-card.tsx**

```tsx
import * as React from "react";
import { cn } from "@/lib/utils";

export function StatCard({
  label,
  value,
  accent,
  caption,
}: {
  label: string;
  value: React.ReactNode;
  accent?: boolean;
  caption?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col gap-3 rounded-lg border p-4 sm:p-5 transition-colors",
        accent
          ? "border-[var(--ring)]/20 bg-[var(--ring)]/10"
          : "border-[var(--border)] bg-[var(--card)] hover:border-[var(--ring)]/40"
      )}
    >
      <span className="text-[12px] font-medium text-[var(--muted-foreground)]">{label}</span>
      <div
        className={cn(
          "text-[24px] sm:text-[30px] leading-[1.1] tracking-tight tabular-nums font-semibold",
          accent ? "text-[var(--ring)]" : "text-[var(--foreground)]"
        )}
      >
        {value}
      </div>
      {caption && <span className="text-[12px] text-[var(--muted-foreground)]">{caption}</span>}
    </div>
  );
}
```

- [ ] **Step 2: Create progress-bar.tsx**

```tsx
import * as React from "react";
import { cn } from "@/lib/utils";

export function ProgressBar({ pct, className }: { pct: number; className?: string }) {
  const clamped = Math.max(0, Math.min(pct, 100));
  return (
    <div className={cn("h-1 w-full overflow-hidden rounded-full bg-[var(--muted)]", className)}>
      <div
        className={cn(
          "h-full rounded-full transition-all",
          clamped >= 100 ? "bg-[var(--success)]" : "bg-[var(--ring)]"
        )}
        style={{ width: `${clamped}%` }}
      />
    </div>
  );
}
```

- [ ] **Step 3: Build to verify**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/ui/stat-card.tsx frontend/src/components/ui/progress-bar.tsx
git commit -m "feat: StatCard + ProgressBar components"
```

---

### Task 5: FilterPills + SortableHeader components

**Files:**
- Create: `frontend/src/components/ui/filter-pills.tsx`
- Create: `frontend/src/components/ui/sortable-header.tsx`

**Interfaces:**
- Produces:
  - `FilterPills({ items, active, onChange })` where `items: { key: string; label: string; count: number }[]`, `active: string`, `onChange: (key: string) => void`.
  - `SortableHeader({ label, sortKey, activeKey, dir, onClick, align? })` where `dir: "asc" | "desc"`, `onClick: (k: string) => void`, `align?: "right"`. Renders a `TH`.

- [ ] **Step 1: Create filter-pills.tsx**

```tsx
import * as React from "react";
import { cn } from "@/lib/utils";

export type FilterPillItem = { key: string; label: string; count: number };

export function FilterPills({
  items,
  active,
  onChange,
}: {
  items: FilterPillItem[];
  active: string;
  onChange: (key: string) => void;
}) {
  return (
    <div className="inline-flex bg-[var(--muted)] border border-[var(--border)] rounded-md p-0.5">
      {items.map((it) => {
        const on = it.key === active;
        return (
          <button
            key={it.key}
            type="button"
            onClick={() => onChange(it.key)}
            className={cn(
              "h-7 px-2.5 inline-flex items-center gap-1.5 text-[13px] rounded transition-colors",
              on
                ? "bg-[var(--card)] text-[var(--foreground)] shadow-sm font-medium"
                : "text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
            )}
          >
            {it.label}
            <span
              className={cn(
                "text-[11px] tabular-nums",
                on ? "text-[var(--muted-foreground)]" : "text-[var(--muted-foreground)]/70"
              )}
            >
              {it.count}
            </span>
          </button>
        );
      })}
    </div>
  );
}
```

- [ ] **Step 2: Create sortable-header.tsx**

```tsx
import * as React from "react";
import { ArrowUp, ArrowDown, ArrowUpDown } from "lucide-react";
import { cn } from "@/lib/utils";
import { TH } from "./table";

export type SortDir = "asc" | "desc";

export function SortableHeader({
  label,
  sortKey,
  activeKey,
  dir,
  onClick,
  align,
}: {
  label: string;
  sortKey: string;
  activeKey: string;
  dir: SortDir;
  onClick: (k: string) => void;
  align?: "right";
}) {
  const isActive = activeKey === sortKey;
  return (
    <TH className={cn(align === "right" && "text-right")}>
      <button
        type="button"
        onClick={() => onClick(sortKey)}
        className={cn(
          "inline-flex items-center gap-1 transition-colors hover:text-[var(--foreground)]",
          align === "right" && "flex-row-reverse",
          isActive && "text-[var(--foreground)] font-medium"
        )}
      >
        {label}
        {isActive ? (
          dir === "asc" ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />
        ) : (
          <ArrowUpDown className="h-3 w-3 opacity-40" />
        )}
      </button>
    </TH>
  );
}
```

- [ ] **Step 3: Build to verify**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/ui/filter-pills.tsx frontend/src/components/ui/sortable-header.tsx
git commit -m "feat: FilterPills + SortableHeader components"
```

---

### Task 6: Rebuild Orders page as the reference

**Files:**
- Modify: `frontend/src/app/orders/page.tsx` (the `OrdersPage` component only — leave `NewOrderForm` untouched)

**Interfaces:**
- Consumes: `StatCard`, `ProgressBar` (not needed here), `FilterPills`, `SortableHeader`, restyled `Card/Table/Badge`, existing `StatusBadge`.

This task rewrites the `OrdersPage` default export. Keep the imports for `NewOrderForm` (Modal, Input, etc.) and the `NewOrderForm` function exactly as-is.

- [ ] **Step 1: Update imports at the top of orders/page.tsx**

Add these imports (keep all existing ones):

```tsx
import { StatCard } from "@/components/ui/stat-card";
import { FilterPills } from "@/components/ui/filter-pills";
import { SortableHeader, type SortDir } from "@/components/ui/sortable-header";
import { ORDER_STATUS_LABELS } from "@/lib/constants";
import { Search } from "lucide-react";
```

- [ ] **Step 2: Replace the `OrdersPage` function body**

Replace the entire `export default function OrdersPage() { ... }` with:

```tsx
export default function OrdersPage() {
  const { data: orders, loading, reload } = useApi<Order[]>("/orders/");
  const { me } = useAuth();
  const canCreate = can(me, "orders.create");
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("all");
  const [sortKey, setSortKey] = useState("id");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  const list = orders ?? [];
  const activeCount = list.filter(
    (o) => o.status !== "shipped" && o.status !== "cancelled"
  ).length;
  const totalSum = list.reduce((s, o) => s + Number(o.total_amount || 0), 0);

  // Build status filter pills from the statuses actually present.
  const presentStatuses = Array.from(new Set(list.map((o) => o.status)));
  const pills = [
    { key: "all", label: "Все", count: list.length },
    ...presentStatuses.map((st) => ({
      key: st,
      label: ORDER_STATUS_LABELS[st] ?? st,
      count: list.filter((o) => o.status === st).length,
    })),
  ];

  const toggleSort = (k: string) => {
    if (k === sortKey) setSortDir(sortDir === "asc" ? "desc" : "asc");
    else { setSortKey(k); setSortDir("asc"); }
  };

  const filtered = list.filter((o) => {
    if (status !== "all" && o.status !== status) return false;
    if (!q) return true;
    const hay = `${o.id} ${o.client_name ?? ""} ${o.truck_number ?? ""}`.toLowerCase();
    return hay.includes(q.toLowerCase());
  });

  const sorted = [...filtered].sort((a, b) => {
    let av: number | string, bv: number | string;
    if (sortKey === "amount") { av = Number(a.total_amount || 0); bv = Number(b.total_amount || 0); }
    else if (sortKey === "client") { av = a.client_name ?? ""; bv = b.client_name ?? ""; }
    else if (sortKey === "status") { av = a.status; bv = b.status; }
    else { av = a.id; bv = b.id; }
    const cmp = typeof av === "number" && typeof bv === "number"
      ? av - bv
      : String(av).localeCompare(String(bv), "ru");
    return sortDir === "asc" ? cmp : -cmp;
  });

  return (
    <AppShell title="Заказы" section="Работа" description="Заказы клиентов: позиции, оплаты, машина и плановая дата прибытия на отгрузку.">
      <section className="mb-5 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <StatCard label="Всего заказов" value={String(list.length)} />
        <StatCard label="В процессе" value={String(activeCount)} />
        <StatCard label="Сумма" value={`${formatMoney(String(totalSum))} ₸`} accent />
      </section>

      <div className="mb-4 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div className="relative max-w-md flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
          <Input className="pl-9" placeholder="Поиск по клиенту, номеру или #ID"
            value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <div className="flex items-center gap-2 overflow-x-auto">
          <FilterPills items={pills} active={status} onChange={setStatus} />
          {canCreate && (
            <Button size="sm" onClick={() => setOpen(true)}>
              <Plus className="size-4" /> Новый заказ
            </Button>
          )}
        </div>
      </div>

      <Card>
        <CardContent className="pt-6">
          {loading ? (
            <p className="py-6 text-center text-sm text-[var(--muted-foreground)]">Загрузка…</p>
          ) : (
            <Table>
              <THead>
                <TR>
                  <SortableHeader label="№" sortKey="id" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
                  <SortableHeader label="Клиент" sortKey="client" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
                  <TH>Машина</TH>
                  <TH>Прибытие</TH>
                  <SortableHeader label="Сумма" sortKey="amount" activeKey={sortKey} dir={sortDir} onClick={toggleSort} align="right" />
                  <TH>Оплачено</TH>
                  <SortableHeader label="Статус" sortKey="status" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
                </TR>
              </THead>
              <TBody>
                {sorted.map((o) => (
                  <TR key={o.id}>
                    <TD className="font-medium">
                      <Link href={`/orders/${o.id}`} className="hover:underline">#{o.id}</Link>
                    </TD>
                    <TD>{o.client_name || `Клиент #${o.client}`}</TD>
                    <TD className="font-medium tabular-nums">{o.truck_number ? formatPlate(o.truck_number) : "—"}</TD>
                    <TD>{o.arrival_date ? new Date(o.arrival_date).toLocaleDateString("ru-RU") : "—"}</TD>
                    <TD className="text-right tabular-nums">{formatMoney(o.total_amount)} ₸</TD>
                    <TD className="tabular-nums text-[var(--muted-foreground)]">{formatMoney(o.paid_total)} ₸</TD>
                    <TD><StatusBadge status={o.status} dot /></TD>
                  </TR>
                ))}
                {sorted.length === 0 && (
                  <TR><TD colSpan={7} className="py-4 text-center text-[var(--muted-foreground)]">
                    Заказов пока нет.</TD></TR>)}
              </TBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Modal open={open} onClose={() => setOpen(false)} title="Новый заказ" className="max-w-2xl">
        {open && <NewOrderForm onCancel={() => setOpen(false)}
          onDone={() => { setOpen(false); reload(); }} />}
      </Modal>
    </AppShell>
  );
}
```

- [ ] **Step 3: Pass `dot` through StatusBadge**

`StatusBadge` is `frontend/src/components/status-badge.tsx`. Add a `dot` passthrough so the orders table can request dots. Replace it with:

```tsx
import { Badge } from "@/components/ui/badge";
import { ORDER_STATUS_LABELS, ORDER_STATUS_TONE } from "@/lib/constants";

export function StatusBadge({ status, dot }: { status: string; dot?: boolean }) {
  return (
    <Badge tone={ORDER_STATUS_TONE[status] ?? "muted"} dot={dot}>
      {ORDER_STATUS_LABELS[status] ?? status}
    </Badge>
  );
}
```

- [ ] **Step 4: Build to verify**

Run: `cd frontend && npm run build`
Expected: build succeeds, no type errors. If `formatMoney` rejects a number, note it takes a string — `totalSum` is converted via `String(totalSum)` already in the StatCard call.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/orders/page.tsx frontend/src/components/status-badge.tsx
git commit -m "feat: rebuild Orders page with stat cards, filter pills, sortable table"
```

---

### Task 7: Visual verification in Docker

**Files:** none (verification).

- [ ] **Step 1: Build and run**

Run: `docker compose up --build -d` then wait ~6s.

- [ ] **Step 2: Smoke-check pages serve**

Run: `curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3000/login`
Expected: 200.

- [ ] **Step 3: Visual check (manual)**

Log in, open **Orders**: confirm stat-card row (3 cards, the «Сумма» one with blue soft background), filter pills with counts, sortable headers with arrows, status badges with colored dots, denser table with row hover. Then open **Clients** (a page that only inherits the base restyle): confirm Card has soft shadow, table is denser, no breakage. Toggle dark theme — check both.

- [ ] **Step 4: Tear down**

Run: `docker compose down`

- [ ] **Step 5: No commit (verification only).**

---

## Notes for the implementer

- `ORDER_STATUS_LABELS` / `ORDER_STATUS_TONE` live in `frontend/src/lib/constants.ts` — confirm the import path before Task 6 (it's already imported by status-badge.tsx).
- `formatMoney` in `@/lib/utils` takes a string; always pass `String(num)`.
- Only the `OrdersPage` export is rewritten in Task 6 — `NewOrderForm` and its imports stay. Don't drop the `Select`, `Label`, `LicensePlateInput`, `Trash2`, `useRouter` imports (NewOrderForm uses them).
- Other list pages (clients, catalog, employees, warehouse, reports, portal) are NOT edited; they inherit the Card/Table/Badge restyle automatically. That's intended scope.
- No component unit tests exist — `npm run build` is the gate per task, Docker visual is the final gate.
