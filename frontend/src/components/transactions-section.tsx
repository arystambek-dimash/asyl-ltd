"use client";

import { RefreshCcw } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { CurrencyAmounts } from "@/components/ui/currency-amounts";
import { ErrorAlert } from "@/components/ui/data-state";
import { FilterDropdown } from "@/components/ui/filter-dropdown";
import { SearchInput } from "@/components/ui/search-input";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { PaidMethodSummary, summaryPaidParts } from "@/components/transactions/paid-method-summary";
import { TransactionActions, transactionActions } from "@/components/transactions/transaction-actions";
import {
  TransactionStatusChips,
  TransactionsListState,
  TransactionsLoadMore,
} from "@/components/transactions/transaction-list-parts";
import { TransactionModals } from "@/components/transactions/transaction-modals";
import { useTransactions } from "@/components/transactions/use-transactions";
import { paymentStage } from "@/lib/constants";
import type { Department } from "@/lib/types";
import { formatCurrency, formatDateTime, formatMoney, formatPaymentNumber } from "@/lib/utils";

/* ── Вкладка «Транзакции» (десктоп): все платежи, возвраты и чеки ───────── */
export function TransactionsSection({
  canConfirm,
  canCreate,
  departments,
}: {
  canConfirm: boolean;
  canCreate: boolean;
  departments: Department[];
}) {
  const t = useTransactions();
  const { meta, rows } = t;
  const perms = { canConfirm, canCreate };

  return (
    <section className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-3">
        <Card>
          <CardContent className="py-5">
            <div className="text-xs uppercase tracking-wide text-[var(--muted-foreground)]">Операций</div>
            <div className="mt-1 text-2xl font-semibold tabular-nums">{meta?.count ?? 0}</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="py-5">
            <div className="text-xs uppercase tracking-wide text-[var(--muted-foreground)]">Оплачено</div>
            <CurrencyAmounts
              className="mt-1 items-start text-2xl font-semibold tabular-nums text-[var(--success)]"
              byCurrency={meta?.summary.paid_by_currency}
              fallbackAmount={0}
            />
            {/* Из чего сложился итог: касса сразу видит нал/QR/счёт. */}
            <PaidMethodSummary parts={summaryPaidParts(meta?.summary)} className="mt-2 text-xs" />
          </CardContent>
        </Card>
        <Card>
          <CardContent className="py-5">
            <div className="text-xs uppercase tracking-wide text-[var(--muted-foreground)]">Возвращено</div>
            <CurrencyAmounts
              className="mt-1 items-start text-2xl font-semibold tabular-nums"
              byCurrency={meta?.summary.refunded_by_currency}
              fallbackAmount={0}
            />
          </CardContent>
        </Card>
      </div>

      {t.pageError && <ErrorAlert message={t.pageError} />}

      <Card>
        <CardHeader className="flex-row items-center justify-between gap-3">
          <CardTitle>Все платежи, возвраты и чеки</CardTitle>
          <Button variant="outline" size="sm" onClick={() => void t.refreshFromStart()}>
            <RefreshCcw className="size-4" /> Обновить
          </Button>
        </CardHeader>
        <CardContent>
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <SearchInput
              wrapperClassName="max-w-sm flex-1 basis-64"
              placeholder="Клиент, заказ или операция"
              value={t.query}
              onChange={(e) => t.setQuery(e.target.value)}
            />
            <FilterDropdown
              label="Отдел"
              active={t.department}
              onChange={t.setDepartment}
              options={[
                { key: "all", label: "Все" },
                ...departments.map((row) => ({ key: row.code, label: row.name })),
              ]}
            />
            <TransactionStatusChips t={t} className="flex flex-wrap gap-1.5" />
          </div>
          <TransactionsListState t={t} />
          {rows.length > 0 && (
            <>
              <Table>
                <THead>
                  <TR>
                    <TH>Операция</TH>
                    <TH>Клиент</TH>
                    <TH>Способ</TH>
                    <TH>Сумма</TH>
                    <TH>Статус</TH>
                    <TH>Возврат</TH>
                    <TH />
                  </TR>
                </THead>
                <TBody>
                  {rows.map((row) => {
                    const state = paymentStage(row);
                    return (
                      <TR key={row.id}>
                        <TD>
                          <div className="font-medium">{formatPaymentNumber(row.id)}</div>
                          <div className="text-xs text-[var(--muted-foreground)]">Заказ #{row.order}</div>
                          <div className="text-xs text-[var(--muted-foreground)]">{formatDateTime(row.paid_at)}</div>
                        </TD>
                        <TD>{row.client_name ?? "—"}</TD>
                        <TD>
                          {row.method_label}
                          {row.provider?.channel === "qr" && (
                            <div className="text-xs text-[var(--muted-foreground)]">Kaspi QR</div>
                          )}
                        </TD>
                        <TD className="font-medium tabular-nums">{formatCurrency(row.amount, row.currency)}</TD>
                        <TD>
                          <button
                            type="button"
                            onClick={() => t.open("status", row)}
                            className="rounded-md outline-none ring-offset-2 hover:opacity-80 focus-visible:ring-2 focus-visible:ring-[var(--ring)]"
                            title="Нажмите, чтобы узнать значение статуса"
                          >
                            <Badge tone={state.tone}>{state.label}</Badge>
                          </button>
                        </TD>
                        <TD>
                          {Number(row.refunded_amount ?? 0) > 0 ? (
                            <span className="text-sm tabular-nums">
                              {formatCurrency(row.refunded_amount ?? 0, row.currency)}
                            </span>
                          ) : Number(row.pending_refund_amount ?? 0) > 0 ? (
                            <span className="text-sm text-[var(--warning)]">
                              {formatMoney(row.pending_refund_amount ?? 0)} в обработке
                            </span>
                          ) : (
                            "—"
                          )}
                        </TD>
                        <TD>
                          <TransactionActions layout="icons" actions={transactionActions(row, t, perms)} />
                        </TD>
                      </TR>
                    );
                  })}
                </TBody>
              </Table>
              <TransactionsLoadMore t={t} />
            </>
          )}
        </CardContent>
      </Card>

      <TransactionModals t={t} />
    </section>
  );
}
