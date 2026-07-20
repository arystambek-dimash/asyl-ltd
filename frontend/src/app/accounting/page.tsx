"use client";
import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { SummaryCard } from "@/components/ui/summary-card";
import { FilterDropdown } from "@/components/ui/filter-dropdown";
import { Tabs, type TabDef } from "@/components/ui/tabs";
import { ErrorAlert } from "@/components/ui/data-state";
import { PaymentStageBadge } from "@/components/payment-chain";
import { can } from "@/lib/can";
import { useAuth } from "@/store/auth";
import { useApi } from "@/lib/use-api";
import { api, apiError } from "@/lib/api";
import { formatCurrency, formatMoney, todayLocalIsoDate } from "@/lib/utils";
import { CASHIER_PAYMENT_METHOD_LABELS } from "@/lib/constants";
import { ArrowUpRight, RefreshCw, Search, SlidersHorizontal, X } from "lucide-react";
import type { CashierLogItem, ClientDebt, Department, Order, PaymentQueueItem, Store } from "@/lib/types";

const money = formatCurrency;

interface CashFilters {
  dateFrom: string;
  dateTo: string;
  department: string;
  store: string;
  remainingMin: string;
  remainingMax: string;
}

function apiUrl(path: string, params: Record<string, string>) {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value && value !== "all") query.set(key, value);
  });
  const suffix = query.toString();
  return suffix ? `${path}?${suffix}` : path;
}

function filtersAreValid(filters: CashFilters) {
  const datesOk = !filters.dateFrom || !filters.dateTo || filters.dateFrom <= filters.dateTo;
  const min = filters.remainingMin === "" ? null : Number(filters.remainingMin);
  const max = filters.remainingMax === "" ? null : Number(filters.remainingMax);
  const remainingOk = min === null || max === null || min <= max;
  return datesOk && remainingOk;
}

interface ReportSummary {
  income: { total: string; cash: string; cashless: string; payments: number };
  debt_now: { total: string; orders: number };
}

function DepartmentBadge({ name, color }: { name?: string; color?: string }) {
  if (!name) return null;
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-semibold">
      <span className="size-2 rounded-full" style={{ backgroundColor: color ?? "#64748B" }} />
      {name}
    </span>
  );
}

function debtPaymentState(row: ClientDebt) {
  if (row.partial_count > 0 && row.unpaid_count > 0) {
    return { label: "Есть частичные", tone: "warning" as const };
  }
  if (row.partial_count > 0) {
    return { label: "Частично оплачен", tone: "warning" as const };
  }
  return { label: "Не оплачен", tone: "destructive" as const };
}

/* ── Очередь кассира: данные и действия, общие для вкладок ─────────────── */
function useCashierQueue(enabled: boolean, filters: CashFilters) {
  const valid = filtersAreValid(filters);
  const active = enabled && valid;
  const commonParams = {
    date_from: filters.dateFrom,
    date_to: filters.dateTo,
    department: filters.department,
    store: filters.store,
  };
  // Кассе нужны заявки на подтверждение и оплаты — отбор отдела общий.
  const { data: pending, error: loadError, reload: reloadPending } =
    useApi<Order[]>(active ? apiUrl("/orders/", { ...commonParams, status: "pending" }) : null);
  const { data: queue, reload: reloadQueue } =
    useApi<PaymentQueueItem[]>(active
      ? apiUrl("/orders/payments-queue/", commonParams) : null);
  const { data: cashierLog, reload: reloadCashierLog } =
    useApi<CashierLogItem[]>(active
      ? apiUrl("/orders/cashier-log/", commonParams) : null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function act(fn: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await fn(); reloadPending(); reloadQueue(); reloadCashierLog(); }
    catch (e) { setError(apiError(e)); }
    finally { setBusy(false); }
  }

  return {
    pendingOrders: pending ?? [],
    pendingLoaded: pending !== null,
    toReview: queue ?? [],
    log: cashierLog ?? [],
    busy, error, loadError, reloadPending,
    confirmOrder: (o: Order) =>
      act(() => api.post(`/orders/${o.id}/confirm/`, {})),
    confirmPayment: (p: PaymentQueueItem) =>
      act(() => api.post(`/orders/${p.order}/payments/${p.id}/confirm/`)),
    receivePayment: (p: PaymentQueueItem) =>
      act(() => api.post(`/orders/${p.order}/payments/${p.id}/receive/`)),
    rejectPayment: (p: PaymentQueueItem) =>
      act(() => api.post(`/orders/${p.order}/payments/${p.id}/reject/`)),
    reopenPayment: (event: CashierLogItem) => {
      const paymentId = event.payload.payment_id;
      if (!paymentId) return;
      act(() => api.post(`/orders/${event.order}/payments/${paymentId}/reopen/`));
    },
  };
}

type CashierQueue = ReturnType<typeof useCashierQueue>;

function ActionError({ message }: { message: string }) {
  if (!message) return null;
  return (
    <p className="rounded-lg border bg-[var(--card)] p-3 text-sm text-[var(--destructive)] shadow-card">
      {message}
    </p>
  );
}

/* ── Вкладка «Подтверждение»: заявки и оплаты по всем динамическим отделам ── */
function ConfirmQueueSection({ q }: { q: CashierQueue }) {
  const router = useRouter();
  return (
    <section className="flex flex-col gap-4">
      <ActionError message={q.error} />
      {q.loadError && !q.pendingLoaded && <ErrorAlert message={q.loadError} onRetry={q.reloadPending} />}

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>Заявки на подтверждение</CardTitle></CardHeader>
          <CardContent className="flex flex-col gap-3">
            {q.pendingOrders.length === 0 && (
              <p className="text-sm text-[var(--muted-foreground)]">Нет заявок, ожидающих подтверждения.</p>
            )}
            {q.pendingOrders.map((o) => {
              const priced = o.items.every((it) => it.unit_price != null);
              return (
                <div key={o.id} className="flex flex-col gap-2 rounded-lg border p-3">
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <Link href={`/orders/${o.id}`} className="text-sm font-semibold hover:underline">
                        Заказ #{o.id}
                      </Link>
                      <div className="text-xs text-[var(--muted-foreground)]">
                        {o.client_name} · {formatMoney(o.total_amount)} ₸
                      </div>
                    </div>
                    <DepartmentBadge name={o.department_name} color={o.department_color} />
                  </div>
                  {priced ? (
                    <Button size="sm" disabled={q.busy} onClick={() => q.confirmOrder(o)}>
                      Подтвердить заказ
                    </Button>
                  ) : (
                    <Button size="sm" variant="outline"
                      onClick={() => router.push(`/orders/${o.id}`)}>
                      Указать цены и подтвердить
                    </Button>
                  )}
                </div>
              );
            })}
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>Оплаты к подтверждению</CardTitle></CardHeader>
          <CardContent className="flex flex-col gap-3">
            {q.toReview.length === 0 && (
              <p className="text-sm text-[var(--muted-foreground)]">Нет оплат, ожидающих подтверждения.</p>
            )}
            {q.toReview.map((p) => (
              <div key={p.id} className="flex flex-col gap-2 rounded-lg border p-3">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <div className="text-base font-semibold tabular-nums">{formatMoney(p.amount)} ₸</div>
                    <div className="text-xs text-[var(--muted-foreground)]">
                      <Link href={`/orders/${p.order}`} className="hover:underline">Заказ #{p.order}</Link>
                      {" · "}{p.client_name} · {CASHIER_PAYMENT_METHOD_LABELS[p.method] ?? p.method_label}
                      {p.store_name ? ` · ${p.store_name}` : ""}
                      {p.received_by_name ? ` · принял ${p.received_by_name}` : ""}
                    </div>
                  </div>
                  <div className="flex flex-col items-end gap-1">
                    <DepartmentBadge name={p.department_name} color={p.department_color} />
                    <PaymentStageBadge status={p.status} />
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <Button size="sm" disabled={q.busy}
                    onClick={() => p.status === "requested" ? q.receivePayment(p) : q.confirmPayment(p)}>
                    {p.status === "requested" ? "Оплата поступила" : "Подтвердить получение"}
                  </Button>
                  <Button size="sm" variant="ghost" disabled={q.busy} onClick={() => q.rejectPayment(p)}>
                    Отклонить
                  </Button>
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      </div>
    </section>
  );
}

/* ── Вкладка «Журнал»: действия по оплатам ─────────────────────────────── */
function PaymentJournalSection({ q }: { q: CashierQueue }) {
  return (
    <section className="flex flex-col gap-4">
      <ActionError message={q.error} />
      <Card>
        <CardHeader><CardTitle>Журнал действий по оплатам</CardTitle></CardHeader>
        <CardContent className="flex flex-col gap-2">
          {q.log.length === 0 ? (
            <p className="text-sm text-[var(--muted-foreground)]">Действий по оплатам пока нет.</p>
          ) : q.log.map((event) => (
            <div key={event.id} className="flex flex-col gap-2 rounded-lg border p-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <div className="text-sm font-medium">{event.message}</div>
                <div className="text-xs text-[var(--muted-foreground)]">
                  {new Date(event.created_at).toLocaleString("ru-RU")}
                  {` · заказ #${event.order}`}
                  {event.client_name ? ` · ${event.client_name}` : ""}
                  {event.user_name ? ` · ${event.user_name}` : ""}
                </div>
              </div>
              {event.can_reopen && (
                <Button size="sm" variant="outline" disabled={q.busy}
                  onClick={() => q.reopenPayment(event)}>
                  Вернуть на подтверждение
                </Button>
              )}
            </div>
          ))}
        </CardContent>
      </Card>
    </section>
  );
}

/* ── Долги клиентов ─────────────────────────────────────────────────────── */
function DebtsSection({ rows, loading, error, reload }: {
  rows: ClientDebt[]; loading: boolean; error: string; reload: () => void;
}) {
  const [q, setQ] = useState("");
  const [checkMsg, setCheckMsg] = useState("");
  const [busy, setBusy] = useState(false);

  const filtered = rows.filter((row) =>
    !q || `${row.client_name} ${row.client_phone}`.toLowerCase().includes(q.toLowerCase())
  );

  async function checkOverdue() {
    setBusy(true); setCheckMsg("");
    try {
      const r = await api.post<{ checked: number; overdue_notifications: number }>("/stores/check-overdue/");
      setCheckMsg(`Проверено магазинов: ${r.data.checked}. Просрочек: ${r.data.overdue_notifications}.`);
      reload();
    } catch (e) {
      setCheckMsg(apiError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">Долги клиентов</h2>
          <p className="text-sm text-[var(--muted-foreground)]">
            Общий остаток по клиенту. Заказы открываются внутри клиента.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="relative w-full sm:w-72">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
            <Input className="pl-9" placeholder="Поиск по клиенту или телефону"
              value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <Button size="sm" variant="outline" disabled={busy} onClick={checkOverdue} aria-label="Проверить просрочки">
            <RefreshCw className={"size-4" + (busy ? " animate-spin" : "")} />
            <span className="hidden sm:inline">Проверить просрочки</span>
          </Button>
        </div>
      </div>

      {checkMsg && (
        <p className="rounded-lg border bg-[var(--card)] px-4 py-2 text-sm text-[var(--muted-foreground)] shadow-card">
          {checkMsg}
        </p>
      )}

      <Card>
        <CardContent className="pt-6">
          <Table>
            <THead>
              <TR>
                <TH>Клиент</TH>
                <TH>Остаток</TH>
                <TH>Заказы</TH>
                <TH>Статус оплаты</TH>
                <TH>Магазины</TH>
                <TH>Просрочки</TH>
                <TH></TH>
              </TR>
            </THead>
            <TBody>
              {loading ? (
                <TR><TD colSpan={7} className="py-8 text-center text-[var(--muted-foreground)]">Загрузка…</TD></TR>
              ) : error && rows.length === 0 ? (
                <TR><TD colSpan={7} className="py-4"><ErrorAlert message={error} onRetry={reload} /></TD></TR>
              ) : filtered.length === 0 ? (
                <TR><TD colSpan={7} className="py-8 text-center text-[var(--muted-foreground)]">Долгов нет.</TD></TR>
              ) : filtered.map((row) => {
                const state = debtPaymentState(row);
                return (
                  <TR key={row.client_id}>
                    <TD>
                      <div className="font-medium">{row.client_name || "—"}</div>
                      <div className="text-xs text-[var(--muted-foreground)]">{row.client_phone || "—"}</div>
                    </TD>
                    <TD className="tabular-nums text-lg font-semibold text-[var(--destructive)]">
                      {formatMoney(row.debt_total)} ₸
                    </TD>
                    <TD className="tabular-nums">{row.orders_count}</TD>
                    <TD>
                      <Badge tone={state.tone} dot>{state.label}</Badge>
                    </TD>
                    <TD>
                      {row.stores_count > 0 ? (
                        <Badge tone="muted">{row.stores_count}</Badge>
                      ) : (
                        <span className="text-[var(--muted-foreground)]">—</span>
                      )}
                    </TD>
                    <TD>
                      {row.overdue_count > 0 ? (
                        <Badge tone="destructive" dot>{row.overdue_count}</Badge>
                      ) : (
                        <span className="text-[var(--muted-foreground)]">0</span>
                      )}
                    </TD>
                    <TD>
                      <div className="flex justify-end">
                        <Link href={`/accounting/debts/clients/${row.client_id}`}>
                          <Button size="sm" variant="ghost">
                            Детали
                            <ArrowUpRight className="size-4" />
                          </Button>
                        </Link>
                      </div>
                    </TD>
                  </TR>
                );
              })}
            </TBody>
          </Table>
        </CardContent>
      </Card>
    </section>
  );
}

function CashFiltersPanel({ filters, stores, departments, onChange, onReset }: {
  filters: CashFilters;
  stores: Store[];
  departments: Department[];
  onChange: (patch: Partial<CashFilters>) => void;
  onReset: () => void;
}) {
  const activeCount = [
    filters.dateFrom !== "" || filters.dateTo !== "",
    filters.department !== "all",
    filters.store !== "all",
    filters.remainingMin !== "" || filters.remainingMax !== "",
  ].filter(Boolean).length;
  const datesInvalid = Boolean(filters.dateFrom && filters.dateTo && filters.dateFrom > filters.dateTo);
  const remainingInvalid = Boolean(
    filters.remainingMin && filters.remainingMax
    && Number(filters.remainingMin) > Number(filters.remainingMax));

  return (
    <Card>
      <CardContent className="p-4">
        <div className="flex flex-col gap-4">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <span className="flex size-8 items-center justify-center rounded-lg bg-[var(--muted)] text-[var(--muted-foreground)]">
                <SlidersHorizontal className="size-4" />
              </span>
              <div>
                <div className="text-sm font-semibold">Фильтры кассы</div>
                <div className="text-xs text-[var(--muted-foreground)]">
                  {activeCount ? `Применено: ${activeCount}` : "Без ограничений · все оплаты"}
                </div>
              </div>
            </div>
            {activeCount > 0 && (
              <Button size="sm" variant="ghost" onClick={onReset}>
                <X className="size-4" /> Сбросить
              </Button>
            )}
          </div>

          <div className="flex flex-wrap items-end gap-3">
            <label className="flex flex-col gap-1.5">
              <span className="text-[11px] font-medium text-[var(--muted-foreground)]">С даты</span>
              <Input type="date" value={filters.dateFrom}
                onChange={(e) => onChange({ dateFrom: e.target.value })}
                className="h-9 w-[158px]" />
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-[11px] font-medium text-[var(--muted-foreground)]">По дату</span>
              <Input type="date" value={filters.dateTo}
                onChange={(e) => onChange({ dateTo: e.target.value })}
                className="h-9 w-[158px]" />
            </label>
            <FilterDropdown label="Отдел" active={filters.department}
              onChange={(department) => onChange({ department })}
              options={[
                { key: "all", label: "Все" },
                ...departments.map((department) => ({
                  key: department.code,
                  label: department.name,
                })),
              ]} />
            <FilterDropdown label="Магазин" active={filters.store}
              onChange={(store) => onChange({ store })}
              options={[
                { key: "all", label: "Все" },
                ...[...stores]
                  .sort((a, b) => a.name.localeCompare(b.name, "ru"))
                  .map((store) => ({ key: String(store.id), label: store.name })),
              ]} />
            <div className="flex flex-col gap-1.5">
              <span className="text-[11px] font-medium text-[var(--muted-foreground)]">Остаток долга, ₸</span>
              <div className="flex items-center gap-1.5">
                <Input type="number" min="0" inputMode="decimal" placeholder="От"
                  value={filters.remainingMin}
                  onChange={(e) => onChange({ remainingMin: e.target.value })}
                  className="h-9 w-[118px]" />
                <span className="text-[var(--muted-foreground)]">—</span>
                <Input type="number" min="0" inputMode="decimal" placeholder="До"
                  value={filters.remainingMax}
                  onChange={(e) => onChange({ remainingMax: e.target.value })}
                  className="h-9 w-[118px]" />
              </div>
            </div>
          </div>

          {(datesInvalid || remainingInvalid) && (
            <p className="text-xs font-medium text-[var(--destructive)]">
              {datesInvalid
                ? "Дата начала не может быть позже даты окончания."
                : "Минимальный остаток не может быть больше максимального."}
            </p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

type CashTab = "overview" | "confirm" | "journal";

function CashierInner() {
  const { me } = useAuth();
  const canPayments = can(me, "payments.confirm");
  const canReports = can(me, "reports.view");

  const [filters, setFilters] = useState<CashFilters>({
    dateFrom: "",
    dateTo: "",
    department: "all",
    store: "all",
    remainingMin: "",
    remainingMax: "",
  });
  const [tab, setTab] = useState<CashTab>(canReports ? "overview" : "confirm");
  const validFilters = filtersAreValid(filters);
  const commonParams = {
    date_from: filters.dateFrom,
    date_to: filters.dateTo,
    department: filters.department,
    store: filters.store,
  };
  const reportUrl = apiUrl("/reports/summary/", {
    from: filters.dateFrom,
    to: filters.dateTo,
    department: filters.department,
    store: filters.store,
  });
  const debtsUrl = apiUrl("/clients/debts/", {
    ...commonParams,
    remaining_min: filters.remainingMin,
    remaining_max: filters.remainingMax,
  });

  // Кассовая аналитика — тот же серверный отчёт, что и на «Отчётах».
  const { data: summary } = useApi<ReportSummary>(
    canReports && validFilters ? reportUrl : null);
  const queue = useCashierQueue(canPayments, filters);
  const { data: debts, loading: debtsLoading, error: debtsError, reload: reloadDebts } =
    useApi<ClientDebt[]>(canReports && validFilters ? debtsUrl : null);
  const { data: stores } = useApi<Store[]>(canReports ? "/stores/" : null);
  const { data: departments } = useApi<Department[]>("/departments/");

  const toReviewSum = queue.toReview.reduce((s, p) => s + Number(p.amount), 0);
  const toReviewCash = queue.toReview.filter((p) => p.method === "cash")
    .reduce((s, p) => s + Number(p.amount), 0);
  const debtRows = validFilters ? debts ?? [] : [];
  const debtTotal = debtRows.reduce((sum, row) => sum + Number(row.debt_total), 0);
  const overdueClients = debtRows.filter((r) => r.overdue_count > 0).length;
  const today = todayLocalIsoDate();
  const isToday = filters.dateFrom === today && filters.dateTo === today;
  const hasDates = Boolean(filters.dateFrom || filters.dateTo);

  const tabs: TabDef[] = [
    ...(canReports ? [{ key: "overview", label: "Общее" }] : []),
    ...(canPayments ? [
      { key: "confirm", label: "Подтверждение", count: queue.pendingOrders.length + queue.toReview.length },
      { key: "journal", label: "Журнал" },
    ] : []),
  ];

  function resetFilters() {
    setFilters({
      dateFrom: "",
      dateTo: "",
      department: "all",
      store: "all",
      remainingMin: "",
      remainingMax: "",
    });
  }

  return (
    <AppShell title="Касса" section="Работа"
      description="Поступления, очередь подтверждений и долги в одном месте.">
      <div className="flex flex-col gap-6">
        <Tabs tabs={tabs} active={tab} onChange={(key) => setTab(key as CashTab)} />

        <CashFiltersPanel filters={filters} stores={stores ?? []} departments={departments ?? []}
          onChange={(patch) => setFilters((current) => ({ ...current, ...patch }))}
          onReset={resetFilters} />

        {tab === "overview" && canReports && (
          <>
            <section className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
              <SummaryCard title={isToday ? "Поступило сегодня" : hasDates ? "Поступило за период" : "Поступило за всё время"} tone="success"
                value={money(summary?.income.total ?? 0)}
                rows={[
                  { label: "Наличные", value: money(summary?.income.cash ?? 0) },
                  { label: "Безналичные", value: money(summary?.income.cashless ?? 0) },
                ]} />
              {canPayments && (
                <SummaryCard title="Ожидает подтверждения" tone="primary"
                  value={money(toReviewSum)}
                  rows={[
                    { label: "Оплат в очереди", value: String(queue.toReview.length) },
                    { label: "Из них наличными", value: money(toReviewCash) },
                  ]} />
              )}
              <SummaryCard title="Дебиторка" tone="destructive"
                value={money(debtTotal)}
                rows={[
                  { label: "Клиентов с долгом", value: String(debtRows.length) },
                  { label: "С просрочкой", value: String(overdueClients) },
                ]} />
            </section>

            <DebtsSection rows={debtRows} loading={debtsLoading}
              error={debtsError} reload={reloadDebts} />
          </>
        )}

        {tab === "confirm" && canPayments && <ConfirmQueueSection q={queue} />}

        {tab === "journal" && canPayments && <PaymentJournalSection q={queue} />}
      </div>
    </AppShell>
  );
}

export default function CashierPage() {
  // Доступ, если есть хотя бы одна из секций: очередь или аналитика с долгами.
  return (
    <RequirePerm perm={["payments.confirm", "reports.view"]} title="Касса">
      <CashierInner />
    </RequirePerm>
  );
}
