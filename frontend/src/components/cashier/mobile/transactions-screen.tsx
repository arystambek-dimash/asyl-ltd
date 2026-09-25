"use client";
import { ChevronRight, RefreshCcw } from "lucide-react";
import { PaidMethodSummary, summaryPaidParts } from "@/components/transactions/paid-method-summary";
import {
  TransactionStatusChips,
  TransactionsListState,
  TransactionsLoadMore,
} from "@/components/transactions/transaction-list-parts";
import { TransactionModals } from "@/components/transactions/transaction-modals";
import { useTransactions } from "@/components/transactions/use-transactions";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { CurrencyAmounts } from "@/components/ui/currency-amounts";
import { ErrorAlert } from "@/components/ui/data-state";
import { SearchInput } from "@/components/ui/search-input";
import { paymentStage } from "@/lib/constants";
import { groupByDay } from "@/lib/day-groups";
import { useLocalDay } from "@/lib/use-local-day";
import { formatCurrency, formatMoney, formatPaymentNumber, formatTime } from "@/lib/utils";
import type { CashierModel } from "../use-cashier";

/** Транзакции на телефоне: сводка, поиск и чипы, лента по дням; тап по строке — шторка деталей с действиями. */
export function TransactionsScreen({ model }: { model: CashierModel }) {
  const { perms, scope } = model;
  // Отдел берём из шапки кассы: закреплённый или выбранный.
  const t = useTransactions({ department: scope.department });
  const { meta, rows } = t;
  const currentDay = useLocalDay();
  const groups = groupByDay(rows, (row) => new Date(row.paid_at), currentDay);

  return (
    <section className="flex flex-col gap-4">
      <Card>
        <CardContent className="p-4">
          <div className="text-[13px] text-[var(--muted-foreground)]">Оплачено</div>
          <CurrencyAmounts
            className="mt-1 items-start text-[22px] font-bold tabular-nums text-[var(--success)]"
            byCurrency={meta?.summary.paid_by_currency}
            fallbackAmount={0}
          />
          <PaidMethodSummary parts={summaryPaidParts(meta?.summary)} className="mt-2 text-xs" />
          <div className="mt-3 grid grid-cols-2 gap-3 border-t border-[var(--border)] pt-3 text-[13px]">
            <div>
              <div className="text-[var(--muted-foreground)]">Возвращено</div>
              <CurrencyAmounts
                className="items-start tabular-nums"
                byCurrency={meta?.summary.refunded_by_currency}
                fallbackAmount={0}
              />
            </div>
            <div>
              <div className="text-[var(--muted-foreground)]">Операций</div>
              <div className="tabular-nums">{meta?.count ?? 0}</div>
            </div>
          </div>
        </CardContent>
      </Card>

      {t.pageError && <ErrorAlert message={t.pageError} />}

      <div className="flex flex-col gap-2">
        <div className="flex items-center gap-2">
          <SearchInput
            wrapperClassName="flex-1"
            placeholder="Клиент, заказ или операция"
            value={t.query}
            onChange={(e) => t.setQuery(e.target.value)}
          />
          <Button variant="outline" size="icon" aria-label="Обновить" onClick={() => void t.refreshFromStart()}>
            <RefreshCcw className="size-4" />
          </Button>
        </div>
        <TransactionStatusChips
          t={t}
          className="flex gap-1.5 overflow-x-auto pb-0.5 [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
        />
      </div>

      <TransactionsListState t={t} />

      {groups.map((group) => (
        <section key={group.key} className="flex flex-col gap-2">
          <h2 className="px-1 text-[15px] font-semibold">{group.label}</h2>
          <ul className="divide-y divide-[var(--border)] rounded-xl border border-[var(--border)] bg-[var(--card)] shadow-card">
            {group.items.map((row) => {
              const state = paymentStage(row);
              const refunded = Number(row.refunded_amount ?? 0);
              const pendingRefund = Number(row.pending_refund_amount ?? 0);
              return (
                <li key={row.id}>
                  <button
                    type="button"
                    onClick={() => t.open("status", row)}
                    className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-[var(--muted)]/60"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[15px] font-medium">
                        {formatPaymentNumber(row.id)} · {row.client_name ?? "—"}
                      </div>
                      <div className="text-xs text-[var(--muted-foreground)]">
                        {formatTime(row.paid_at)} · заказ #{row.order} · {row.method_label}
                        {row.provider?.channel === "qr" ? " · Kaspi QR" : ""}
                      </div>
                    </div>
                    <div className="flex shrink-0 flex-col items-end gap-1 text-right">
                      <span className="text-[15px] font-semibold tabular-nums">
                        {formatCurrency(row.amount, row.currency)}
                      </span>
                      <Badge tone={state.tone}>{state.label}</Badge>
                      {refunded > 0 ? (
                        <span className="text-xs tabular-nums text-[var(--muted-foreground)]">
                          возврат {formatCurrency(refunded, row.currency)}
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

      {rows.length > 0 && <TransactionsLoadMore t={t} />}

      <TransactionModals t={t} mobile={{ canConfirm: perms.canPayments, canCreate: perms.canCreatePayments }} />
    </section>
  );
}
