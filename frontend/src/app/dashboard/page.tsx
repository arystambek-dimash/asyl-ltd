"use client";
import { AppShell } from "@/components/layout/app-shell";
import { KpiCard } from "@/components/kpi-card";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { StatusBadge } from "@/components/status-badge";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { useApi } from "@/lib/use-api";
import { formatMoney } from "@/lib/utils";
import type { Order, StockItem } from "@/lib/types";

export default function DashboardPage() {
  const { data: orders } = useApi<Order[]>("/orders/");
  const { data: stock } = useApi<StockItem[]>("/stock/");

  const list = orders ?? [];
  const activeOrders = list.filter((o) => !["shipped", "cancelled"].includes(o.status));
  const shippedToday = list.filter((o) => o.status === "shipped");
  const totalBags = (stock ?? []).reduce((s, i) => s + i.bags, 0);
  const debt = list
    .filter((o) => !o.is_fully_paid && o.status !== "cancelled")
    .reduce((s, o) => s + (Number(o.total_amount) - Number(o.paid_total)), 0);

  return (
    <AppShell title="Дашборд">
      <div className="grid grid-cols-4 gap-4">
        <KpiCard label="Активные заказы" value={String(activeOrders.length)}
          sub="в работе" />
        <KpiCard label="Мешков на складе" value={formatMoney(totalBags)}
          sub="всего по сортам" />
        <KpiCard label="Отгружено" value={String(shippedToday.length)}
          sub="завершённых заказов" />
        <KpiCard label="Дебиторка" value={`${formatMoney(debt)} ₸`}
          sub="неоплаченный остаток" />
      </div>

      <Card className="mt-6">
        <CardHeader>
          <CardTitle>Заказы в работе</CardTitle>
        </CardHeader>
        <CardContent>
          {activeOrders.length === 0 ? (
            <p className="py-6 text-center text-sm text-[var(--muted-foreground)]">
              Нет активных заказов.
            </p>
          ) : (
            <Table>
              <THead>
                <TR>
                  <TH>№</TH><TH>Машина</TH><TH>Сумма</TH>
                  <TH>Оплата</TH><TH>Статус</TH>
                </TR>
              </THead>
              <TBody>
                {activeOrders.map((o) => (
                  <TR key={o.id}>
                    <TD className="font-medium">#{o.id}</TD>
                    <TD>{o.truck_number || "—"}</TD>
                    <TD className="tabular-nums">{formatMoney(o.total_amount)} ₸</TD>
                    <TD className="tabular-nums text-[var(--muted-foreground)]">
                      {formatMoney(o.paid_total)} ₸
                    </TD>
                    <TD><StatusBadge status={o.status} /></TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </AppShell>
  );
}
