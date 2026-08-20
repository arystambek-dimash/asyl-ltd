"use client";
import { Fragment, useState } from "react";
import Link from "next/link";
import { ChevronRight } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { CurrencyAmounts } from "@/components/ui/currency-amounts";
import { LoadMore } from "@/components/ui/load-more";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { finiteMoney } from "@/lib/currency-map";
import type { ReportClientRow } from "@/lib/types";
import { cn, formatCurrency, formatMoney } from "@/lib/utils";

function orderDayLabel(iso: string): string {
  const [y, m, d] = iso.split("-");
  return `${d}.${m}.${y}`;
}

function OrderPaymentBadge({ order }: { order: ReportClientRow["order_list"][number] }) {
  const remaining = finiteMoney(order.remaining_amount);
  const paid = finiteMoney(order.paid_amount);
  if (remaining <= 0) return <Badge tone="success">Оплачен</Badge>;
  if (order.is_debt) {
    return (
      <Badge tone={paid > 0 ? "warning" : "destructive"}>
        {paid > 0 ? "Частично оплачен" : "В долгу"} · осталось {formatCurrency(remaining, order.currency)}
      </Badge>
    );
  }
  return (
    <Badge tone="warning">
      {paid > 0 ? "Частично оплачен" : "Ожидает оплаты"} · осталось {formatCurrency(remaining, order.currency)}
    </Badge>
  );
}

/** Отчёт по клиентам: строка — итоги клиента, по клику раскрываются заказы. */
export function ClientsTable({ clients }: { clients: ReportClientRow[] }) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  // Отчёт приходит целиком — рендерим лениво, чтобы сотни клиентов не
  // раскатывались в одну простыню.
  const [limit, setLimit] = useState(25);
  const visible = clients.slice(0, limit);

  function toggle(id: number) {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <div className="overflow-x-auto rounded-xl border border-[var(--border)] bg-[var(--card)] shadow-card">
      <Table>
        <THead>
          <TR>
            <TH>Клиент</TH>
            <TH className="text-right">Заказов</TH>
            <TH className="text-right">Мешков</TH>
            <TH className="text-right">Отгружено</TH>
            <TH className="text-right">Погашено</TH>
            <TH className="text-right">Остаток долга</TH>
            <TH className="text-right">К оплате без отсрочки</TH>
          </TR>
        </THead>
        <TBody>
          {clients.length === 0 ? (
            <TR>
              <TD colSpan={7} className="py-14 text-center text-sm text-[var(--muted-foreground)]">
                Здесь пусто
              </TD>
            </TR>
          ) : (
            visible.map((client) => {
              const open = expanded.has(client.id);
              return (
                <Fragment key={client.id}>
                  <TR
                    className="cursor-pointer transition-colors hover:bg-[var(--muted)]/40"
                    onClick={() => toggle(client.id)}
                  >
                    <TD>
                      <button
                        type="button"
                        aria-expanded={open}
                        onClick={(event) => {
                          event.stopPropagation();
                          toggle(client.id);
                        }}
                        className="flex items-center gap-1.5 text-left font-medium"
                      >
                        <ChevronRight
                          className={cn(
                            "size-4 shrink-0 text-[var(--muted-foreground)] transition-transform",
                            open && "rotate-90",
                          )}
                        />
                        {client.name}
                      </button>
                    </TD>
                    <TD className="text-right tabular-nums">{client.orders}</TD>
                    <TD className="text-right tabular-nums">{formatMoney(client.bags)}</TD>
                    <TD className="text-right font-semibold tabular-nums">
                      <CurrencyAmounts byCurrency={client.revenue_by_currency} fallbackAmount="0" />
                    </TD>
                    <TD className="text-right tabular-nums text-[var(--success)]">
                      <CurrencyAmounts byCurrency={client.paid_amount_by_currency} fallbackAmount="0" />
                    </TD>
                    <TD className="text-right tabular-nums text-[var(--destructive)]">
                      <CurrencyAmounts byCurrency={client.debt_amount_by_currency} fallbackAmount="0" />
                    </TD>
                    <TD className="text-right tabular-nums text-[var(--warning)]">
                      <CurrencyAmounts byCurrency={client.awaiting_amount_by_currency} fallbackAmount="0" />
                    </TD>
                  </TR>
                  {open && (
                    <TR className="bg-[var(--muted)]/30">
                      <TD colSpan={7} className="p-0">
                        <div className="flex flex-col gap-1 px-4 py-3 pl-10">
                          {client.order_list.map((order) => (
                            <div
                              key={order.id}
                              className="flex flex-wrap items-baseline gap-x-4 gap-y-1 rounded-lg px-2 py-1.5 text-[13px] hover:bg-[var(--card)]"
                            >
                              <Link
                                href={`/orders/${order.id}`}
                                className="font-medium text-[var(--ring)] hover:underline"
                              >
                                №{order.id}
                              </Link>
                              <span className="tabular-nums text-[var(--muted-foreground)]">
                                {orderDayLabel(order.date)}
                              </span>
                              <span className="tabular-nums">{formatMoney(order.bags)} меш.</span>
                              <span className="ml-auto font-semibold tabular-nums">
                                {formatCurrency(order.total, order.currency)}
                              </span>
                              <OrderPaymentBadge order={order} />
                            </div>
                          ))}
                          <Link
                            href={`/clients/${client.id}`}
                            className="mt-1 self-start px-2 text-xs font-medium text-[var(--ring)] hover:underline"
                          >
                            Открыть клиента
                          </Link>
                        </div>
                      </TD>
                    </TR>
                  )}
                </Fragment>
              );
            })
          )}
        </TBody>
      </Table>
      <LoadMore
        shown={visible.length}
        total={clients.length}
        hasMore={clients.length > visible.length}
        onClick={() => setLimit((current) => current + 25)}
      />
    </div>
  );
}
