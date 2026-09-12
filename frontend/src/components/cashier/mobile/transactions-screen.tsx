"use client";
import { ChevronRight, RefreshCcw, Search } from "lucide-react";
import { PaidMethodSummary } from "@/components/transactions/paid-method-summary";
import { TransactionModals } from "@/components/transactions/transaction-modals";
import { useTransactions } from "@/components/transactions/use-transactions";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Chip } from "@/components/ui/chip";
import { DataGate } from "@/components/ui/data-state";
import { FilterDropdown } from "@/components/ui/filter-dropdown";
import { Input } from "@/components/ui/input";
import { LoadMore } from "@/components/ui/load-more";
import { paymentStage } from "@/lib/constants";
import { groupByDay } from "@/lib/day-groups";
import { useLocalDay } from "@/lib/use-local-day";
import { currencySymbol, formatMoney, formatTime } from "@/lib/utils";
import type { CashierModel } from "../use-cashier";

/** Транзакции на телефоне: сводка, поиск и чипы, лента по дням; тап по строке — шторка деталей с действиями. */
export function TransactionsScreen({ model }: { model: CashierModel }) {
  const { perms, departments, queue } = model;
  const t = useTransactions({ onChanged: queue.reload });
  const { data, rows, meta, loading, loadError } = t;
  const currentDay = useLocalDay();
  const groups = groupByDay(rows, (row) => new Date(row.paid_at), currentDay);

  return (
    <section className="flex flex-col gap-4">
      <Card>
        <CardContent className="p-4">
          <div className="text-[13px] text-[var(--muted-foreground)]">Оплачено</div>
          <div className="mt-1 text-[22px] font-bold tabular-nums text-[var(--success)]">
            {formatMoney(data?.summary.paid_by_currency.KZT ?? 0)} ₸
            {Number(data?.summary.paid_by_currency.USD ?? 0) > 0 && (
              <span className="ml-2 text-base text-[var(--muted-foreground)]">
                + {formatMoney(data!.summary.paid_by_currency.USD)} $
              </span>
            )}
          </div>
          <PaidMethodSummary summary={data?.summary.paid_by_method} />
          <div className="mt-3 grid grid-cols-2 gap-3 border-t border-[var(--border)] pt-3 text-[13px]">
            <div>
              <div className="text-[var(--muted-foreground)]">Возвращено</div>
              <div className="tabular-nums">
                {formatMoney(data?.summary.refunded_by_currency.KZT ?? 0)} ₸
                {Number(data?.summary.refunded_by_currency.USD ?? 0) > 0
                  ? ` + ${formatMoney(data!.summary.refunded_by_currency.USD)} $`
                  : ""}
              </div>
            </div>
            <div>
              <div className="text-[var(--muted-foreground)]">Операций</div>
              <div className="tabular-nums">{data?.count ?? 0}</div>
            </div>
          </div>
        </CardContent>
      </Card>

      {t.error && !t.refundFor && !t.rejectFor && !t.restoreFor && (
        <div className="rounded-lg border border-[var(--destructive)]/25 bg-[var(--destructive)]/5 px-3 py-2 text-sm text-[var(--destructive)]">
          {t.error}
        </div>
      )}

      <div className="flex flex-col gap-2">
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
          <Input
            className="pl-9"
            placeholder="Клиент, заказ или операция"
            value={t.query}
            onChange={(e) => t.setQuery(e.target.value)}
          />
        </div>
        <div className="flex items-center gap-2">
          <FilterDropdown
            label="Отдел"
            active={t.department}
            onChange={t.setDepartment}
            options={[{ key: "all", label: "Все" }, ...departments.map((row) => ({ key: row.code, label: row.name }))]}
          />
          <Button variant="outline" size="icon" aria-label="Обновить" onClick={() => void t.refreshFromStart()}>
            <RefreshCcw className="size-4" />
          </Button>
        </div>
        {/* Мини-отчёт по статусам: пилюля = фильтр, цифра = сколько таких. */}
        <div className="flex gap-1.5 overflow-x-auto pb-0.5 [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          {t.statusItems.map((item) => (
            <Chip key={item.key} active={t.statusFilter === item.key} onClick={() => t.setStatusFilter(item.key)}>
              {item.label}
              <span className="tabular-nums">{item.count}</span>
            </Chip>
          ))}
        </div>
      </div>

      {((loading && rows.length === 0) || loadError) && (
        <DataGate loading={loading && rows.length === 0} error={loadError} onRetry={t.reload} />
      )}
      {!loading && !loadError && rows.length === 0 && (
        <p className="py-8 text-center text-sm text-[var(--muted-foreground)]">Транзакций пока нет.</p>
      )}

      {groups.map((group) => (
        <section key={group.key} className="flex flex-col gap-2">
          <h2 className="px-1 text-[15px] font-semibold">{group.label}</h2>
          <ul className="divide-y divide-[var(--border)] rounded-xl border border-[var(--border)] bg-[var(--card)] shadow-card">
            {group.items.map((row) => {
              const state = paymentStage(row.effective_status ?? row.status);
              const refunded = Number(row.refunded_amount ?? 0);
              const pendingRefund = Number(row.pending_refund_amount ?? 0);
              return (
                <li key={row.id}>
                  <button
                    type="button"
                    onClick={() => t.openStatus(row)}
                    className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-[var(--muted)]/60"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[15px] font-medium">
                        PAY-{String(row.id).padStart(6, "0")} · {row.client_name ?? "—"}
                      </div>
                      <div className="text-xs text-[var(--muted-foreground)]">
                        {formatTime(row.paid_at)} · заказ #{row.order} · {row.method_label ?? row.method}
                        {row.provider?.channel === "qr" ? " · Kaspi QR" : ""}
                      </div>
                    </div>
                    <div className="flex shrink-0 flex-col items-end gap-1 text-right">
                      <span className="text-[15px] font-semibold tabular-nums">
                        {formatMoney(row.amount)} {currencySymbol(row.currency)}
                      </span>
                      <Badge tone={state.tone}>{state.label}</Badge>
                      {refunded > 0 ? (
                        <span className="text-xs tabular-nums text-[var(--muted-foreground)]">
                          возврат {formatMoney(refunded)} {currencySymbol(row.currency)}
                        </span>
                      ) : pendingRefund > 0 ? (
                        <span className="text-xs text-[var(--warning)]">{formatMoney(pendingRefund)} в обработке</span>
                      ) : null}
                    </div>
                    <ChevronRight className="size-4 shrink-0 text-[var(--muted-foreground)]" />
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      ))}

      {rows.length > 0 && (
        <LoadMore
          shown={rows.length}
          total={meta?.count ?? rows.length}
          hasMore={(meta?.page ?? 1) < (meta?.pages ?? 1)}
          loading={loading && t.page > 1}
          onClick={t.loadNextPage}
        />
      )}

      <TransactionModals t={t} canConfirm={perms.canPayments} canCreate={perms.canCreatePayments} sheet detailActions />
    </section>
  );
}
