"use client";
import { useState } from "react";
import { ChevronRight, RefreshCw, Search } from "lucide-react";
import { ActionCard } from "@/components/ui/action-card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { CurrencyAmounts } from "@/components/ui/currency-amounts";
import { ErrorAlert } from "@/components/ui/data-state";
import { Input } from "@/components/ui/input";
import { LoadMore } from "@/components/ui/load-more";
import { formatCompactCurrency, pluralRu } from "@/lib/utils";
import { debtPaymentState, matchesDebtQuery } from "../debt-state";
import type { CashierModel } from "../use-cashier";
import { useOverdueCheck } from "../use-overdue-check";

/** Долги клиентов списком: имя и телефон слева, остаток справа, тап — в карточку клиента. */
export function DebtsScreen({ model }: { model: CashierModel }) {
  const { debtRows: rows, debts, debtTotals, debtsReady, perms } = model;
  const [q, setQ] = useState("");
  const [limit, setLimit] = useState(25);
  const overdue = useOverdueCheck(debts.reload);
  const filtered = rows.filter((row) => matchesDebtQuery(row, q));
  const visible = filtered.slice(0, limit);
  const totalsLine = [
    formatCompactCurrency(debtTotals.total, debtTotals.currency),
    ...debtTotals.other.map(([unit, value]) => formatCompactCurrency(value, unit)),
  ].join(" + ");

  return (
    <section className="flex flex-col gap-4">
      {debtsReady && (
        <p className="text-[13px] text-[var(--muted-foreground)]">
          Дебиторка <span className="font-semibold text-[var(--foreground)]">{totalsLine}</span>
          {` · ${debtTotals.clients} ${pluralRu(debtTotals.clients, ["клиент", "клиента", "клиентов"])}`}
          {debtTotals.overdue > 0 ? ` · ${debtTotals.overdue} с просрочкой` : ""}
        </p>
      )}
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
          <Input
            className="pl-9"
            placeholder="Поиск по клиенту или телефону"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>
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
                        kind: "link",
                        href: `/accounting/debts/clients/${row.client_id}`,
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
                          fallbackCurrency={row.debt_currency ?? "KZT"}
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
      <LoadMore
        shown={visible.length}
        total={filtered.length}
        hasMore={filtered.length > visible.length}
        onClick={() => setLimit((current) => current + 25)}
      />
    </section>
  );
}
