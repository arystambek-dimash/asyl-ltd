"use client";
import { ChevronRight, RefreshCw } from "lucide-react";
import { ActionCard } from "@/components/ui/action-card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { CurrencyAmounts } from "@/components/ui/currency-amounts";
import { ErrorAlert } from "@/components/ui/data-state";
import { SearchInput } from "@/components/ui/search-input";
import { LoadMore } from "@/components/ui/load-more";
import { pluralRu } from "@/lib/utils";
import { debtHref, debtPaymentState } from "../debt-state";
import { formatCompactTotals } from "../totals";
import type { CashierModel } from "../use-cashier";
import { useDebtList } from "../use-debt-list";

/** Долги клиентов списком: имя и телефон слева, остаток справа, тап — в карточку клиента. */
export function DebtsScreen({ model }: { model: CashierModel }) {
  const { debtRows: rows, debts, debtTotals, debtsReady, perms } = model;
  const list = useDebtList(rows, debts.reload);
  const { overdue, filtered, visible } = list;

  return (
    <section className="flex flex-col gap-4">
      {debtsReady && (
        <p className="text-[13px] text-[var(--muted-foreground)]">
          Дебиторка <span className="font-semibold text-[var(--foreground)]">{formatCompactTotals(debtTotals)}</span>
          {` · ${debtTotals.clients} ${pluralRu(debtTotals.clients, ["клиент", "клиента", "клиентов"])}`}
          {debtTotals.overdue > 0 ? ` · ${debtTotals.overdue} с просрочкой` : ""}
        </p>
      )}
      <div className="flex items-center gap-2">
        <SearchInput
          wrapperClassName="flex-1"
          placeholder="Поиск по клиенту или телефону"
          value={list.query}
          onChange={(e) => list.setQuery(e.target.value)}
        />
        {perms.canCheckOverdue && (
          <Button
            variant="outline"
            size="icon"
            disabled={overdue.busy}
            onClick={() => void overdue.run()}
            aria-label="Проверить просрочки"
          >
            <RefreshCw className={"size-4" + (overdue.busy ? " animate-spin" : "")} />
          </Button>
        )}
      </div>
      {overdue.message && (
        <p className="rounded-lg border bg-[var(--card)] px-4 py-2 text-sm text-[var(--muted-foreground)] shadow-card">
          {overdue.message}
        </p>
      )}
      {debts.error && rows.length === 0 && <ErrorAlert message={debts.error} onRetry={debts.reload} />}
      {(rows.length > 0 || !debts.error) && (
        <div className="rounded-xl border border-[var(--border)] bg-[var(--card)] shadow-card">
          {debts.loading ? (
            <p className="py-8 text-center text-sm text-[var(--muted-foreground)]">Загрузка…</p>
          ) : !debts.error && filtered.length === 0 ? (
            <p className="py-8 text-center text-sm text-[var(--muted-foreground)]">Долгов нет.</p>
          ) : (
            <ul className="divide-y divide-[var(--border)]">
              {visible.map((row) => {
                const state = debtPaymentState(row);
                return (
                  <li key={row.client_id}>
                    <ActionCard
                      primaryAction={{
                        href: debtHref(row),
                        label: `Долг клиента ${row.client_name || row.client_id}`,
                      }}
                      className="flex items-center gap-3 px-4 py-3.5"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-[15px] font-semibold">{row.client_name || "—"}</div>
                        <div className="text-xs text-[var(--muted-foreground)]">{row.client_phone || "—"}</div>
                        <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-[var(--muted-foreground)]">
                          <span>
                            {row.orders_count} {pluralRu(row.orders_count, ["заказ", "заказа", "заказов"])}
                          </span>
                          <Badge tone={state.tone} dot>
                            {state.label}
                          </Badge>
                          {row.stores_count > 0 && (
                            <span>
                              {row.stores_count} {pluralRu(row.stores_count, ["магазин", "магазина", "магазинов"])}
                            </span>
                          )}
                          {row.overdue_count > 0 && (
                            <Badge tone="destructive" dot>
                              {row.overdue_count} {pluralRu(row.overdue_count, ["просрочка", "просрочки", "просрочек"])}
                            </Badge>
                          )}
                        </div>
                      </div>
                      <div className="shrink-0 text-right text-[15px] font-semibold tabular-nums text-[var(--destructive)]">
                        <CurrencyAmounts
                          byCurrency={row.debt_by_currency}
                          fallbackAmount={row.debt_total}
                          fallbackCurrency={row.debt_currency}
                        />
                      </div>
                      <ChevronRight className="size-4 shrink-0 text-[var(--muted-foreground)]" />
                    </ActionCard>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
      <LoadMore {...list.more} />
    </section>
  );
}
