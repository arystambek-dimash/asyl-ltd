"use client";
import { useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { formatPlate } from "@/components/ui/license-plate-input";
import { StatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { StatCard } from "@/components/ui/stat-card";
import { FilterDropdown } from "@/components/ui/filter-dropdown";
import { SortableHeader, type SortDir } from "@/components/ui/sortable-header";
import { ErrorAlert } from "@/components/ui/data-state";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { ActionMenu, type ActionMenuItem } from "@/components/ui/action-menu";
import { OrderForm } from "@/components/order-form";
import {
  ORDER_PUBLIC_STATUSES,
  ORDER_STATUS_LABELS,
  PAYMENT_STATUS_LABELS,
  PAYMENT_STATUS_TONE,
  orderStatusGroup,
} from "@/lib/constants";
import { useApi } from "@/lib/use-api";
import { useAuth } from "@/store/auth";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import { cn, formatDateTime, formatMoney } from "@/lib/utils";
import { useDismiss } from "@/lib/use-dismiss";
import {
  Archive,
  Building2,
  CalendarDays,
  Check,
  ChevronDown,
  ChevronLeft,
  Pencil,
  Plus,
  RotateCcw,
  Search,
  Settings2,
  Trash2,
} from "lucide-react";
import type { Department, DepartmentSummary, Order } from "@/lib/types";

// Позиции и цены редактируются до начала загрузки (включая «ожидает загрузки»).
function isEditable(o: Order): boolean {
  return ["draft", "pending", "confirmed", "arrived"].includes(o.status);
}

function shortDate(value: string) {
  if (!value) return "";
  const [year, month, day] = value.split("-");
  return `${day}.${month}.${year}`;
}

function DateRangeFilter({ dateFrom, dateTo, onDateFrom, onDateTo }: {
  dateFrom: string;
  dateTo: string;
  onDateFrom: (value: string) => void;
  onDateTo: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const active = Boolean(dateFrom || dateTo);
  const value = dateFrom && dateTo
    ? `${shortDate(dateFrom)} — ${shortDate(dateTo)}`
    : dateFrom
      ? `с ${shortDate(dateFrom)}`
      : dateTo
        ? `по ${shortDate(dateTo)}`
        : "Все";

  useDismiss(ref, () => setOpen(false), open);

  return (
    <div ref={ref} className="relative shrink-0">
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        aria-haspopup="dialog"
        aria-expanded={open}
        className={cn(
          "flex h-9 items-center gap-1.5 rounded-md border px-3 text-[13px] transition-colors",
          active
            ? "border-[var(--primary)]/40 bg-[var(--primary)]/5 text-[var(--foreground)]"
            : "border-[var(--border)] bg-[var(--card)] text-[var(--muted-foreground)] hover:text-[var(--foreground)]",
        )}
      >
        <CalendarDays className="size-3.5" />
        <span className="text-[var(--muted-foreground)]">Дата:</span>
        <span className="max-w-52 truncate font-medium">{value}</span>
        <ChevronDown className={cn("size-3.5 transition-transform", open && "rotate-180")} />
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="Фильтр по дате создания"
          className="absolute left-0 z-40 mt-1 w-[min(300px,calc(100vw-2rem))] rounded-xl border bg-[var(--card)] p-3 shadow-xl sm:left-auto sm:right-0"
        >
          <div className="mb-3">
            <div className="text-sm font-semibold">Дата создания</div>
            <div className="text-xs text-[var(--muted-foreground)]">Обе даты входят в выбранный период.</div>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <label className="text-xs text-[var(--muted-foreground)]">
              С
              <Input
                type="date"
                value={dateFrom}
                max={dateTo || undefined}
                onChange={(event) => onDateFrom(event.target.value)}
                className="mt-1 h-9 px-2.5 text-xs"
              />
            </label>
            <label className="text-xs text-[var(--muted-foreground)]">
              По
              <Input
                type="date"
                value={dateTo}
                min={dateFrom || undefined}
                onChange={(event) => onDateTo(event.target.value)}
                className="mt-1 h-9 px-2.5 text-xs"
              />
            </label>
          </div>
          <div className="mt-3 flex items-center justify-between border-t pt-3">
            <button
              type="button"
              disabled={!active}
              onClick={() => { onDateFrom(""); onDateTo(""); }}
              className="text-xs font-medium text-[var(--muted-foreground)] transition-colors hover:text-[var(--foreground)] disabled:opacity-40"
            >
              Сбросить
            </button>
            <Button type="button" size="sm" onClick={() => setOpen(false)}>Готово</Button>
          </div>
        </div>
      )}
    </div>
  );
}

/* ── Доли статусов в сумме: стековый бар + легенда ──────────────────────── */
const STATUS_SHARE_COLORS: Record<string, string> = {
  pending: "var(--warning)",
  confirmed: "var(--ring)",
  shipped: "var(--success)",
};

function StatusShareBar({ orders, total }: { orders: Order[]; total: number }) {
  const shares = ORDER_PUBLIC_STATUSES
    .filter((g) => g !== "cancelled")
    .map((g) => ({
      key: g,
      label: ORDER_STATUS_LABELS[g],
      value: orders
        .filter((o) => orderStatusGroup(o.status) === g)
        .reduce((s, o) => s + Number(o.total_amount || 0), 0),
    }))
    .filter((s) => s.value > 0);
  if (total <= 0 || shares.length === 0) return null;
  return (
    <div className="mt-1 flex flex-col gap-2">
      <div className="flex h-2 w-full gap-px overflow-hidden rounded-full">
        {shares.map((s) => (
          <div key={s.key} title={`${s.label}: ${formatMoney(s.value)} ₸`}
            style={{ width: `${(s.value / total) * 100}%`, background: STATUS_SHARE_COLORS[s.key] }} />
        ))}
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-[var(--muted-foreground)]">
        {shares.map((s) => (
          <span key={s.key} className="flex items-center gap-1.5">
            <span className="size-2 rounded-full" style={{ background: STATUS_SHARE_COLORS[s.key] }} />
            {s.label}
            <span className="tabular-nums font-medium text-[var(--foreground)]">
              {Math.round((s.value / total) * 100)}%
            </span>
          </span>
        ))}
      </div>
    </div>
  );
}

function DepartmentBadge({ order }: { order: Order }) {
  return (
    <span className="inline-flex max-w-44 items-center gap-1.5 rounded-full border bg-[var(--card)] px-2.5 py-1 text-[11px] font-semibold">
      <span className="size-2 shrink-0 rounded-full"
        style={{ backgroundColor: order.department_color ?? "#64748B" }} />
      <span className="truncate">{order.department_name ?? order.department ?? "Без отдела"}</span>
    </span>
  );
}

const DEPARTMENT_COLORS = [
  "#315FD5", "#D68B2C", "#238C6E", "#B84A5A", "#7654B3", "#3B7F91", "#6B7280",
];

function DepartmentManager({ onChanged }: { onChanged: () => void }) {
  const { data, reload } = useApi<Department[]>("/departments/?all=1");
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Department | null>(null);
  const [name, setName] = useState("");
  const [color, setColor] = useState(DEPARTMENT_COLORS[0]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  function begin(department?: Department) {
    setEditing(department ?? null);
    setName(department?.name ?? "");
    setColor(department?.color ?? DEPARTMENT_COLORS[(data?.length ?? 0) % DEPARTMENT_COLORS.length]);
    setError("");
  }

  async function save() {
    setSaving(true); setError("");
    try {
      if (editing) await api.patch(`/departments/${editing.id}/`, { name, color });
      else await api.post("/departments/", { name, color });
      begin();
      await reload();
      onChanged();
    } catch (cause) { setError(apiError(cause)); }
    finally { setSaving(false); }
  }

  async function update(department: Department, payload: Partial<Department>) {
    setSaving(true); setError("");
    try {
      await api.patch(`/departments/${department.id}/`, payload);
      await reload();
      onChanged();
    } catch (cause) { setError(apiError(cause)); }
    finally { setSaving(false); }
  }

  return (
    <>
      <Button size="sm" variant="outline" onClick={() => { setOpen(true); begin(); }}>
        <Settings2 className="size-4" /> Отделы
      </Button>
      <Modal open={open} onClose={() => setOpen(false)}
        eyebrow="Заказы · Настройка"
        title="Отделы продаж"
        description="Добавляйте отделы здесь — они сразу появятся в новом заказе, фильтрах и аналитике."
        className="max-w-2xl">
        <div className="grid gap-5 md:grid-cols-[1.15fr_.85fr]">
          <div className="flex flex-col gap-2">
            {(data ?? []).map((department) => (
              <div key={department.id}
                className={cn(
                  "group flex items-center gap-3 rounded-xl border p-3 transition",
                  department.is_active ? "bg-[var(--card)]" : "bg-[var(--muted)]/35 opacity-65",
                )}>
                <span className="size-3 shrink-0 rounded-full ring-4 ring-current/10"
                  style={{ backgroundColor: department.color, color: department.color }} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-semibold">{department.name}</span>
                    {department.is_default && (
                      <span className="rounded-full bg-[var(--muted)] px-2 py-0.5 text-[10px] font-medium text-[var(--muted-foreground)]">основной</span>
                    )}
                  </div>
                  <div className="text-[11px] text-[var(--muted-foreground)]">
                    {department.order_count} заказов · {department.is_active ? "доступен" : "отключён"}
                  </div>
                </div>
                <button type="button" onClick={() => begin(department)}
                  className="rounded-lg p-2 text-[var(--muted-foreground)] hover:bg-[var(--accent)] hover:text-[var(--foreground)]"
                  title="Изменить отдел">
                  <Pencil className="size-3.5" />
                </button>
                <button type="button" disabled={saving || (department.is_default && department.is_active)}
                  onClick={() => void update(department, { is_active: !department.is_active })}
                  className="min-w-20 rounded-lg border px-2.5 py-1.5 text-[11px] font-medium disabled:opacity-40">
                  {department.is_active ? "Отключить" : "Включить"}
                </button>
              </div>
            ))}
            {(data ?? []).length === 0 && (
              <div className="rounded-xl border border-dashed p-8 text-center text-sm text-[var(--muted-foreground)]">
                Создайте первый отдел для новых заказов.
              </div>
            )}
          </div>

          <div className="h-fit rounded-2xl border bg-[var(--muted)]/25 p-4">
            <div className="mb-4 flex items-center justify-between gap-3">
              <div>
                <div className="text-sm font-bold">{editing ? "Изменить отдел" : "Новый отдел"}</div>
                <div className="text-[11px] text-[var(--muted-foreground)]">Название и цвет метки</div>
              </div>
              {editing && (
                <button type="button" onClick={() => begin()}
                  className="text-xs font-medium text-[var(--muted-foreground)] hover:text-[var(--foreground)]">
                  Сбросить
                </button>
              )}
            </div>
            <Input autoFocus value={name} onChange={(event) => setName(event.target.value)}
              maxLength={100} placeholder="Например, Оптовые продажи" />
            <div className="mt-3 flex flex-wrap gap-2">
              {DEPARTMENT_COLORS.map((item) => (
                <button key={item} type="button" onClick={() => setColor(item)}
                  aria-label={`Цвет ${item}`}
                  className={cn("flex size-8 items-center justify-center rounded-full transition-transform hover:scale-110",
                    color === item && "ring-2 ring-[var(--foreground)] ring-offset-2 ring-offset-[var(--card)]")}
                  style={{ backgroundColor: item }}>
                  {color === item && <Check className="size-4 text-white" />}
                </button>
              ))}
            </div>
            {error && <p className="mt-3 text-sm text-[var(--destructive)]">{error}</p>}
            <Button className="mt-4 w-full" disabled={saving || !name.trim()}
              onClick={() => void save()}>
              {saving ? "Сохранение…" : editing ? "Сохранить изменения" : "Добавить отдел"}
            </Button>
            {editing && !editing.is_default && (
              <button type="button" disabled={saving}
                onClick={() => void update(editing, { is_default: true, is_active: true })}
                className="mt-3 w-full text-center text-xs font-medium text-[var(--muted-foreground)] hover:text-[var(--foreground)]">
                Сделать основным
              </button>
            )}
          </div>
        </div>
      </Modal>
    </>
  );
}

function DepartmentAnalytics({ rows, active, onSelect }: {
  rows: DepartmentSummary[];
  active: string;
  onSelect: (code: string) => void;
}) {
  if (rows.length === 0) return null;
  const totalOrders = rows.reduce((sum, row) => sum + row.orders, 0);
  return (
    <section className="mb-5 overflow-hidden rounded-2xl border bg-[var(--card)] shadow-card">
      <div className="flex flex-wrap items-end justify-between gap-3 border-b px-5 py-4">
        <div>
          <div className="flex items-center gap-2 text-sm font-bold"><Building2 className="size-4" /> По отделам</div>
          <p className="mt-1 text-xs text-[var(--muted-foreground)]">Живая структура заказов за выбранный период</p>
        </div>
        <button type="button" onClick={() => onSelect("all")}
          className={cn("rounded-full px-3 py-1.5 text-xs font-semibold transition",
            active === "all" ? "bg-[var(--foreground)] text-[var(--background)]" : "bg-[var(--muted)] hover:bg-[var(--accent)]")}>
          Все отделы · {totalOrders}
        </button>
      </div>
      <div className="grid sm:grid-cols-2 xl:grid-cols-3">
        {rows.map((row) => (
          <button key={row.code} type="button" onClick={() => onSelect(row.code)}
            className={cn(
              "relative min-h-32 border-b p-5 text-left transition hover:bg-[var(--muted)]/35 sm:border-r",
              active === row.code && "bg-[var(--muted)]/45",
            )}>
            <span className="absolute inset-y-4 left-0 w-1 rounded-r-full" style={{ backgroundColor: row.color }} />
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="text-sm font-bold">{row.name}</div>
                <div className="mt-0.5 text-[11px] text-[var(--muted-foreground)]">{row.active} сейчас в работе</div>
              </div>
              <span className="text-2xl font-black tabular-nums">{row.orders}</span>
            </div>
            <div className="mt-5 flex items-end justify-between gap-3">
              <span className="text-xs text-[var(--muted-foreground)]">{row.shipped} завершено</span>
              <span className="text-sm font-bold tabular-nums">{formatMoney(row.revenue)} ₸</span>
            </div>
          </button>
        ))}
      </div>
    </section>
  );
}

/* ── Док архива в углу: клик раскрывает стопку удалённых, как Stacks в macOS ── */
function ArchiveDock({ trashed, onOpenArchive, onChanged }: {
  trashed: Order[];
  onOpenArchive: () => void;
  onChanged: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [purgeItem, setPurgeItem] = useState<Order | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  // Пока открыт диалог удаления, клик по нему не должен схлопывать стопку.
  useDismiss(ref, () => setOpen(false), open && !purgeItem);

  // Веер стопки: ближе к кнопке — удалённые последними.
  const recent = [...trashed]
    .sort((a, b) => (b.deleted_at ?? "").localeCompare(a.deleted_at ?? ""))
    .slice(0, 4);
  const fan = [...recent].reverse();

  async function act(o: Order, fn: () => Promise<unknown>) {
    setBusyId(o.id); setError("");
    try { await fn(); onChanged(); }
    catch (e) { setError(apiError(e)); throw e; }
    finally { setBusyId(null); }
  }
  const restore = (o: Order) => act(o, () => api.post(`/orders/${o.id}/restore/`)).catch(() => {});
  const purge = (o: Order) =>
    act(o, () => api.delete(`/orders/${o.id}/purge/`))
      .then(() => setPurgeItem(null)).catch(() => setPurgeItem(null));

  // Задержки анимации: карточки «выезжают» из кнопки снизу вверх.
  const delay = (indexFromBottom: number) => ({ animationDelay: `${indexFromBottom * 45}ms` });

  // Портал в body: у контента AppShell есть transform (animate-fade-up),
  // внутри него fixed считается от контейнера, а не от окна.
  return createPortal(
    <div ref={ref} className="fixed bottom-5 right-4 z-[90] flex flex-col items-end sm:bottom-6 sm:right-6">
      {open && (
        <div className="mb-3 flex w-[300px] max-w-[calc(100vw-2rem)] flex-col gap-2">
          <button
            type="button"
            style={delay(fan.length + 1)}
            onClick={() => { setOpen(false); onOpenArchive(); }}
            className="animate-fade-up flex items-center justify-center gap-1.5 self-center rounded-full border bg-[var(--popover)] px-4 py-1.5 text-xs font-medium shadow-lg transition-colors hover:bg-[var(--accent)]"
          >
            Открыть архив{trashed.length > 0 ? ` (${trashed.length})` : ""}
            <ChevronDown className="size-3.5 -rotate-90" />
          </button>
          {error && (
            <p style={delay(fan.length)} className="animate-fade-up rounded-lg border bg-[var(--popover)] px-3 py-2 text-xs text-[var(--destructive)] shadow-lg">
              {error}
            </p>
          )}
          {fan.length === 0 ? (
            <div style={delay(0)} className="animate-fade-up rounded-xl border bg-[var(--popover)] px-4 py-5 text-center text-sm text-[var(--muted-foreground)] shadow-lg">
              Архив пуст.
            </div>
          ) : fan.map((o, i) => (
            <div key={o.id} style={delay(fan.length - 1 - i)}
              className="animate-fade-up flex items-center justify-between gap-2 rounded-xl border bg-[var(--popover)] p-3 shadow-[0_10px_35px_rgba(0,0,0,0.16)]">
              <div className="min-w-0">
                <div className="flex items-center gap-2 text-sm">
                  <span className="font-semibold">#{o.id}</span>
                  <span className="truncate">{o.client_name || `Клиент #${o.client}`}</span>
                </div>
                <div className="mt-0.5 text-xs text-[var(--muted-foreground)]">
                  <span className="tabular-nums">{formatMoney(o.total_amount)} ₸</span>
                  {o.deleted_at && <> · {formatDateTime(o.deleted_at)}</>}
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-1">
                <Button size="sm" variant="outline" disabled={busyId === o.id}
                  title="Восстановить заказ" onClick={() => restore(o)}>
                  <RotateCcw className="size-3.5" /> Вернуть
                </Button>
                <Button size="sm" variant="ghost" disabled={busyId === o.id}
                  className="text-[var(--muted-foreground)] hover:text-[var(--destructive)]"
                  title="Удалить навсегда" onClick={() => setPurgeItem(o)}>
                  <Trash2 className="size-4" />
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}

      <ConfirmDialog
        open={!!purgeItem}
        onClose={() => setPurgeItem(null)}
        title="Удалить заказ навсегда?"
        description={purgeItem
          ? `Заказ #${purgeItem.id} (${purgeItem.client_name ?? "клиент"}) будет удалён безвозвратно вместе с позициями и оплатами. Восстановить его будет нельзя.`
          : ""}
        confirmLabel="Удалить навсегда"
        busy={purgeItem ? busyId === purgeItem.id : false}
        error={error}
        onConfirm={() => purgeItem && purge(purgeItem)}
      />

      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        title="Архив заказов"
        aria-label="Архив заказов"
        aria-haspopup="menu"
        aria-expanded={open}
        className={cn(
          "relative flex size-12 items-center justify-center rounded-2xl border shadow-lg transition-all",
          open
            ? "border-[var(--foreground)] bg-[var(--foreground)] text-[var(--background)]"
            : "bg-[var(--card)] text-[var(--muted-foreground)] hover:-translate-y-0.5 hover:text-[var(--foreground)] hover:shadow-xl",
        )}
      >
        <Archive className="size-5" />
        {trashed.length > 0 && !open && (
          <span className="absolute -right-1.5 -top-1.5 flex h-5 min-w-5 items-center justify-center rounded-full bg-[var(--foreground)] px-1.5 text-[11px] font-semibold tabular-nums text-[var(--background)]">
            {trashed.length}
          </span>
        )}
      </button>
    </div>,
    document.body,
  );
}

function OrdersPageInner() {
  const router = useRouter();
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [status, setStatus] = useState("all");
  const [dept, setDept] = useState("all");
  // Фильтры уходят на бэк: список, карточки и сумма считаются по выборке сервера.
  const ordersUrl = useMemo(() => {
    const params = new URLSearchParams();
    if (dateFrom) params.set("date_from", dateFrom);
    if (dateTo) params.set("date_to", dateTo);
    if (dept !== "all") params.set("department", dept);
    if (status !== "all") params.set("status_group", status);
    const query = params.toString();
    return `/orders/${query ? `?${query}` : ""}`;
  }, [dateFrom, dateTo, dept, status]);
  const { data: orders, loading, error, reload } = useApi<Order[]>(ordersUrl);
  const summaryUrl = useMemo(() => {
    const params = new URLSearchParams();
    if (dateFrom) params.set("date_from", dateFrom);
    if (dateTo) params.set("date_to", dateTo);
    if (status !== "all") params.set("status_group", status);
    const query = params.toString();
    return `/orders/department-summary/${query ? `?${query}` : ""}`;
  }, [dateFrom, dateTo, status]);
  const { data: departmentSummary, reload: reloadSummary } = useApi<DepartmentSummary[]>(summaryUrl);
  const { data: departments, reload: reloadDepartments } = useApi<Department[]>("/departments/");
  const { me } = useAuth();
  const canCreate = can(me, "orders.create");
  const canEdit = can(me, "orders.edit");
  const canManageDepartments = can(me, "rbac.manage");
  const showDept = (departments?.length ?? 0) > 1;
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Order | null>(null);
  const [q, setQ] = useState("");
  const [sortKey, setSortKey] = useState("id");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [view, setView] = useState<"orders" | "archive">("orders");
  const [delItem, setDelItem] = useState<Order | null>(null);
  const [delBusy, setDelBusy] = useState(false);
  const [delError, setDelError] = useState("");
  // Стопка архива в углу видна всегда — держим список удалённых под рукой.
  const { data: trashed, reload: reloadTrash } = useApi<Order[]>(canEdit ? "/orders/trash/" : null);

  async function confirmDelete() {
    if (!delItem) return;
    setDelBusy(true); setDelError("");
    try {
      await api.delete(`/orders/${delItem.id}/`);
      setDelItem(null); reload(); reloadTrash();
    } catch (e) { setDelError(apiError(e)); } finally { setDelBusy(false); }
  }

  // Карандаш и архив живут в одном меню «⋮» строки заказа.
  const rowActions = (o: Order): ActionMenuItem[] => [
    { key: "edit", label: "Изменить", icon: Pencil, disabled: !isEditable(o),
      hint: isEditable(o) ? undefined : "Заказ в этом статусе не редактируется",
      onSelect: () => setEditing(o) },
    { key: "archive", label: "В архив", icon: Archive, tone: "destructive" as const,
      onSelect: () => { setDelError(""); setDelItem(o); } },
  ];

  const list = orders ?? [];
  const filtered = list.filter((o) => {
    if (!q) return true;
    const hay = `${o.id} ${o.client_name ?? ""} ${o.truck_number ?? ""}`.toLowerCase();
    return hay.includes(q.toLowerCase());
  });

  // Карточки считаются по видимой выборке (фильтры + поиск): выбрал
  // «На рассмотрении» — видишь их сумму. Отменённые и отклонённые
  // не искажают цифры, «в процессе» = ещё не загружен.
  const countable = filtered.filter((o) => orderStatusGroup(o.status) !== "cancelled");
  const activeCount = countable.filter((o) => orderStatusGroup(o.status) !== "shipped").length;
  const totalSum = countable.reduce((s, o) => s + Number(o.total_amount || 0), 0);

  // Счётчики в опциях не показываем: при серверной фильтрации в наличии
  // только выбранная группа, честных цифр по остальным нет.
  const pills = [
    { key: "all", label: "Все" },
    ...ORDER_PUBLIC_STATUSES.map((st) => ({ key: st, label: ORDER_STATUS_LABELS[st] })),
  ];

  const toggleSort = (k: string) => {
    if (k === sortKey) setSortDir(sortDir === "asc" ? "desc" : "asc");
    else { setSortKey(k); setSortDir("asc"); }
  };

  // Загруженные всегда падают вниз списка — сверху активная работа.
  const doneRank = (o: Order) => (orderStatusGroup(o.status) === "shipped" ? 1 : 0);
  const sorted = [...filtered].sort((a, b) => {
    const rank = doneRank(a) - doneRank(b);
    if (rank !== 0) return rank;
    let av: number | string, bv: number | string;
    if (sortKey === "amount") { av = Number(a.total_amount || 0); bv = Number(b.total_amount || 0); }
    else if (sortKey === "client") { av = a.client_name ?? ""; bv = b.client_name ?? ""; }
    else if (sortKey === "status") { av = a.status; bv = b.status; }
    else if (sortKey === "created") { av = a.created_at; bv = b.created_at; }
    else { av = a.id; bv = b.id; }
    const cmp = typeof av === "number" && typeof bv === "number"
      ? av - bv
      : String(av).localeCompare(String(bv), "ru");
    return sortDir === "asc" ? cmp : -cmp;
  });

  return (
    <AppShell title="Заказы" section="Работа" description="Единый центр заказов: отделы, статусы, выручка и отгрузка."
      actions={(canCreate || canManageDepartments) ? (
        <div className="flex items-center gap-2">
          {canManageDepartments && (
            <DepartmentManager onChanged={() => {
              void reloadDepartments(); void reloadSummary(); void reload();
            }} />
          )}
          {canCreate && (
            <Button size="sm" aria-label="Новый заказ" onClick={() => setOpen(true)}>
              <Plus className="size-4" /> <span className="hidden sm:inline">Новый заказ</span>
            </Button>
          )}
        </div>
      ) : undefined}>
      {view === "archive" ? (
        <ArchiveView
          showDept={showDept}
          onBack={() => { reload(); reloadTrash(); setView("orders"); }}
          onRestored={() => { reload(); reloadTrash(); }}
        />
      ) : (
      <>
      <DepartmentAnalytics rows={departmentSummary ?? []} active={dept} onSelect={setDept} />
      <section className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-3">
        <StatCard label="Всего заказов" value={String(list.length)} />
        <StatCard label="В процессе" value={String(activeCount)} />
        <StatCard label="Сумма" value={`${formatMoney(totalSum)} ₸`} accent
          caption="Без отменённых и отклонённых"
          className="col-span-2 sm:col-span-1">
          <StatusShareBar orders={countable} total={totalSum} />
        </StatCard>
      </section>

      <div className="mb-4 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div className="relative max-w-md flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
          <Input className="pl-9" placeholder="Поиск по клиенту, номеру или #ID"
            value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <DateRangeFilter
            dateFrom={dateFrom}
            dateTo={dateTo}
            onDateFrom={setDateFrom}
            onDateTo={setDateTo}
          />
          {(departments?.length ?? 0) > 0 && (
            <FilterDropdown label="Отдел" active={dept} onChange={setDept} options={[
              { key: "all", label: "Все" },
              ...(departments ?? []).map((department) => ({
                key: department.code,
                label: department.name,
              })),
            ]} />
          )}
          <FilterDropdown label="Статус" options={pills} active={status} onChange={setStatus} />
        </div>
      </div>

      {error && <div className="mb-4"><ErrorAlert message={error} onRetry={reload} /></div>}

      {/* Мобильные карточки: таблица на телефоне нечитаемая. */}
      <div className="flex flex-col gap-3 md:hidden">
        {loading ? (
          <p className="py-6 text-center text-sm text-[var(--muted-foreground)]">Загрузка…</p>
        ) : sorted.length === 0 ? (
          <p className="py-6 text-center text-sm text-[var(--muted-foreground)]">Заказов пока нет.</p>
        ) : sorted.map((o) => (
          <div key={o.id} onClick={() => router.push(`/orders/${o.id}`)}
            className="flex cursor-pointer flex-col gap-2.5 rounded-xl border bg-[var(--card)] p-4 shadow-card">
            <div className="flex items-start justify-between gap-2">
              <div className="flex items-center gap-2">
                <span className="text-sm font-semibold">#{o.id}</span>
                {showDept && <DepartmentBadge order={o} />}
              </div>
              <StatusBadge status={o.status} dot />
            </div>
            <div className="text-sm font-medium">{o.client_name || `Клиент #${o.client}`}</div>
            <div className="text-xs text-[var(--muted-foreground)]">Создан {formatDateTime(o.created_at)}</div>
            <div className="grid grid-cols-2 gap-2 text-sm">
              <div>
                <div className="text-[11px] text-[var(--muted-foreground)]">Сумма</div>
                <div className="font-semibold tabular-nums">{formatMoney(o.total_amount)} ₸</div>
              </div>
              <div>
                <div className="text-[11px] text-[var(--muted-foreground)]">Оплачено</div>
                <div className="tabular-nums">{formatMoney(o.paid_total)} ₸</div>
              </div>
              {o.truck_number && (
                <div>
                  <div className="text-[11px] text-[var(--muted-foreground)]">Машина</div>
                  <div className="tabular-nums">{formatPlate(o.truck_number)}</div>
                </div>
              )}
              {o.arrival_date && (
                <div>
                  <div className="text-[11px] text-[var(--muted-foreground)]">Прибытие</div>
                  <div>{new Date(o.arrival_date).toLocaleDateString("ru-RU")}</div>
                </div>
              )}
            </div>
            <div className="flex items-center justify-between border-t pt-2">
              {o.status === "shipped" && o.payment_status ? (
                <Badge tone={PAYMENT_STATUS_TONE[o.payment_status] ?? "muted"}>
                  {PAYMENT_STATUS_LABELS[o.payment_status] ?? o.payment_status}
                </Badge>
              ) : <span />}
              {canEdit && <ActionMenu items={rowActions(o)} />}
            </div>
          </div>
        ))}
      </div>

      <Card className="hidden md:block">
        <CardContent className="pt-6">
          {loading ? (
            <p className="py-6 text-center text-sm text-[var(--muted-foreground)]">Загрузка…</p>
          ) : (
            <Table>
              <THead>
                <TR>
                  <SortableHeader label="№" sortKey="id" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
                  <SortableHeader label="Создан" sortKey="created" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
                  {showDept && <TH>Отдел</TH>}
                  <SortableHeader label="Клиент" sortKey="client" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
                  <SortableHeader label="Сумма" sortKey="amount" activeKey={sortKey} dir={sortDir} onClick={toggleSort} align="right" />
                  <SortableHeader label="Статус" sortKey="status" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
                  {canEdit && <TH></TH>}
                </TR>
              </THead>
              <TBody>
                {sorted.map((o) => (
                  <TR key={o.id} className="cursor-pointer"
                    onClick={() => router.push(`/orders/${o.id}`)}>
                    <TD className="font-medium">
                      <Link href={`/orders/${o.id}`} className="hover:underline"
                        onClick={(e) => e.stopPropagation()}>#{o.id}</Link>
                    </TD>
                    <TD className="whitespace-nowrap tabular-nums text-[var(--muted-foreground)]">
                      {formatDateTime(o.created_at)}
                    </TD>
                    {showDept && <TD><DepartmentBadge order={o} /></TD>}
                    <TD>{o.client_name || `Клиент #${o.client}`}</TD>
                    <TD className="text-right tabular-nums">{formatMoney(o.total_amount)} ₸</TD>
                    <TD>
                      <div className="flex flex-wrap items-center gap-1.5">
                        <StatusBadge status={o.status} dot />
                        {o.payment_status && (
                          <Badge tone={PAYMENT_STATUS_TONE[o.payment_status] ?? "muted"}>
                            {PAYMENT_STATUS_LABELS[o.payment_status] ?? o.payment_status}
                          </Badge>
                        )}
                      </div>
                    </TD>
                    {canEdit && (
                      <TD onClick={(e) => e.stopPropagation()}>
                        <div className="flex justify-end">
                          <ActionMenu items={rowActions(o)} />
                        </div>
                      </TD>
                    )}
                  </TR>
                ))}
                {sorted.length === 0 && (
                  <TR><TD colSpan={(showDept ? 6 : 5) + (canEdit ? 1 : 0)} className="py-4 text-center text-[var(--muted-foreground)]">
                    Заказов пока нет.</TD></TR>)}
              </TBody>
            </Table>
          )}
        </CardContent>
      </Card>
      </>
      )}

      {canEdit && view === "orders" && (
        <ArchiveDock
          trashed={trashed ?? []}
          onOpenArchive={() => setView("archive")}
          onChanged={() => { reload(); reloadTrash(); }}
        />
      )}

      <ConfirmDialog
        open={!!delItem}
        onClose={() => setDelItem(null)}
        title="Переместить заказ в архив?"
        description={delItem
          ? `Заказ #${delItem.id} (${delItem.client_name ?? "клиент"}) исчезнет из рабочих списков и отчётов. Его можно будет восстановить из архива.`
          : ""}
        confirmLabel="В архив"
        busy={delBusy}
        error={delError}
        onConfirm={confirmDelete}
      />

      <Modal open={open} onClose={() => setOpen(false)}
        eyebrow="Работа · Заказ"
        title="Новый заказ"
        description="Отдел, клиент, позиции и плановая дата прибытия."
        className="max-w-2xl">
        {open && <OrderForm onCancel={() => setOpen(false)}
          onDone={() => { setOpen(false); reload(); reloadSummary(); }} />}
      </Modal>

      <Modal open={!!editing} onClose={() => setEditing(null)}
        eyebrow={editing ? `Работа · Заказ #${editing.id}` : "Работа · Заказ"}
        title="Изменить заказ"
        description="Позиции, цены, машина и дата прибытия. Изменения фиксируются в журнале."
        className="max-w-2xl">
        {editing && <OrderForm editing={editing}
          onCancel={() => setEditing(null)}
          onDone={() => { setEditing(null); reload(); reloadSummary(); }} />}
      </Modal>
    </AppShell>
  );
}

/* ── Архив: удалённые заказы с восстановлением ──────────────────────────── */
function ArchiveView({ showDept, onBack, onRestored }: {
  showDept: boolean;
  onBack: () => void;
  onRestored: () => void;
}) {
  const { data: trashed, loading, error, reload } = useApi<Order[]>("/orders/trash/");
  const [busyId, setBusyId] = useState<number | null>(null);
  const [actErr, setActErr] = useState("");
  const [purgeItem, setPurgeItem] = useState<Order | null>(null);

  async function act(o: Order, fn: () => Promise<unknown>) {
    setBusyId(o.id); setActErr("");
    try { await fn(); reload(); onRestored(); }
    catch (e) { setActErr(apiError(e)); }
    finally { setBusyId(null); }
  }
  const restore = (o: Order) => act(o, () => api.post(`/orders/${o.id}/restore/`));
  const purge = async (o: Order) => {
    await act(o, () => api.delete(`/orders/${o.id}/purge/`));
    setPurgeItem(null);
  };

  const list = trashed ?? [];
  return (
    <>
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="flex size-10 items-center justify-center rounded-xl bg-[var(--secondary)]">
            <Archive className="size-5" />
          </div>
          <div>
            <h2 className="font-semibold">Архив заказов</h2>
            <p className="text-sm text-[var(--muted-foreground)]">
              Эти заказы не участвуют в рабочих списках и отчётах.
            </p>
          </div>
        </div>
        <Button size="sm" variant="outline" onClick={onBack}>
          <ChevronLeft className="size-4" /> К заказам
        </Button>
      </div>
      {actErr && <div className="mb-4"><ErrorAlert message={actErr} /></div>}
      {error && !trashed && <div className="mb-4"><ErrorAlert message={error} onRetry={reload} /></div>}
      <Card>
        <CardContent className="pt-6">
          <Table>
            <THead>
              <TR>
                <TH>№</TH>
                {showDept && <TH>Отдел</TH>}
                <TH>Клиент</TH>
                <TH className="text-right">Сумма</TH>
                <TH>Удалён</TH>
                <TH></TH>
              </TR>
            </THead>
            <TBody>
              {loading ? (
                <TR><TD colSpan={showDept ? 6 : 5} className="py-8 text-center text-[var(--muted-foreground)]">Загрузка…</TD></TR>
              ) : list.length === 0 ? (
                <TR><TD colSpan={showDept ? 6 : 5} className="py-8 text-center text-[var(--muted-foreground)]">В архиве пока нет заказов.</TD></TR>
              ) : list.map((o) => (
                <TR key={o.id}>
                  <TD className="font-medium">#{o.id}</TD>
                  {showDept && (
                    <TD><DepartmentBadge order={o} /></TD>
                  )}
                  <TD>{o.client_name || `Клиент #${o.client}`}</TD>
                  <TD className="text-right tabular-nums">{formatMoney(o.total_amount)} ₸</TD>
                  <TD className="whitespace-nowrap text-[var(--muted-foreground)]">
                    <div className="text-sm">{o.deleted_at ? formatDateTime(o.deleted_at) : "—"}</div>
                    {o.deleted_by_name && <div className="text-xs">{o.deleted_by_name}</div>}
                  </TD>
                  <TD>
                    <div className="flex items-center justify-end gap-1.5">
                      <Button size="sm" variant="outline" disabled={busyId === o.id}
                        onClick={() => restore(o)}>
                        <RotateCcw className="size-3.5" /> Восстановить
                      </Button>
                      <Button size="sm" variant="ghost" disabled={busyId === o.id}
                        className="text-[var(--muted-foreground)] hover:text-[var(--destructive)]"
                        title="Удалить навсегда" onClick={() => setPurgeItem(o)}>
                        <Trash2 className="size-4" />
                      </Button>
                    </div>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </CardContent>
      </Card>

      <ConfirmDialog
        open={!!purgeItem}
        onClose={() => setPurgeItem(null)}
        title="Удалить заказ навсегда?"
        description={purgeItem
          ? `Заказ #${purgeItem.id} (${purgeItem.client_name ?? "клиент"}) будет удалён безвозвратно вместе с позициями и оплатами. Восстановить его будет нельзя.`
          : ""}
        confirmLabel="Удалить навсегда"
        busy={purgeItem ? busyId === purgeItem.id : false}
        error={actErr}
        onConfirm={() => purgeItem && purge(purgeItem)}
      />
    </>
  );
}

export default function OrdersPage() {
  return <RequirePerm perm="orders.view" title="Заказы"><OrdersPageInner /></RequirePerm>;
}
