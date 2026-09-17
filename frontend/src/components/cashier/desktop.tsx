"use client";
import Link from "next/link";
import { useState } from "react";
import dynamic from "next/dynamic";
import { ArrowUpRight, RefreshCw, Search } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { PaymentStageBadge } from "@/components/payment-chain";
import { DepartmentComparison } from "@/components/reports/department-comparison";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { CurrencyAmounts } from "@/components/ui/currency-amounts";
import { ErrorAlert } from "@/components/ui/data-state";
import { Input } from "@/components/ui/input";
import { LoadMore } from "@/components/ui/load-more";
import { SummaryCard } from "@/components/ui/summary-card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { Tabs, type TabDef } from "@/components/ui/tabs";
import { PAYMENT_METHOD_LABELS } from "@/lib/constants";
import { withBack } from "@/lib/navigation";
import type { ClientDebt, Me } from "@/lib/types";
import { formatCurrency, todayLocalIsoDate } from "@/lib/utils";
import { ActionError } from "./action-error";
import { CashFiltersPanel } from "./cash-filters-panel";
import { debtPaymentState, matchesDebtQuery } from "./debt-state";
import { AwaitingPaymentRow } from "./awaiting-payment-row";
import { DepartmentBadge } from "./department-badge";
import { canOpenQueueOrder, type DepartmentScope } from "./scope";
import type { CashierModel } from "./use-cashier";
import type { CashierQueue } from "./use-cashier-queue";
import { useOverdueCheck } from "./use-overdue-check";
import type { CashView } from "./view";

const TransactionsSection = dynamic(() =>
  import("@/components/transactions-section").then((m) => m.TransactionsSection),
);

/* ── Вкладка «Оплаты»: ждут оплаты и оплаты к подтверждению ─────────────── */
// Оплаты к подтверждению — общая очередь всех отделов, бейдж отдела на карточке говорит, чья это оплата.
// «Ждут оплаты» — отгруженные заказы отдела кассы без долга: принять оплату или перевести в долг.
function PaymentsSection({
  q,
  me,
  canViewOrders,
  canReceivePayments,
  assigned,
}: {
  q: CashierQueue;
  me: Me | null;
  canViewOrders: boolean;
  canReceivePayments: boolean;
  assigned: DepartmentScope["assigned"];
}) {
  return (
    <section className="flex flex-col gap-4">
      <ActionError message={q.error} />
      {q.loadError && <ErrorAlert message={q.loadError} onRetry={q.reload} />}

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Ждут оплаты</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            {!q.loading && !q.loadError && q.awaiting.length === 0 && (
              <p className="text-sm text-[var(--muted-foreground)]">Все отгруженные заказы оплачены или в долге.</p>
            )}
            {q.awaiting.length > 0 && (
              <ul className="divide-y divide-[var(--border)] rounded-lg border">
                {q.awaiting.map((order) => (
                  <AwaitingPaymentRow key={order.id} order={order} q={q} me={me} canOpenOrder={canViewOrders} />
                ))}
              </ul>
            )}
            <LoadMore
              shown={q.awaiting.length}
              total={q.awaitingPage.count}
              hasMore={q.awaitingPage.hasMore}
              loading={q.awaitingPage.loadingMore}
              onClick={q.awaitingPage.loadMore}
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Оплаты к подтверждению</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            {!q.loading && !q.loadError && q.toReview.length === 0 && (
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
                      {canViewOrders && canOpenQueueOrder(assigned, p.department) ? (
                        <Link
                          href={withBack(`/orders/${p.order}`, "/accounting?view=confirm")}
                          className="hover:underline"
                        >
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
            <LoadMore
              shown={q.toReview.length}
              total={q.queuePage.count}
              hasMore={q.queuePage.hasMore}
              loading={q.queuePage.loadingMore}
              onClick={q.queuePage.loadMore}
            />
          </CardContent>
        </Card>
      </div>
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
  const overdue = useOverdueCheck(reload);
  // Данные уже загружены целиком — лениво рендерим, чтобы длинный список
  // должников не разворачивался простынёй.
  const [limit, setLimit] = useState(25);

  const filtered = rows.filter((row) => matchesDebtQuery(row, q));
  const visible = filtered.slice(0, limit);

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
            <Button
              size="sm"
              variant="outline"
              disabled={overdue.busy}
              onClick={() => void overdue.run()}
              aria-label="Проверить просрочки"
            >
              <RefreshCw className={"size-4" + (overdue.busy ? " animate-spin" : "")} />
              <span className="hidden sm:inline">Проверить просрочки</span>
            </Button>
          )}
        </div>
      </div>

      {overdue.message && (
        <p className="rounded-lg border bg-[var(--card)] px-4 py-2 text-sm text-[var(--muted-foreground)] shadow-card">
          {overdue.message}
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

export function CashierDesktop({ model, onTab }: { model: CashierModel; onTab: (view: CashView) => void }) {
  const {
    view,
    perms,
    filters,
    filterScreen,
    filtersByScreen,
    patchFilters,
    resetFilters,
    summary,
    queueSummary,
    debts,
    income,
    incomeReady,
    queueTotals,
    queueReady,
    debtRows,
    debtTotals,
    debtsReady,
    queue,
    stores,
    departments,
  } = model;
  const money = formatCurrency;
  const overviewFilters = filtersByScreen.overview;
  const today = todayLocalIsoDate();
  const isToday = overviewFilters.dateFrom === today && overviewFilters.dateTo === today;
  const hasDates = Boolean(overviewFilters.dateFrom || overviewFilters.dateTo);

  const tabs: TabDef[] = [
    ...(perms.canDebtEntry ? [{ key: "overview", label: perms.canReports ? "Общее" : "Долги" }] : []),
    ...(perms.canPayments
      ? [
          {
            key: "confirm",
            label: "Оплаты",
            count: view === "confirm" && !queue.loading ? queue.awaitingPage.count + queue.queuePage.count : undefined,
          },
        ]
      : []),
    ...(perms.canTransactions ? [{ key: "transactions", label: "Транзакции" }] : []),
  ];

  return (
    <AppShell title="Касса" section="Работа" description="Поступления, оплаты, долги и транзакции в одном месте.">
      <div className="flex flex-col gap-6">
        <Tabs
          className="overflow-x-auto whitespace-nowrap"
          tabs={tabs}
          active={view}
          onChange={(key) => onTab(key as CashView)}
        />

        {/* У транзакций свой поиск — фильтры кассы к ним не применяются.
            Панель правит фильтры только текущей вкладки. */}
        {filterScreen && (
          <CashFiltersPanel
            filters={filters}
            stores={stores}
            departments={departments}
            showRemaining={filterScreen === "overview"}
            onChange={patchFilters}
            onReset={resetFilters}
          />
        )}

        {view === "overview" && perms.canDebtEntry && (
          <>
            {perms.canReports && summary.error && <ErrorAlert message={summary.error} onRetry={summary.reload} />}
            {perms.canPayments && queueSummary.error && (
              <ErrorAlert message={queueSummary.error} onRetry={queueSummary.reload} />
            )}
            <section className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
              {perms.canReports && (
                <SummaryCard
                  title={
                    isToday
                      ? "Чистое поступление сегодня"
                      : hasDates
                        ? "Чистое поступление за период"
                        : "Чистое поступление за всё время"
                  }
                  tone={income.total < 0 ? "destructive" : "success"}
                  value={incomeReady ? money(income.total, income.currency) : "—"}
                  rows={
                    !incomeReady
                      ? []
                      : [
                          { label: "Наличные, нетто", value: money(income.cash, income.currency) },
                          { label: "Безналичные, нетто", value: money(income.cashless, income.currency) },
                          ...income.otherCurrencies.map(([currency, value]) => ({
                            label: "Также чистыми",
                            value: money(value, currency),
                          })),
                          ...(income.refunded > 0
                            ? [
                                { label: "Поступило до возвратов", value: money(income.gross, income.currency) },
                                { label: "Возвращено", value: money(income.refunded, income.currency) },
                              ]
                            : []),
                          ...income.otherRefunds.flatMap(([currency, value]) => [
                            {
                              label: `Поступило до возвратов, ${currency}`,
                              value: money(income.grossFor(currency), currency),
                            },
                            { label: `Возвращено, ${currency}`, value: money(value, currency) },
                          ]),
                        ]
                  }
                />
              )}
              {perms.canPayments && (
                <SummaryCard
                  title="Ожидает подтверждения"
                  tone="primary"
                  value={queueReady ? money(queueTotals.total, queueTotals.currency) : "—"}
                  rows={
                    !queueReady
                      ? []
                      : [
                          ...queueTotals.other.map(([currency, value]) => ({
                            label: "Также в очереди",
                            value: money(value, currency),
                          })),
                          { label: "Оплат в очереди", value: String(queueTotals.count) },
                          { label: "Из них наличными", value: money(queueTotals.cash, queueTotals.currency) },
                        ]
                  }
                />
              )}
              <SummaryCard
                title="Дебиторка"
                tone="destructive"
                value={debtsReady ? money(debtTotals.total, debtTotals.currency) : "—"}
                rows={
                  !debtsReady
                    ? []
                    : [
                        ...debtTotals.other.map(([currency, value]) => ({
                          label: "Также в долге",
                          value: money(value, currency),
                        })),
                        { label: "Клиентов с долгом", value: String(debtTotals.clients) },
                        { label: "С просрочкой", value: String(debtTotals.overdue) },
                      ]
                }
              />
            </section>

            {perms.canReports && summary.data?.departments && (
              <DepartmentComparison
                rows={summary.data.departments}
                incomeOnly
                from={summary.data.from}
                to={summary.data.to}
              />
            )}
            <DebtsSection
              rows={debtRows}
              loading={debts.loading}
              error={debts.error}
              reload={debts.reload}
              canCheckOverdue={perms.canCheckOverdue}
            />
          </>
        )}

        {view === "confirm" && perms.canPayments && (
          <PaymentsSection
            q={queue}
            me={model.me}
            canViewOrders={perms.canViewOrders}
            canReceivePayments={perms.canPayments}
            assigned={model.scope.assigned}
          />
        )}

        {view === "transactions" && perms.canTransactions && (
          <TransactionsSection
            canConfirm={perms.canPayments}
            canCreate={perms.canCreatePayments}
            departments={departments}
            onChanged={queue.reload}
          />
        )}
      </div>
    </AppShell>
  );
}
