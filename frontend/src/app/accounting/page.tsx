"use client";
import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { SummaryCard } from "@/components/ui/summary-card";
import { FilterDropdown } from "@/components/ui/filter-dropdown";
import { Tabs, type TabDef } from "@/components/ui/tabs";
import { ErrorAlert } from "@/components/ui/data-state";
import { CurrencyAmounts } from "@/components/ui/currency-amounts";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { PaymentStageBadge } from "@/components/payment-chain";
import { TransactionsSection } from "@/components/transactions-section";
import { can } from "@/lib/can";
import { amountForCurrency, otherCurrencyAmounts, primaryMoneyCurrency } from "@/lib/currency-map";
import { useAuth } from "@/store/auth";
import { useApi } from "@/lib/use-api";
import { usePagedApi } from "@/lib/use-paged-api";
import { LoadMore } from "@/components/ui/load-more";
import { api, apiError } from "@/lib/api";
import { showSuccess } from "@/lib/toast";
import {
  cn,
  formatCurrency,
  formatDateTime,
  sumDebtByCurrency,
  sumMoneyByCurrency,
  todayLocalIsoDate,
} from "@/lib/utils";
import { PAYMENT_METHOD_LABELS } from "@/lib/constants";
import { ArrowUpRight, RefreshCw, Search, SlidersHorizontal, X } from "lucide-react";
import type {
  CashierLogItem,
  ClientDebt,
  Department,
  Order,
  PaymentQueueItem,
  ReportSummary,
  Store,
} from "@/lib/types";

const money = formatCurrency;

interface CashFilters {
  dateFrom: string;
  dateTo: string;
  department: string;
  store: string;
  remainingMin: string;
  remainingMax: string;
  remainingCurrency: string;
}

const EMPTY_CASH_FILTERS: CashFilters = {
  dateFrom: "",
  dateTo: "",
  department: "all",
  store: "all",
  remainingMin: "",
  remainingMax: "",
  remainingCurrency: "all",
};

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
// Журнал живёт на своей вкладке со своими фильтрами и ленивой подгрузкой —
// хук очереди отдаёт только заявки и оплаты, а об изменениях сообщает
// наружу, чтобы журнал перезагрузил себя сам.
function useCashierQueue(
  enabled: boolean,
  canReviewOrders: boolean,
  queueFilters: CashFilters,
  onChanged?: () => void,
) {
  const queueActive = enabled && filtersAreValid(queueFilters);
  const queueParams = {
    date_from: queueFilters.dateFrom,
    date_to: queueFilters.dateTo,
    department: queueFilters.department,
    store: queueFilters.store,
  };
  // Кассе нужны заявки на подтверждение и оплаты — отбор отдела общий.
  const {
    data: pending,
    error: pendingError,
    reload: reloadPending,
  } = useApi<Order[]>(
    queueActive && canReviewOrders ? apiUrl("/orders/", { ...queueParams, status: "pending" }) : null,
  );
  const {
    data: queue,
    error: queueError,
    reload: reloadQueue,
  } = useApi<PaymentQueueItem[]>(queueActive ? apiUrl("/orders/payments-queue/", queueParams) : null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const loadError = pendingError || queueError;

  function reloadAll() {
    void reloadPending();
    void reloadQueue();
    onChanged?.();
  }

  async function act(fn: () => Promise<unknown>, done?: string) {
    setBusy(true);
    setError("");
    try {
      await fn();
      reloadAll();
      // Без подтверждения удачное действие выглядит как «ничего не произошло»,
      // и кассир жмёт кнопку второй раз.
      if (done) showSuccess(done);
    } catch (e) {
      setError(apiError(e));
    } finally {
      setBusy(false);
    }
  }

  return {
    pendingOrders: pending ?? [],
    toReview: queue ?? [],
    busy,
    error,
    loadError,
    reload: reloadAll,
    confirmOrder: (o: Order) => act(() => api.post(`/orders/${o.id}/confirm/`, {}), "Заказ подтверждён"),
    confirmPayment: (p: PaymentQueueItem) =>
      act(() => api.post(`/orders/${p.order}/payments/${p.id}/confirm/`), "Оплата подтверждена"),
    receivePayment: (p: PaymentQueueItem) =>
      act(() => api.post(`/orders/${p.order}/payments/${p.id}/receive/`), "Поступление подтверждено"),
    rejectPayment: (p: PaymentQueueItem) =>
      act(() => api.post(`/orders/${p.order}/payments/${p.id}/reject/`), "Оплата отклонена"),
    reopenPayment: (event: CashierLogItem) => {
      const paymentId = event.payload.payment_id;
      if (!paymentId) return;
      act(() => api.post(`/orders/${event.order}/payments/${paymentId}/reopen/`));
    },
    restorePayment: (event: CashierLogItem) => {
      const paymentId = event.payload.payment_id;
      if (!paymentId) return;
      return act(() => api.post(`/orders/${event.order}/payments/${paymentId}/restore/`));
    },
  };
}

type CashierQueue = ReturnType<typeof useCashierQueue>;
type PagedCashierLog = ReturnType<typeof usePagedApi<CashierLogItem>>;

function ActionError({ message }: { message: string }) {
  if (!message) return null;
  return (
    <p className="rounded-lg border bg-[var(--card)] p-3 text-sm text-[var(--destructive)] shadow-card">{message}</p>
  );
}

/* ── Вкладка «Подтверждение»: заявки и оплаты по всем динамическим отделам ── */
function ConfirmQueueSection({
  q,
  canViewOrders,
  canReviewOrders,
  canEditOrders,
  canReceivePayments,
}: {
  q: CashierQueue;
  canViewOrders: boolean;
  canReviewOrders: boolean;
  canEditOrders: boolean;
  canReceivePayments: boolean;
}) {
  const router = useRouter();
  return (
    <section className="flex flex-col gap-4">
      <ActionError message={q.error} />
      {q.loadError && <ErrorAlert message={q.loadError} onRetry={q.reload} />}

      <div className={cn("grid grid-cols-1 gap-6", canReviewOrders && "xl:grid-cols-2")}>
        {canReviewOrders && (
          <Card>
            <CardHeader>
              <CardTitle>Заявки на подтверждение</CardTitle>
            </CardHeader>
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
                          {o.client_name} · {formatCurrency(o.total_amount, o.currency)}
                        </div>
                      </div>
                      <DepartmentBadge name={o.department_name} color={o.department_color} />
                    </div>
                    {priced ? (
                      <Button size="sm" disabled={q.busy} onClick={() => q.confirmOrder(o)}>
                        Подтвердить заказ
                      </Button>
                    ) : canEditOrders ? (
                      <Button size="sm" variant="outline" onClick={() => router.push(`/orders/${o.id}`)}>
                        Указать цены и подтвердить
                      </Button>
                    ) : (
                      <p className="text-xs text-[var(--muted-foreground)]">
                        Сначала сотрудник с правом редактирования должен указать цены.
                      </p>
                    )}
                  </div>
                );
              })}
            </CardContent>
          </Card>
        )}

        <Card>
          <CardHeader>
            <CardTitle>Оплаты к подтверждению</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            {q.toReview.length === 0 && (
              <p className="text-sm text-[var(--muted-foreground)]">Нет оплат, ожидающих подтверждения.</p>
            )}
            {q.toReview.map((p) => (
              <div key={p.id} className="flex flex-col gap-2 rounded-lg border p-3">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <div className="text-base font-semibold tabular-nums">
                      {formatCurrency(p.amount, p.currency ?? "KZT")}
                    </div>
                    <div className="text-xs text-[var(--muted-foreground)]">
                      {canViewOrders ? (
                        <Link href={`/orders/${p.order}`} className="hover:underline">
                          Заказ #{p.order}
                        </Link>
                      ) : (
                        <span>Заказ #{p.order}</span>
                      )}
                      {" · "}
                      {p.client_name} · {PAYMENT_METHOD_LABELS[p.method] ?? p.method_label}
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
                  {p.status !== "requested" || canReceivePayments ? (
                    <Button
                      size="sm"
                      disabled={q.busy}
                      onClick={() => (p.status === "requested" ? q.receivePayment(p) : q.confirmPayment(p))}
                    >
                      {p.status === "requested" ? "Оплата поступила" : "Подтвердить получение"}
                    </Button>
                  ) : (
                    <p className="self-center text-xs text-[var(--muted-foreground)]">
                      Принять может сотрудник с правом подтверждения оплат.
                    </p>
                  )}
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
function PaymentJournalSection({ q, log }: { q: CashierQueue; log: PagedCashierLog }) {
  const [restoreEvent, setRestoreEvent] = useState<CashierLogItem | null>(null);
  return (
    <section className="flex flex-col gap-4">
      <ActionError message={q.error} />
      {log.error && <ErrorAlert message={log.error} onRetry={log.reload} />}
      <Card>
        <CardHeader>
          <CardTitle>Журнал действий по оплатам</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-2">
          {log.items.length === 0 ? (
            <p className="text-sm text-[var(--muted-foreground)]">Действий по оплатам пока нет.</p>
          ) : (
            log.items.map((event) => (
              <div
                key={event.id}
                className="flex flex-col gap-2 rounded-lg border p-3 sm:flex-row sm:items-center sm:justify-between"
              >
                <div>
                  <div className="text-sm font-medium">{event.message}</div>
                  <div className="text-xs text-[var(--muted-foreground)]">
                    {formatDateTime(event.created_at)}
                    {` · заказ #${event.order}`}
                    {event.client_name ? ` · ${event.client_name}` : ""}
                    {event.user_name ? ` · ${event.user_name}` : ""}
                  </div>
                </div>
                {event.can_reopen && (
                  <Button size="sm" variant="outline" disabled={q.busy} onClick={() => q.reopenPayment(event)}>
                    Вернуть на подтверждение
                  </Button>
                )}
                {event.can_restore && (
                  <Button size="sm" variant="outline" disabled={q.busy} onClick={() => setRestoreEvent(event)}>
                    <RefreshCw className="size-3.5" /> Восстановить
                  </Button>
                )}
              </div>
            ))
          )}
          <LoadMore
            shown={log.items.length}
            total={log.count}
            hasMore={log.hasMore}
            loading={log.loadingMore}
            onClick={log.loadMore}
          />
        </CardContent>
      </Card>
      <ConfirmDialog
        open={!!restoreEvent}
        onClose={() => setRestoreEvent(null)}
        title="Восстановить отклонённую оплату?"
        description={
          restoreEvent
            ? `Оплата по заказу #${restoreEvent.order} вернётся в очередь кассы. Само событие отмены останется в журнале.`
            : ""
        }
        confirmLabel="Восстановить"
        confirmVariant="default"
        busy={q.busy}
        error={q.error}
        onConfirm={async () => {
          if (!restoreEvent) return;
          await q.restorePayment(restoreEvent);
          setRestoreEvent(null);
        }}
      />
    </section>
  );
}

/* ── Долги клиентов ─────────────────────────────────────────────────────── */
function DebtsSection({
  rows,
  loading,
  error,
  reload,
  canCheckOverdue,
}: {
  rows: ClientDebt[];
  loading: boolean;
  error: string;
  reload: () => void;
  canCheckOverdue: boolean;
}) {
  const [q, setQ] = useState("");
  const [checkMsg, setCheckMsg] = useState("");
  const [busy, setBusy] = useState(false);
  // Данные уже загружены целиком — лениво рендерим, чтобы длинный список
  // должников не разворачивался простынёй.
  const [limit, setLimit] = useState(25);

  const filtered = rows.filter(
    (row) => !q || `${row.client_name} ${row.client_phone}`.toLowerCase().includes(q.toLowerCase()),
  );
  const visible = filtered.slice(0, limit);

  async function checkOverdue() {
    setBusy(true);
    setCheckMsg("");
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
            <Input
              className="pl-9"
              placeholder="Поиск по клиенту или телефону"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
          </div>
          {canCheckOverdue && (
            <Button size="sm" variant="outline" disabled={busy} onClick={checkOverdue} aria-label="Проверить просрочки">
              <RefreshCw className={"size-4" + (busy ? " animate-spin" : "")} />
              <span className="hidden sm:inline">Проверить просрочки</span>
            </Button>
          )}
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
                <TR>
                  <TD colSpan={7} className="py-8 text-center text-[var(--muted-foreground)]">
                    Загрузка…
                  </TD>
                </TR>
              ) : error && rows.length === 0 ? (
                <TR>
                  <TD colSpan={7} className="py-4">
                    <ErrorAlert message={error} onRetry={reload} />
                  </TD>
                </TR>
              ) : filtered.length === 0 ? (
                <TR>
                  <TD colSpan={7} className="py-8 text-center text-[var(--muted-foreground)]">
                    Долгов нет.
                  </TD>
                </TR>
              ) : (
                visible.map((row) => {
                  const state = debtPaymentState(row);
                  return (
                    <TR key={row.client_id}>
                      <TD>
                        <div className="font-medium">{row.client_name || "—"}</div>
                        <div className="text-xs text-[var(--muted-foreground)]">{row.client_phone || "—"}</div>
                      </TD>
                      <TD className="tabular-nums text-lg font-semibold text-[var(--destructive)]">
                        <CurrencyAmounts
                          byCurrency={row.debt_by_currency}
                          fallbackAmount={row.debt_total}
                          fallbackCurrency={row.debt_currency ?? "KZT"}
                        />
                      </TD>
                      <TD className="tabular-nums">{row.orders_count}</TD>
                      <TD>
                        <Badge tone={state.tone} dot>
                          {state.label}
                        </Badge>
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
                          <Badge tone="destructive" dot>
                            {row.overdue_count}
                          </Badge>
                        ) : (
                          <span className="text-[var(--muted-foreground)]">0</span>
                        )}
                      </TD>
                      <TD>
                        <div className="flex justify-end">
                          <Link
                            href={`/accounting/debts/clients/${row.client_id}`}
                            className={buttonVariants({ size: "sm", variant: "ghost" })}
                          >
                            Детали
                            <ArrowUpRight className="size-4" />
                          </Link>
                        </div>
                      </TD>
                    </TR>
                  );
                })
              )}
            </TBody>
          </Table>
          <LoadMore
            shown={visible.length}
            total={filtered.length}
            hasMore={filtered.length > visible.length}
            onClick={() => setLimit((current) => current + 25)}
          />
        </CardContent>
      </Card>
    </section>
  );
}

function CashFiltersPanel({
  filters,
  stores,
  departments,
  showRemaining,
  onChange,
  onReset,
}: {
  filters: CashFilters;
  stores: Store[];
  departments: Department[];
  /** Диапазон остатка долга уместен только там, где есть долги («Общее»). */
  showRemaining: boolean;
  onChange: (patch: Partial<CashFilters>) => void;
  onReset: () => void;
}) {
  const activeCount = [
    filters.dateFrom !== "" || filters.dateTo !== "",
    filters.department !== "all",
    filters.store !== "all",
    showRemaining && (filters.remainingMin !== "" || filters.remainingMax !== ""),
  ].filter(Boolean).length;
  const datesInvalid = Boolean(filters.dateFrom && filters.dateTo && filters.dateFrom > filters.dateTo);
  const remainingInvalid =
    showRemaining &&
    Boolean(
      filters.remainingMin && filters.remainingMax && Number(filters.remainingMin) > Number(filters.remainingMax),
    );

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
              <Input
                type="date"
                value={filters.dateFrom}
                onChange={(e) => onChange({ dateFrom: e.target.value })}
                className="h-9 w-[158px]"
              />
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-[11px] font-medium text-[var(--muted-foreground)]">По дату</span>
              <Input
                type="date"
                value={filters.dateTo}
                onChange={(e) => onChange({ dateTo: e.target.value })}
                className="h-9 w-[158px]"
              />
            </label>
            <FilterDropdown
              label="Отдел"
              active={filters.department}
              onChange={(department) => onChange({ department })}
              options={[
                { key: "all", label: "Все" },
                ...departments.map((department) => ({
                  key: department.code,
                  label: department.name,
                })),
              ]}
            />
            <FilterDropdown
              label="Магазин"
              active={filters.store}
              onChange={(store) => onChange({ store })}
              options={[
                { key: "all", label: "Все" },
                ...[...stores]
                  .sort((a, b) => a.name.localeCompare(b.name, "ru"))
                  .map((store) => ({ key: String(store.id), label: store.name })),
              ]}
            />
            {showRemaining && (
              <div className="flex flex-col gap-1.5">
                <span className="text-[11px] font-medium text-[var(--muted-foreground)]">Остаток долга</span>
                <div className="flex items-center gap-1.5">
                  <FilterDropdown
                    label="Валюта"
                    active={filters.remainingCurrency}
                    onChange={(remainingCurrency) => onChange({ remainingCurrency })}
                    options={[
                      { key: "all", label: "Основная" },
                      { key: "KZT", label: "KZT" },
                      { key: "USD", label: "USD" },
                    ]}
                  />
                  <Input
                    type="number"
                    min="0"
                    inputMode="decimal"
                    placeholder="От"
                    value={filters.remainingMin}
                    onChange={(e) => onChange({ remainingMin: e.target.value })}
                    className="h-9 w-[118px]"
                  />
                  <span className="text-[var(--muted-foreground)]">—</span>
                  <Input
                    type="number"
                    min="0"
                    inputMode="decimal"
                    placeholder="До"
                    value={filters.remainingMax}
                    onChange={(e) => onChange({ remainingMax: e.target.value })}
                    className="h-9 w-[118px]"
                  />
                </div>
              </div>
            )}
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

type CashTab = "overview" | "confirm" | "journal" | "transactions";
// У «Транзакций» свой поиск, панель фильтров есть у остальных вкладок.
type FilterableCashTab = Exclude<CashTab, "transactions">;

function CashierInner() {
  const { me } = useAuth();
  const canPayments = can(me, "payments.confirm");
  const canCreatePayments = can(me, "payments.create");
  const canReports = can(me, "reports.view");
  const canDebtEntry = canReports || canCreatePayments;
  const canTransactions = can(me, "payments.view");
  const canViewOrders = can(me, "orders.view");
  const canReviewOrders = canViewOrders && can(me, "orders.confirm");
  const canEditOrders = can(me, "orders.edit");
  const canViewClients = can(me, "clients.view");
  const canCheckOverdue = can(me, "clients.edit");

  // Свои фильтры на каждой вкладке: период журнала не должен обрезать
  // «Общее», а остаток долга имеет смысл только там, где есть долги.
  const [filtersByTab, setFiltersByTab] = useState<Record<FilterableCashTab, CashFilters>>({
    overview: EMPTY_CASH_FILTERS,
    confirm: EMPTY_CASH_FILTERS,
    journal: EMPTY_CASH_FILTERS,
  });
  const [tab, setTab] = useState<CashTab>(canDebtEntry ? "overview" : canPayments ? "confirm" : "transactions");
  const filterTab: FilterableCashTab = tab === "transactions" ? "overview" : tab;
  const filters = filtersByTab[filterTab];
  const overviewFilters = filtersByTab.overview;
  const validOverview = filtersAreValid(overviewFilters);
  const reportUrl = apiUrl("/reports/summary/", {
    from: overviewFilters.dateFrom,
    to: overviewFilters.dateTo,
    department: overviewFilters.department,
    store: overviewFilters.store,
  });
  const debtsUrl = apiUrl("/clients/debts/", {
    date_from: overviewFilters.dateFrom,
    date_to: overviewFilters.dateTo,
    department: overviewFilters.department,
    store: overviewFilters.store,
    remaining_min: overviewFilters.remainingMin,
    remaining_max: overviewFilters.remainingMax,
    remaining_currency: overviewFilters.remainingCurrency,
  });

  // Кассовая аналитика — тот же серверный отчёт, что и на «Отчётах».
  const {
    data: summary,
    error: summaryError,
    reload: reloadSummary,
  } = useApi<ReportSummary>(canReports && validOverview ? reportUrl : null);
  const journalFilters = filtersByTab.journal;
  const journalLog = usePagedApi<CashierLogItem>(
    canPayments && filtersAreValid(journalFilters)
      ? apiUrl("/orders/cashier-log/", {
          date_from: journalFilters.dateFrom,
          date_to: journalFilters.dateTo,
          department: journalFilters.department,
          store: journalFilters.store,
        })
      : null,
    50,
  );
  const queue = useCashierQueue(canPayments, canReviewOrders, filtersByTab.confirm, journalLog.reload);
  const {
    data: debts,
    loading: debtsLoading,
    error: debtsError,
    reload: reloadDebts,
  } = useApi<ClientDebt[]>(canDebtEntry && validOverview ? debtsUrl : null);
  const { data: stores } = useApi<Store[]>(canReports && canViewClients ? "/stores/" : null);
  const { data: departments } = useApi<Department[]>("/departments/");

  const toReviewByCurrency = sumMoneyByCurrency(
    queue.toReview,
    (payment) => payment.amount,
    (payment) => payment.currency,
  );
  const toReviewCashByCurrency = sumMoneyByCurrency(
    queue.toReview.filter((payment) => payment.method === "cash"),
    (payment) => payment.amount,
    (payment) => payment.currency,
  );
  const toReviewCurrency = primaryMoneyCurrency(toReviewByCurrency);
  const toReviewSum = toReviewByCurrency[toReviewCurrency] ?? 0;
  const otherReviewCurrencies = Object.entries(toReviewByCurrency).filter(
    ([currency, value]) => currency !== toReviewCurrency && value > 0,
  );
  const debtRows = validOverview ? (debts ?? []) : [];
  // Валюты не складываются: 1000 ₸ и 5 $ не дают «1005». Крупно — основная
  // валюта, остальные отдельной строкой под ней.
  const debtByCurrency = sumDebtByCurrency(debtRows);
  const debtCurrency = primaryMoneyCurrency(debtByCurrency);
  const debtTotal = debtByCurrency[debtCurrency] ?? 0;
  const otherDebtCurrencies = Object.entries(debtByCurrency).filter(
    ([currency, value]) => currency !== debtCurrency && value > 0,
  );
  const overdueClients = debtRows.filter((r) => r.overdue_count > 0).length;
  const today = todayLocalIsoDate();
  const isToday = overviewFilters.dateFrom === today && overviewFilters.dateTo === today;
  const hasDates = Boolean(overviewFilters.dateFrom || overviewFilters.dateTo);
  const incomeByCurrency = summary?.income.by_currency ?? {};
  const incomeCurrency = summary?.income.currency || Object.keys(incomeByCurrency)[0] || "KZT";
  const incomeTotal = amountForCurrency(incomeByCurrency, summary?.income.total ?? "0", incomeCurrency);
  const cashTotal = amountForCurrency(
    summary?.income.cash_by_currency ?? {},
    summary?.income.cash ?? "0",
    incomeCurrency,
  );
  const cashlessTotal = amountForCurrency(
    summary?.income.cashless_by_currency ?? {},
    summary?.income.cashless ?? "0",
    incomeCurrency,
  );
  const otherIncomeCurrencies = otherCurrencyAmounts(incomeByCurrency, incomeCurrency);

  const tabs: TabDef[] = [
    ...(canDebtEntry ? [{ key: "overview", label: canReports ? "Общее" : "Долги" }] : []),
    ...(canPayments
      ? [
          { key: "confirm", label: "Подтверждение", count: queue.pendingOrders.length + queue.toReview.length },
          { key: "journal", label: "Журнал" },
        ]
      : []),
    ...(canTransactions ? [{ key: "transactions", label: "Транзакции" }] : []),
  ];

  function patchFilters(patch: Partial<CashFilters>) {
    setFiltersByTab((current) => ({ ...current, [filterTab]: { ...current[filterTab], ...patch } }));
  }

  function resetFilters() {
    setFiltersByTab((current) => ({ ...current, [filterTab]: EMPTY_CASH_FILTERS }));
  }

  return (
    <AppShell
      title="Касса"
      section="Работа"
      description="Поступления, очередь подтверждений, долги и транзакции в одном месте."
    >
      <div className="flex flex-col gap-6">
        <Tabs tabs={tabs} active={tab} onChange={(key) => setTab(key as CashTab)} />

        {/* У транзакций свой поиск — фильтры кассы к ним не применяются.
            Панель правит фильтры только текущей вкладки. */}
        {tab !== "transactions" && (
          <CashFiltersPanel
            filters={filters}
            stores={stores ?? []}
            departments={departments ?? []}
            showRemaining={filterTab === "overview"}
            onChange={patchFilters}
            onReset={resetFilters}
          />
        )}

        {tab === "overview" && canDebtEntry && (
          <>
            {canReports && summaryError && <ErrorAlert message={summaryError} onRetry={reloadSummary} />}
            <section className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
              {canReports && (
                <SummaryCard
                  title={isToday ? "Поступило сегодня" : hasDates ? "Поступило за период" : "Поступило за всё время"}
                  tone="success"
                  value={money(incomeTotal, incomeCurrency)}
                  rows={[
                    { label: "Наличные", value: money(cashTotal, incomeCurrency) },
                    { label: "Безналичные", value: money(cashlessTotal, incomeCurrency) },
                    ...otherIncomeCurrencies.map(([currency, value]) => ({
                      label: "Также поступило",
                      value: money(value, currency),
                    })),
                  ]}
                />
              )}
              {canPayments && (
                <SummaryCard
                  title="Ожидает подтверждения"
                  tone="primary"
                  value={money(toReviewSum, toReviewCurrency)}
                  rows={[
                    ...otherReviewCurrencies.map(([currency, value]) => ({
                      label: "Также в очереди",
                      value: money(value, currency),
                    })),
                    { label: "Оплат в очереди", value: String(queue.toReview.length) },
                    {
                      label: "Из них наличными",
                      value: money(toReviewCashByCurrency[toReviewCurrency] ?? 0, toReviewCurrency),
                    },
                  ]}
                />
              )}
              <SummaryCard
                title="Дебиторка"
                tone="destructive"
                value={money(debtTotal, debtCurrency)}
                rows={[
                  ...otherDebtCurrencies.map(([currency, value]) => ({
                    label: "Также в долге",
                    value: money(value, currency),
                  })),
                  { label: "Клиентов с долгом", value: String(debtRows.length) },
                  { label: "С просрочкой", value: String(overdueClients) },
                ]}
              />
            </section>

            <DebtsSection
              rows={debtRows}
              loading={debtsLoading}
              error={debtsError}
              reload={reloadDebts}
              canCheckOverdue={canCheckOverdue}
            />
          </>
        )}

        {tab === "confirm" && canPayments && (
          <ConfirmQueueSection
            q={queue}
            canViewOrders={canViewOrders}
            canReviewOrders={canReviewOrders}
            canEditOrders={canEditOrders}
            canReceivePayments={canPayments}
          />
        )}

        {tab === "journal" && canPayments && <PaymentJournalSection q={queue} log={journalLog} />}

        {tab === "transactions" && canTransactions && (
          <TransactionsSection canConfirm={canPayments} canCreate={canCreatePayments} departments={departments ?? []} />
        )}
      </div>
    </AppShell>
  );
}

export default function CashierPage() {
  // Доступ, если есть хотя бы одна из секций: очередь, аналитика с долгами или транзакции.
  return (
    <RequirePerm perm={["payments.confirm", "payments.create", "reports.view", "payments.view"]} title="Касса">
      <CashierInner />
    </RequirePerm>
  );
}
