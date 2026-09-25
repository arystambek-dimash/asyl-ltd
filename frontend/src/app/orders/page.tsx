"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SearchInput } from "@/components/ui/search-input";
import { Modal } from "@/components/ui/modal";
import { StatusBadge } from "@/components/status-badge";
import { OrderPaymentBadge, paymentBadgeStatus } from "@/components/payments/order-payment-badge";
import { EmptyRow, Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { StatCard } from "@/components/ui/stat-card";
import { BADGE_TONE_COLOR } from "@/components/ui/badge";
import { DepartmentBadge, DepartmentDot } from "@/components/ui/department-badge";
import { FilterDropdown, FilterTrigger } from "@/components/ui/filter-dropdown";
import { SortableHeader, useSortState } from "@/components/ui/sortable-header";
import { ErrorAlert } from "@/components/ui/data-state";
import { ActionMenu } from "@/components/ui/action-menu";
import { ActionCard } from "@/components/ui/action-card";
import { OrderStatusSelect } from "@/components/order-status-select";
import { useOrderStatusChange } from "@/components/orders/use-order-status-change";
import { useOrderActions } from "@/components/orders/order-actions";
import { ALL_CLIENTS_STATEMENT_SECTIONS, StatementExportModal } from "@/components/statement-export-modal";
import { ArchiveDock } from "@/components/orders/archive-dock";
import { useOrderArchiveActions } from "@/components/orders/use-order-archive-actions";
import { DepartmentManager } from "@/components/orders/department-manager";
import { OrderRequestsSection } from "@/components/orders/order-requests";
import { useOrderRequests } from "@/components/orders/use-order-requests";
import { departmentScope } from "@/components/cashier/scope";
import { Tabs, type TabDef } from "@/components/ui/tabs";
import { ORDER_PUBLIC_STATUSES, ORDER_STATUS_LABELS, orderStatusTone } from "@/lib/constants";
import { useApi } from "@/lib/use-api";
import { usePagedApi } from "@/lib/use-paged-api";
import { useDebounced } from "@/lib/use-debounced";
import { LoadMore } from "@/components/ui/load-more";
import { useAuth } from "@/store/auth";
import { can } from "@/lib/can";
import { clientLabel, orderItemsSummary } from "@/lib/orders";
import { otherCurrencyAmounts } from "@/lib/currency-map";
import { cn, formatCurrency, formatDateTime, formatIsoDate } from "@/lib/utils";
import { orderTransportText } from "@/lib/wagons";
import { useDismiss } from "@/lib/use-dismiss";
import { clearOrderDraft, loadOrderDraft } from "@/lib/order-draft";
import {
  Archive,
  BarChart3,
  Building2,
  CalendarDays,
  Check,
  ChevronLeft,
  CircleDollarSign,
  Clock3,
  CopyPlus,
  Eraser,
  FileSpreadsheet,
  ListChecks,
  Plus,
  RotateCcw,
  Trash2,
} from "lucide-react";
import type { Department, DepartmentSummary, Order, OrderListSummary } from "@/lib/types";

const OrderForm = dynamic(() => import("@/components/order-form").then((m) => m.OrderForm));

function DateRangeFilter({
  dateFrom,
  dateTo,
  onDateFrom,
  onDateTo,
}: {
  dateFrom: string;
  dateTo: string;
  onDateFrom: (value: string) => void;
  onDateTo: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const active = Boolean(dateFrom || dateTo);
  const value =
    dateFrom && dateTo
      ? `${formatIsoDate(dateFrom)} — ${formatIsoDate(dateTo)}`
      : dateFrom
        ? `с ${formatIsoDate(dateFrom)}`
        : dateTo
          ? `по ${formatIsoDate(dateTo)}`
          : "Все";

  useDismiss(ref, () => setOpen(false), open);

  return (
    <div ref={ref} className="relative shrink-0">
      <FilterTrigger
        label="Дата"
        value={value}
        icon={CalendarDays}
        active={active}
        open={open}
        onClick={() => setOpen((current) => !current)}
        aria-haspopup="dialog"
      />

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
              onClick={() => {
                onDateFrom("");
                onDateTo("");
              }}
              className="text-xs font-medium text-[var(--muted-foreground)] transition-colors hover:text-[var(--foreground)] disabled:opacity-40"
            >
              Сбросить
            </button>
            <Button type="button" size="sm" onClick={() => setOpen(false)}>
              Готово
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

/* ── Доли статусов в сумме: стековый бар + легенда ──────────────────────── */

function StatusShareBar({
  byGroup,
  total,
  currency,
}: {
  byGroup: Record<string, string>;
  total: number;
  currency: string;
}) {
  const shares = ORDER_PUBLIC_STATUSES.filter((g) => g !== "cancelled")
    .map((g) => ({ key: g, label: ORDER_STATUS_LABELS[g], value: Number(byGroup[g] ?? 0) }))
    .filter((s) => s.value > 0);
  if (total <= 0 || shares.length === 0) return null;
  return (
    <div className="mt-1 flex flex-col gap-2">
      <div className="flex h-2 w-full gap-px overflow-hidden rounded-full">
        {shares.map((s) => (
          <div
            key={s.key}
            title={`${s.label}: ${formatCurrency(s.value, currency)}`}
            style={{ width: `${(s.value / total) * 100}%`, background: BADGE_TONE_COLOR[orderStatusTone(s.key)] }}
          />
        ))}
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-[var(--muted-foreground)]">
        {shares.map((s) => (
          <span key={s.key} className="flex items-center gap-1.5">
            <span className="size-2 rounded-full" style={{ background: BADGE_TONE_COLOR[orderStatusTone(s.key)] }} />
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

/** Отдел в списке заказов — без подстановки отдела клиента, в отличие от OrderDepartmentBadge в заявках. */
function OrderListDepartmentBadge({ order }: { order: Order }) {
  return (
    <DepartmentBadge
      name={order.department ? order.department_name : null}
      color={order.department_color}
      className="max-w-44 bg-[var(--card)]"
    />
  );
}

/**
 * Разбивка выручки отдела: сколько получено, сколько висит долгом и в каком
 * состоянии расчёты по заказам.
 *
 * Полоса показывает долю полученных денег в выручке — она отвечает на вопрос
 * «оборот большой, а деньги где». Доли считаются только по основной валюте:
 * складывать ₸ и $ в один процент нельзя.
 */
function PaymentBreakdown({ row }: { row: DepartmentSummary }) {
  const currency = row.revenue_currency;
  const revenue = Number(row.revenue ?? 0);
  const paid = Number(row.paid ?? 0);
  const debt = Number(row.debt ?? 0);
  const settled = row.paid_orders + row.partial_orders + row.unpaid_orders;
  if (!settled && revenue <= 0) return null;

  const paidShare = revenue > 0 ? Math.min(100, Math.round((paid / revenue) * 100)) : 0;
  const counters = [
    { label: "оплачено", value: row.paid_orders, tone: "bg-[var(--success)]" },
    { label: "частично", value: row.partial_orders, tone: "bg-[var(--warning)]" },
    { label: "не оплачено", value: row.unpaid_orders, tone: "bg-[var(--destructive)]" },
  ].filter((counter) => counter.value > 0);

  return (
    <div className="mt-3 border-t pt-3">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[10px] uppercase tracking-wide text-[var(--muted-foreground)]">Получено</span>
        <span className="truncate text-xs font-bold tabular-nums">
          {formatCurrency(row.paid, currency)}
          {revenue > 0 && <span className="ml-1 font-medium text-[var(--muted-foreground)]">· {paidShare}%</span>}
        </span>
      </div>
      <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-[var(--muted)]">
        <div className="h-full rounded-full bg-[var(--success)]" style={{ width: `${paidShare}%` }} />
      </div>

      {debt > 0 && (
        <div className="mt-2 flex items-baseline justify-between gap-2">
          <span className="text-[10px] uppercase tracking-wide text-[var(--muted-foreground)]">
            Долг · {row.debt_orders} зак.
          </span>
          <span className="truncate text-xs font-bold tabular-nums text-[var(--destructive)]">
            {formatCurrency(row.debt, currency)}
          </span>
        </div>
      )}
      {/* Долги во второй валюте — отдельной строкой, без сложения с основной. */}
      {Object.entries(row.debt_by_currency)
        .filter(([code, amount]) => code !== currency && Number(amount) > 0)
        .map(([code, amount]) => (
          <div key={code} className="mt-0.5 text-right text-[11px] tabular-nums text-[var(--muted-foreground)]">
            {formatCurrency(amount, code)}
          </div>
        ))}

      {counters.length > 0 && (
        <div className="mt-2.5 flex flex-wrap gap-x-3 gap-y-1">
          {counters.map((counter) => (
            <span key={counter.label} className="flex items-center gap-1.5 text-[11px] text-[var(--muted-foreground)]">
              <span className={cn("size-1.5 rounded-full", counter.tone)} />
              <span className="font-semibold tabular-nums text-[var(--foreground)]">{counter.value}</span>
              {counter.label}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

type AnalyticsView = "departments" | "overview";

const ANALYTICS_VIEW_TABS: TabDef[] = [
  { key: "departments", label: "По отделам", icon: Building2, panelId: "orders-analytics-departments" },
  { key: "overview", label: "Общая", icon: BarChart3, panelId: "orders-analytics-overview" },
];

function OrdersAnalytics({
  rows,
  active,
  onSelect,
  overviewUrl,
  scopeName,
}: {
  rows: DepartmentSummary[];
  active: string;
  onSelect: (code: string) => void;
  /** Итоги «Общей» вкладки: те же фильтры и поиск, что у списка. */
  overviewUrl: string;
  /** Отдел сотрудника, закреплённого за отделом: сервер отдаёт карточку только его отдела. */
  scopeName?: string;
}) {
  const [view, setView] = useState<AnalyticsView>("departments");
  const totalOrders = rows.reduce((sum, row) => sum + row.orders, 0);
  const activeDepartment = rows.find((row) => row.code === active);
  // Итоги считает сервер по всей выборке: раньше складывалась только
  // загруженная страница списка (50 заказов), и при большем числе заказов
  // «Всего» и «Сумма» были занижены.
  const overview = useApi<OrderListSummary>(view === "overview" ? overviewUrl : null);
  const summary = overview.data;
  // Сумма по валютам: тенге и доллары не складываются. Крупно — основная
  // валюта, остальные строкой в подписи.
  const mainCurrency = summary?.total_currency ?? "KZT";
  const mainTotal = Number(summary?.total_by_currency[mainCurrency] ?? 0);
  const otherSums = summary ? otherCurrencyAmounts(summary.total_by_currency, mainCurrency) : [];

  return (
    <section className="flex flex-col">
      <div className="flex flex-col gap-3 border-b pb-4 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-xs text-[var(--muted-foreground)]">
          {view === "departments"
            ? "Сравнение отделов: нажмите на отдел, чтобы показать его заказы"
            : "Общие показатели по текущим фильтрам и поиску"}
        </p>
        <Tabs
          variant="segment"
          label="Вид аналитики заказов"
          tabs={ANALYTICS_VIEW_TABS}
          active={view}
          onChange={(key) => setView(key as AnalyticsView)}
          className="w-fit"
        />
      </div>

      {view === "departments" ? (
        <div
          id="orders-analytics-departments"
          role="tabpanel"
          aria-labelledby="orders-analytics-departments-tab"
          className="pt-4"
        >
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2 px-1">
            <p className="text-xs text-[var(--muted-foreground)]">
              {activeDepartment ? (
                <>
                  Показаны заказы отдела <strong className="text-[var(--foreground)]">{activeDepartment.name}</strong>
                </>
              ) : (
                <>
                  {scopeName ? `Отдел ${scopeName}` : "Все отделы"} ·{" "}
                  <strong className="text-[var(--foreground)] tabular-nums">{totalOrders}</strong> заказов
                </>
              )}
            </p>
            {active !== "all" && (
              <button
                type="button"
                onClick={() => onSelect("all")}
                className="rounded-full bg-[var(--muted)] px-3 py-1.5 text-[11px] font-semibold transition hover:bg-[var(--accent)]"
              >
                Сбросить выбор
              </button>
            )}
          </div>
          {rows.length > 0 ? (
            <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,280px),1fr))] gap-3">
              {rows.map((row) => (
                <button
                  key={row.code}
                  type="button"
                  onClick={() => onSelect(row.code)}
                  aria-pressed={active === row.code}
                  className={cn(
                    "group relative min-h-36 overflow-hidden rounded-xl border p-4 text-left transition-all hover:-translate-y-0.5 hover:border-[var(--ring)]/35 hover:shadow-md sm:p-5",
                    active === row.code
                      ? "border-[var(--ring)]/45 bg-[var(--muted)]/45 ring-1 ring-[var(--ring)]/20"
                      : "bg-[var(--card)]",
                  )}
                >
                  <span className="absolute inset-y-0 left-0 w-1" style={{ backgroundColor: row.color }} />
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="truncate text-sm font-bold">{row.name}</div>
                      <div className="mt-1 flex items-center gap-1.5 text-[11px] text-[var(--muted-foreground)]">
                        <DepartmentDot color={row.color} className="size-1.5" />
                        {row.active} сейчас в работе
                      </div>
                    </div>
                    <span className="text-2xl font-black tabular-nums">{row.orders}</span>
                  </div>
                  <div className="mt-5 grid grid-cols-2 gap-3 border-t pt-3">
                    <div>
                      <div className="text-[10px] uppercase tracking-wide text-[var(--muted-foreground)]">
                        Отгружено
                      </div>
                      <div className="mt-1 text-sm font-bold tabular-nums">{row.shipped}</div>
                    </div>
                    <div className="text-right">
                      <div className="text-[10px] uppercase tracking-wide text-[var(--muted-foreground)]">
                        Стоимость заказов
                      </div>
                      <div className="mt-1 truncate text-sm font-bold tabular-nums">
                        {formatCurrency(row.revenue, row.revenue_currency)}
                      </div>
                      {/* Вторая валюта отдельной строкой: раньше на её месте
                          было слово «По валютам» и суммы не было видно вовсе. */}
                      {Object.entries(row.revenue_by_currency)
                        .filter(([currency]) => currency !== row.revenue_currency)
                        .map(([currency, amount]) => (
                          <div
                            key={currency}
                            className="truncate text-[11px] tabular-nums text-[var(--muted-foreground)]"
                          >
                            {formatCurrency(amount, currency)}
                          </div>
                        ))}
                    </div>
                  </div>

                  {/* Из чего состоит выручка: сколько уже получено и сколько
                      висит долгом. Одной цифры оборота для этого мало. */}
                  <PaymentBreakdown row={row} />
                </button>
              ))}
            </div>
          ) : (
            <div className="rounded-xl border border-dashed px-4 py-10 text-center text-sm text-[var(--muted-foreground)]">
              За выбранный период данных по отделам нет.
            </div>
          )}
        </div>
      ) : (
        <div
          id="orders-analytics-overview"
          role="tabpanel"
          aria-labelledby="orders-analytics-overview-tab"
          className="grid grid-cols-2 gap-3 pt-4 sm:grid-cols-3"
        >
          {overview.error && (
            <div className="col-span-full">
              <ErrorAlert message={overview.error} onRetry={overview.reload} />
            </div>
          )}
          <StatCard label="Всего заказов" value={summary ? String(summary.orders) : "—"} icon={ListChecks} />
          <StatCard label="В процессе" value={summary ? String(summary.active) : "—"} icon={Clock3} />
          <StatCard
            label="Сумма"
            value={summary ? formatCurrency(mainTotal, mainCurrency) : "—"}
            icon={CircleDollarSign}
            accent
            caption={
              otherSums.length > 0
                ? `ещё ${otherSums.map(([c, v]) => formatCurrency(v, c)).join(", ")} · без отменённых`
                : "Без отменённых и отклонённых"
            }
            className="col-span-2 sm:col-span-1"
          >
            {summary && <StatusShareBar byGroup={summary.by_status_group} total={mainTotal} currency={mainCurrency} />}
          </StatCard>
        </div>
      )}
    </section>
  );
}

function OrderTemplatePicker({
  orders,
  selected,
  onSelect,
  draftSaved,
  onClear,
}: {
  orders: Order[];
  selected: Order | null;
  onSelect: (order: Order | null) => void;
  draftSaved: boolean;
  onClear: () => void;
}) {
  const sorted = [...orders].sort((a, b) => b.id - a.id);

  return (
    <section className="flex flex-col gap-2 rounded-2xl border border-slate-200 bg-slate-50/70 px-3.5 py-3 sm:flex-row sm:items-center">
      <div className="flex min-w-0 flex-1 items-center gap-2.5">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-blue-600 text-white">
          <CopyPlus className="size-4" />
        </span>
        <div className="min-w-0">
          <div className="text-sm font-bold text-slate-900">Повторить старый заказ</div>
          <div className="truncate text-xs text-slate-500">
            {selected
              ? `${selected.client_name} · ${orderItemsSummary(selected) || "Без позиций"} · ${formatCurrency(selected.total_amount, selected.currency)}`
              : "Данные заполнятся из шаблона, заказ сохранится после вашей проверки."}
          </div>
          {draftSaved && (
            <div role="status" className="mt-0.5 flex items-center gap-1 text-[11px] font-medium text-emerald-700">
              <Check className="size-3" /> Черновик сохранён — можно закрыть окно и вернуться
            </div>
          )}
        </div>
      </div>
      <div className="flex w-full items-center gap-2 sm:w-auto">
        <select
          aria-label="Шаблон заказа"
          value={selected ? String(selected.id) : ""}
          onChange={(event) => {
            const value = Number(event.target.value);
            onSelect(sorted.find((order) => order.id === value) ?? null);
          }}
          className="h-9 min-w-0 flex-1 rounded-xl border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-700 shadow-sm outline-none transition focus:border-blue-400 focus:ring-2 focus:ring-blue-100 sm:w-64 sm:flex-none"
        >
          <option value="">Новый заказ с нуля</option>
          {sorted.map((order) => (
            <option key={order.id} value={order.id}>
              #{order.id} · {clientLabel(order)} · {formatDateTime(order.created_at)}
            </option>
          ))}
        </select>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-9 shrink-0 rounded-xl"
          disabled={!draftSaved && !selected}
          onClick={onClear}
          title="Очистить форму и черновик"
        >
          <Eraser className="size-4" /> Очистить всё
        </Button>
      </div>
    </section>
  );
}

type OrdersTab = "orders" | "requests";

const tabFromQuery = (value: string | null): OrdersTab => (value === "requests" ? value : "orders");

function OrdersPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [status, setStatus] = useState("all");
  const [dept, setDept] = useState("all");
  const [q, setQ] = useState("");
  const search = useDebounced(q.trim());
  const { sortKey, sortDir, toggleSort } = useSortState("id", "desc");
  const [view, setView] = useState<"orders" | "archive">("orders");
  const [tab, setTab] = useState<OrdersTab>(() => tabFromQuery(searchParams.get("tab")));
  // Фильтры уходят на бэк: список, карточки и сумма считаются по выборке сервера.
  const listFilters = useMemo(() => {
    const params = new URLSearchParams();
    if (dateFrom) params.set("date_from", dateFrom);
    if (dateTo) params.set("date_to", dateTo);
    if (dept !== "all") params.set("department", dept);
    if (status !== "all") params.set("status_group", status);
    if (search) params.set("search", search);
    return params.toString();
  }, [dateFrom, dateTo, dept, status, search]);
  const ordersUrl = `/orders/?${listFilters ? `${listFilters}&` : ""}ordering=${sortDir === "desc" ? "-" : ""}${sortKey}`;
  const paged = usePagedApi<Order>(view === "orders" ? ordersUrl : null, 50);
  const orders = paged.items;
  const { loading, error, reload } = paged;
  const summaryUrl = useMemo(() => {
    const params = new URLSearchParams();
    if (dateFrom) params.set("date_from", dateFrom);
    if (dateTo) params.set("date_to", dateTo);
    if (status !== "all") params.set("status_group", status);
    const query = params.toString();
    return `/orders/department-summary/${query ? `?${query}` : ""}`;
  }, [dateFrom, dateTo, status]);
  // Аналитика по отделам скрыта в окне: сводка грузится, только пока окно открыто.
  const [analyticsOpen, setAnalyticsOpen] = useState(false);
  const { data: departmentSummary } = useApi<DepartmentSummary[]>(
    view === "orders" && analyticsOpen ? summaryUrl : null,
  );
  const { data: departments, reload: reloadDepartments } = useApi<Department[]>("/departments/");
  const { me } = useAuth();
  const canCreate = can(me, "orders.create");
  const canEdit = can(me, "orders.edit");
  const canExport = can(me, "reports.export");
  const canRollback = can(me, "orders.rollback");
  const canManageDepartments = can(me, "sys_permissions.manage");
  // Заявки клиентов разбирает сотрудник с правом подтверждения; закреплённый за отделом видит свой отдел.
  const canReviewOrders = can(me, "orders.confirm");
  const { assigned } = departmentScope(me);
  const activeTab: OrdersTab = canReviewOrders && tab === "requests" ? "requests" : "orders";
  const requests = useOrderRequests(canReviewOrders && view === "orders", reload);
  function chooseTab(key: string) {
    const next = tabFromQuery(key);
    setTab(next);
    router.replace(next === "orders" ? "/orders" : `/orders?tab=${next}`, { scroll: false });
  }
  const showDept = (departments?.length ?? 0) > 1 || orders.some((order) => !order.department);
  const [open, setOpen] = useState(false);
  const [templateOrder, setTemplateOrder] = useState<Order | null>(null);
  const [draftSaved, setDraftSaved] = useState(false);
  // Смена ключа пересоздаёт форму заказа с нуля (кнопка «Очистить всё»).
  const [orderFormResetKey, setOrderFormResetKey] = useState(0);
  const [statementOpen, setStatementOpen] = useState(false);
  const { data: trashPreview, reload: reloadTrashPreview } = useApi<{ count: number; results: Order[] }>(
    canEdit && view === "orders" ? "/orders/trash-preview/" : null,
  );
  const {
    data: trashed,
    loading: trashLoading,
    error: trashError,
    reload: reloadTrash,
  } = useApi<Order[]>(canEdit && view === "archive" ? "/orders/trash/" : null);

  const requestedTemplateId = Number(searchParams.get("template") || 0);
  const {
    data: requestedTemplate,
    error: requestedTemplateError,
    reload: reloadRequestedTemplate,
  } = useApi<Order>(open && canCreate && requestedTemplateId > 0 ? `/orders/${requestedTemplateId}/` : null);
  const templateOrders = useMemo(() => {
    if (!requestedTemplate || orders.some((order) => order.id === requestedTemplate.id)) return orders;
    return [requestedTemplate, ...orders];
  }, [orders, requestedTemplate]);

  useEffect(() => {
    if (requestedTemplateId > 0 && canCreate) setOpen(true);
  }, [requestedTemplateId, canCreate]);
  useEffect(() => {
    if (requestedTemplate) setTemplateOrder(requestedTemplate);
  }, [requestedTemplate]);

  function openNewOrder() {
    // Незаконченный заказ продолжается с того же шаблона, на котором его бросили.
    setTemplateOrder(loadOrderDraft(me?.id)?.template ?? null);
    setOpen(true);
  }

  function closeNewOrder() {
    setOpen(false);
    setTemplateOrder(null);
    if (requestedTemplateId) router.replace("/orders");
  }

  function clearNewOrder() {
    clearOrderDraft(me?.id);
    setDraftSaved(false);
    setTemplateOrder(null);
    setOrderFormResetKey((key) => key + 1);
    if (requestedTemplateId) router.replace("/orders");
  }

  const statusChange = useOrderStatusChange({
    canEdit,
    canRollback,
    // Заявку подтверждает окно карточки: ?confirm=1 открывает его сразу.
    onConfirm: (order) => router.push(`/orders/${order.id}?confirm=1`),
    onChanged: () => Promise.all([reload(), reloadTrashPreview()]),
  });

  // Правка, фиксация, стоимость и архив живут в одном меню «⋮» строки заказа.
  const orderActions = useOrderActions({
    onChanged: reload,
    onArchived: () => Promise.all([reload(), reloadTrashPreview()]),
  });
  const hasActions = orderActions.available;

  const statusCell = (o: Order) =>
    canEdit && statusChange.canChoose(o) ? (
      <OrderStatusSelect
        status={o.status}
        disabled={statusChange.busyId === o.id}
        onChange={(target) => statusChange.choose(o, target)}
      />
    ) : (
      <StatusBadge status={o.status} dot />
    );

  // Счётчики в опциях не показываем: при серверной фильтрации в наличии
  // только выбранная группа, честных цифр по остальным нет.
  const pills = [
    { key: "all", label: "Все" },
    ...ORDER_PUBLIC_STATUSES.map((st) => ({ key: st, label: ORDER_STATUS_LABELS[st] })),
  ];

  return (
    <AppShell
      title="Заказы"
      section="Работа"
      description="Единый центр заказов: отделы, статусы, выручка и отгрузка."
      actions={
        canCreate || canManageDepartments || canExport ? (
          <div className="flex items-center gap-2">
            {canManageDepartments && (
              <DepartmentManager
                onChanged={() => {
                  void reloadDepartments();
                  void reload();
                }}
              />
            )}
            {canExport && (
              <Button
                size="sm"
                variant="outline"
                aria-label="Общая Excel-выписка"
                onClick={() => setStatementOpen(true)}
              >
                <FileSpreadsheet className="size-4 text-emerald-600" />
                <span className="hidden lg:inline">Excel-выписка</span>
              </Button>
            )}
            {canCreate && (
              <Button size="sm" aria-label="Новый заказ" onClick={openNewOrder}>
                <Plus className="size-4" /> <span className="hidden sm:inline">Новый заказ</span>
              </Button>
            )}
          </div>
        ) : undefined
      }
    >
      {view === "archive" ? (
        <ArchiveView
          showDept={showDept}
          trashed={trashed}
          loading={trashLoading}
          error={trashError}
          reload={reloadTrash}
          onBack={() => setView("orders")}
        />
      ) : (
        <>
          {canReviewOrders && (
            <Tabs
              className="mb-4"
              label="Заказы и заявки"
              tabs={[
                { key: "orders", label: "Все заказы" },
                ...(canReviewOrders
                  ? [{ key: "requests", label: "Заявки", count: requests.loading ? undefined : requests.count }]
                  : []),
              ]}
              active={activeTab}
              onChange={chooseTab}
            />
          )}
          {activeTab === "requests" ? (
            <OrderRequestsSection requests={requests} departments={departments ?? undefined} />
          ) : (
            <>
              <Modal
                open={analyticsOpen}
                onClose={() => setAnalyticsOpen(false)}
                title="Аналитика заказов"
                className="max-w-5xl"
                mobileFullscreen
              >
                <OrdersAnalytics
                  rows={departmentSummary ?? []}
                  active={dept}
                  onSelect={(code) => {
                    setDept(code);
                    // Выбрал отдел — сразу к его заказам; сброс выбора оставляет окно открытым.
                    if (code !== "all") setAnalyticsOpen(false);
                  }}
                  overviewUrl={`/orders/list-summary/${listFilters ? `?${listFilters}` : ""}`}
                  scopeName={assigned?.name}
                />
              </Modal>

              <div className="mb-4 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                <SearchInput
                  wrapperClassName="max-w-md flex-1"
                  placeholder="Поиск по клиенту, номеру или #ID"
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                />
                <div className="flex flex-wrap items-center gap-2">
                  <DateRangeFilter dateFrom={dateFrom} dateTo={dateTo} onDateFrom={setDateFrom} onDateTo={setDateTo} />
                  {(departments?.length ?? 0) > 0 && !assigned && (
                    <FilterDropdown
                      label="Отдел"
                      active={dept}
                      onChange={setDept}
                      options={[
                        { key: "all", label: "Все" },
                        { key: "__unassigned", label: "Нет отдела" },
                        ...(departments ?? []).map((department) => ({
                          key: department.code,
                          label: department.name,
                        })),
                      ]}
                    />
                  )}
                  <FilterDropdown label="Статус" options={pills} active={status} onChange={setStatus} />
                  <Button size="sm" variant="outline" onClick={() => setAnalyticsOpen(true)}>
                    <BarChart3 className="size-4" /> Аналитика
                  </Button>
                </div>
              </div>

              {error && (
                <div className="mb-4">
                  <ErrorAlert message={error} onRetry={reload} />
                </div>
              )}
              {statusChange.error && (
                <div className="mb-4">
                  <ErrorAlert message={statusChange.error} />
                </div>
              )}

              {/* Мобильные карточки: таблица на телефоне нечитаемая. */}
              <div className="flex flex-col gap-3 md:hidden">
                {loading ? (
                  <p className="py-6 text-center text-sm text-[var(--muted-foreground)]">Загрузка…</p>
                ) : orders.length === 0 ? (
                  <p className="py-6 text-center text-sm text-[var(--muted-foreground)]">Заказов пока нет.</p>
                ) : (
                  orders.map((o) => (
                    <ActionCard
                      key={o.id}
                      primaryAction={{
                        href: `/orders/${o.id}`,
                        label: `Открыть заказ #${o.id}`,
                      }}
                      className="flex cursor-pointer flex-col gap-2.5 rounded-xl border bg-[var(--card)] p-4 shadow-card"
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-semibold">#{o.id}</span>
                          {showDept && <OrderListDepartmentBadge order={o} />}
                        </div>
                        <div className="relative z-10">{statusCell(o)}</div>
                      </div>
                      <div className="text-sm font-medium">{clientLabel(o)}</div>
                      <div className="text-xs text-[var(--muted-foreground)]">
                        Создан {formatDateTime(o.created_at)}
                      </div>
                      <div className="grid grid-cols-2 gap-2 text-sm">
                        <div>
                          <div className="text-[11px] text-[var(--muted-foreground)]">Сумма</div>
                          <div className="font-semibold tabular-nums">{formatCurrency(o.total_amount, o.currency)}</div>
                        </div>
                        <div>
                          <div className="text-[11px] text-[var(--muted-foreground)]">Оплачено</div>
                          <div className="tabular-nums">{formatCurrency(o.paid_total, o.currency)}</div>
                        </div>
                        {orderTransportText(o) && (
                          <div>
                            <div className="text-[11px] text-[var(--muted-foreground)]">
                              {o.transport_type !== "train" ? "Машина" : o.wagons?.length ? "Вагоны" : "Вагон"}
                            </div>
                            <div className="tabular-nums">{orderTransportText(o)}</div>
                          </div>
                        )}
                        {o.arrival_date && (
                          <div>
                            <div className="text-[11px] text-[var(--muted-foreground)]">Прибытие</div>
                            <div>{formatIsoDate(o.arrival_date)}</div>
                          </div>
                        )}
                      </div>
                      <div className="flex items-center justify-between border-t pt-2">
                        {paymentBadgeStatus(o) ? <OrderPaymentBadge order={o} /> : <span />}
                        {hasActions && (
                          <div className="relative z-10">
                            <ActionMenu items={orderActions.items(o)} />
                          </div>
                        )}
                      </div>
                    </ActionCard>
                  ))
                )}
              </div>

              <Card className="hidden md:block">
                <CardContent className="pt-6">
                  {loading ? (
                    <p className="py-6 text-center text-sm text-[var(--muted-foreground)]">Загрузка…</p>
                  ) : (
                    <Table>
                      <THead>
                        <TR>
                          <SortableHeader
                            label="№"
                            sortKey="id"
                            activeKey={sortKey}
                            dir={sortDir}
                            onClick={toggleSort}
                          />
                          <SortableHeader
                            label="Создан"
                            sortKey="created"
                            activeKey={sortKey}
                            dir={sortDir}
                            onClick={toggleSort}
                          />
                          {showDept && <TH>Отдел</TH>}
                          <SortableHeader
                            label="Клиент"
                            sortKey="client"
                            activeKey={sortKey}
                            dir={sortDir}
                            onClick={toggleSort}
                          />
                          <SortableHeader
                            label="Сумма"
                            sortKey="amount"
                            activeKey={sortKey}
                            dir={sortDir}
                            onClick={toggleSort}
                            align="right"
                          />
                          <SortableHeader
                            label="Статус"
                            sortKey="status"
                            activeKey={sortKey}
                            dir={sortDir}
                            onClick={toggleSort}
                          />
                          {hasActions && <TH></TH>}
                        </TR>
                      </THead>
                      <TBody>
                        {orders.map((o) => (
                          <TR key={o.id} className="cursor-pointer" onClick={() => router.push(`/orders/${o.id}`)}>
                            <TD className="font-medium">
                              <Link
                                href={`/orders/${o.id}`}
                                className="hover:underline"
                                onClick={(e) => e.stopPropagation()}
                              >
                                #{o.id}
                              </Link>
                            </TD>
                            <TD className="whitespace-nowrap tabular-nums text-[var(--muted-foreground)]">
                              {formatDateTime(o.created_at)}
                            </TD>
                            {showDept && (
                              <TD>
                                <OrderListDepartmentBadge order={o} />
                              </TD>
                            )}
                            <TD>{clientLabel(o)}</TD>
                            <TD className="text-right tabular-nums">{formatCurrency(o.total_amount, o.currency)}</TD>
                            <TD>
                              <div className="flex flex-wrap items-center gap-1.5">
                                {statusCell(o)}
                                <OrderPaymentBadge order={o} />
                              </div>
                            </TD>
                            {hasActions && (
                              <TD onClick={(e) => e.stopPropagation()}>
                                <div className="flex justify-end">
                                  <ActionMenu items={orderActions.items(o)} />
                                </div>
                              </TD>
                            )}
                          </TR>
                        ))}
                        {orders.length === 0 && (
                          <EmptyRow colSpan={(showDept ? 6 : 5) + (hasActions ? 1 : 0)}>Заказов пока нет.</EmptyRow>
                        )}
                      </TBody>
                    </Table>
                  )}
                </CardContent>
              </Card>
              <LoadMore
                shown={orders.length}
                total={paged.count}
                hasMore={paged.hasMore}
                loading={paged.loading || paged.loadingMore}
                onClick={paged.loadMore}
              />
            </>
          )}
        </>
      )}

      {canEdit && view === "orders" && activeTab === "orders" && (
        <ArchiveDock
          trashed={trashPreview?.results ?? []}
          count={trashPreview?.count ?? 0}
          onOpenArchive={() => setView("archive")}
          onChanged={() => {
            reload();
            reloadTrashPreview();
          }}
        />
      )}

      {statusChange.dialogs}
      {orderActions.dialogs}

      <Modal
        open={open}
        onClose={closeNewOrder}
        eyebrow="Работа · Заказ"
        title={templateOrder ? `Новый заказ по шаблону #${templateOrder.id}` : "Новый заказ"}
        description="Создайте с нуля или подставьте старый заказ, проверьте данные и только потом сохраните."
        className="max-w-5xl"
        mobileFullscreen
        dismissible={false}
      >
        {open && (
          <div className="space-y-4">
            {requestedTemplateError && (
              <ErrorAlert message={requestedTemplateError} onRetry={reloadRequestedTemplate} />
            )}
            <OrderTemplatePicker
              orders={templateOrders}
              selected={templateOrder}
              onSelect={setTemplateOrder}
              draftSaved={draftSaved}
              onClear={clearNewOrder}
            />
            <OrderForm
              key={`${templateOrder?.id ?? "blank"}-${orderFormResetKey}`}
              template={templateOrder}
              onDraftChange={setDraftSaved}
              onCancel={closeNewOrder}
              onDone={() => {
                closeNewOrder();
                reload();
              }}
            />
          </div>
        )}
      </Modal>

      <StatementExportModal
        open={statementOpen}
        onClose={() => setStatementOpen(false)}
        endpoint="/clients/statement/"
        filenameStem="orders-clients-full-statement"
        title="Общая выписка по заказам"
        description="Подробная выписка в Excel или PDF по всем клиентам, заказам, позициям, продажам, платежам и задолженности."
        scopeLabel="Все клиенты и все заказы"
        sections={ALL_CLIENTS_STATEMENT_SECTIONS}
        initialFrom={dateFrom}
        initialTo={dateTo}
      />
    </AppShell>
  );
}

/* ── Архив: удалённые заказы с восстановлением ──────────────────────────── */
function ArchiveView({
  showDept,
  trashed,
  loading,
  error,
  reload,
  onBack,
}: {
  showDept: boolean;
  trashed: Order[] | null | undefined;
  loading: boolean;
  error: string;
  reload: () => Promise<unknown>;
  onBack: () => void;
}) {
  const { busyId, error: actErr, restore, purge, purgeDialog } = useOrderArchiveActions(reload);
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
      {actErr && (
        <div className="mb-4">
          <ErrorAlert message={actErr} />
        </div>
      )}
      {error && !trashed && (
        <div className="mb-4">
          <ErrorAlert message={error} onRetry={reload} />
        </div>
      )}
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
                <EmptyRow colSpan={showDept ? 6 : 5}>Загрузка…</EmptyRow>
              ) : list.length === 0 ? (
                <EmptyRow colSpan={showDept ? 6 : 5}>В архиве пока нет заказов.</EmptyRow>
              ) : (
                list.map((o) => (
                  <TR key={o.id}>
                    <TD className="font-medium">#{o.id}</TD>
                    {showDept && (
                      <TD>
                        <OrderListDepartmentBadge order={o} />
                      </TD>
                    )}
                    <TD>{clientLabel(o)}</TD>
                    <TD className="text-right tabular-nums">{formatCurrency(o.total_amount, o.currency)}</TD>
                    <TD className="whitespace-nowrap text-[var(--muted-foreground)]">
                      <div className="text-sm">{o.deleted_at ? formatDateTime(o.deleted_at) : "—"}</div>
                      {o.deleted_by_name && <div className="text-xs">{o.deleted_by_name}</div>}
                    </TD>
                    <TD>
                      <div className="flex items-center justify-end gap-1.5">
                        <Button size="sm" variant="outline" disabled={busyId === o.id} onClick={() => void restore(o)}>
                          <RotateCcw className="size-3.5" /> Восстановить
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={busyId === o.id}
                          className="text-[var(--muted-foreground)] hover:text-[var(--destructive)]"
                          title="Удалить из архива"
                          onClick={() => purge(o)}
                        >
                          <Trash2 className="size-4" />
                        </Button>
                      </div>
                    </TD>
                  </TR>
                ))
              )}
            </TBody>
          </Table>
        </CardContent>
      </Card>

      {purgeDialog}
    </>
  );
}

export default function OrdersPage() {
  return (
    <RequirePerm perm="orders.view" title="Заказы">
      <OrdersPageInner />
    </RequirePerm>
  );
}
