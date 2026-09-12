"use client";

import { RefreshCcw, Search } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Chip } from "@/components/ui/chip";
import { DataGate } from "@/components/ui/data-state";
import { FilterDropdown } from "@/components/ui/filter-dropdown";
import { Input } from "@/components/ui/input";
import { LoadMore } from "@/components/ui/load-more";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { PaidMethodSummary } from "@/components/transactions/paid-method-summary";
import { TransactionActions, transactionActions } from "@/components/transactions/transaction-actions";
import { TransactionModals } from "@/components/transactions/transaction-modals";
import { useTransactions } from "@/components/transactions/use-transactions";
import { paymentStage } from "@/lib/constants";
import type { Department } from "@/lib/types";
import { currencySymbol, formatDateTime, formatMoney } from "@/lib/utils";

/* ── Вкладка «Транзакции» (десктоп): все платежи, возвраты и чеки ───────── */
export function TransactionsSection({
  onChanged,
  canConfirm,
  canCreate,
  departments,
}: {
  onChanged?: () => Promise<unknown>;
  canConfirm: boolean;
  canCreate: boolean;
  departments: Department[];
}) {
  const t = useTransactions({ onChanged });
  const { data, rows, meta, loading, loadError } = t;
  const perms = { canConfirm, canCreate };

  return (
    <section className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-3">
        <Card>
          <CardContent className="py-5">
            <div className="text-xs uppercase tracking-wide text-[var(--muted-foreground)]">Операций</div>
            <div className="mt-1 text-2xl font-semibold tabular-nums">{data?.count ?? 0}</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="py-5">
            <div className="text-xs uppercase tracking-wide text-[var(--muted-foreground)]">Оплачено</div>
            <div className="mt-1 text-2xl font-semibold tabular-nums text-[var(--success)]">
              {formatMoney(data?.summary.paid_by_currency.KZT ?? 0)} ₸
              {Number(data?.summary.paid_by_currency.USD ?? 0) > 0 && (
                <span className="ml-2 text-base text-[var(--muted-foreground)]">
                  + {formatMoney(data!.summary.paid_by_currency.USD)} $
                </span>
              )}
            </div>
            {/* Из чего сложился итог: касса сразу видит нал/QR/счёт. */}
            <PaidMethodSummary summary={data?.summary.paid_by_method} />
          </CardContent>
        </Card>
        <Card>
          <CardContent className="py-5">
            <div className="text-xs uppercase tracking-wide text-[var(--muted-foreground)]">Возвращено</div>
            <div className="mt-1 text-2xl font-semibold tabular-nums">
              {formatMoney(data?.summary.refunded_by_currency.KZT ?? 0)} ₸
              {Number(data?.summary.refunded_by_currency.USD ?? 0) > 0 && (
                <span className="ml-2 text-base text-[var(--muted-foreground)]">
                  + {formatMoney(data!.summary.refunded_by_currency.USD)} $
                </span>
              )}
            </div>
          </CardContent>
        </Card>
      </div>

      {t.error && !t.refundFor && !t.rejectFor && !t.restoreFor && (
        <div className="rounded-lg border border-[var(--destructive)]/25 bg-[var(--destructive)]/5 px-3 py-2 text-sm text-[var(--destructive)]">
          {t.error}
        </div>
      )}

      <Card>
        <CardHeader className="flex-row items-center justify-between gap-3">
          <CardTitle>Все платежи, возвраты и чеки</CardTitle>
          <Button variant="outline" size="sm" onClick={() => void t.refreshFromStart()}>
            <RefreshCcw className="size-4" /> Обновить
          </Button>
        </CardHeader>
        <CardContent>
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <div className="relative max-w-sm flex-1 basis-64">
              <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
              <Input
                className="pl-9"
                placeholder="Клиент, заказ или операция"
                value={t.query}
                onChange={(e) => t.setQuery(e.target.value)}
              />
            </div>
            <FilterDropdown
              label="Отдел"
              active={t.department}
              onChange={t.setDepartment}
              options={[
                { key: "all", label: "Все" },
                ...departments.map((row) => ({ key: row.code, label: row.name })),
              ]}
            />
            {/* Мини-отчёт по статусам: пилюля = фильтр, цифра = сколько таких. */}
            <div className="flex flex-wrap gap-1.5">
              {t.statusItems.map((item) => (
                <Chip key={item.key} active={t.statusFilter === item.key} onClick={() => t.setStatusFilter(item.key)}>
                  {item.label}
                  <span className="tabular-nums">{item.count}</span>
                </Chip>
              ))}
            </div>
          </div>
          {/* Спиннер на весь блок — только пока нет ни одной строки: догрузка
              следующих страниц не должна прятать уже показанное. */}
          {((loading && rows.length === 0) || loadError) && (
            <DataGate loading={loading && rows.length === 0} error={loadError} onRetry={t.reload} />
          )}
          {!loading && !loadError && rows.length === 0 && (
            <p className="py-8 text-center text-sm text-[var(--muted-foreground)]">Транзакций пока нет.</p>
          )}
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
                    const state = paymentStage(row.effective_status ?? row.status);
                    return (
                      <TR key={row.id}>
                        <TD>
                          <div className="font-medium">PAY-{String(row.id).padStart(6, "0")}</div>
                          <div className="text-xs text-[var(--muted-foreground)]">Заказ #{row.order}</div>
                          <div className="text-xs text-[var(--muted-foreground)]">{formatDateTime(row.paid_at)}</div>
                        </TD>
                        <TD>{row.client_name ?? "—"}</TD>
                        <TD>
                          {row.method_label ?? row.method}
                          {row.provider?.channel === "qr" && (
                            <div className="text-xs text-[var(--muted-foreground)]">Kaspi QR</div>
                          )}
                        </TD>
                        <TD className="font-medium tabular-nums">
                          {formatMoney(row.amount)} {currencySymbol(row.currency)}
                        </TD>
                        <TD>
                          <button
                            type="button"
                            onClick={() => t.openStatus(row)}
                            className="rounded-md outline-none ring-offset-2 hover:opacity-80 focus-visible:ring-2 focus-visible:ring-[var(--ring)]"
                            title="Нажмите, чтобы узнать значение статуса"
                          >
                            <Badge tone={state.tone}>{state.label}</Badge>
                          </button>
                        </TD>
                        <TD>
                          {Number(row.refunded_amount ?? 0) > 0 ? (
                            <span className="text-sm tabular-nums">
                              {formatMoney(row.refunded_amount ?? 0)} {currencySymbol(row.currency)}
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
              <LoadMore
                shown={rows.length}
                total={meta?.count ?? rows.length}
                hasMore={(meta?.page ?? 1) < (meta?.pages ?? 1)}
                loading={loading && t.page > 1}
                onClick={t.loadNextPage}
              />
            </>
          )}
        </CardContent>
      </Card>

      <TransactionModals t={t} canConfirm={canConfirm} canCreate={canCreate} />
    </section>
  );
}
