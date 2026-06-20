"use client";
import { AppShell } from "@/components/layout/app-shell";
import { KpiCard } from "@/components/kpi-card";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { useApi } from "@/lib/use-api";
import { formatMoney } from "@/lib/utils";
import type { Order, StockItem } from "@/lib/types";

export default function ReportsPage() {
  const { data: orders } = useApi<Order[]>("/orders/");
  const { data: stock } = useApi<StockItem[]>("/stock/");
  const list = orders ?? [];

  const revenue = list.reduce((s, o) => s + Number(o.paid_total), 0);
  const shipped = list.filter((o) => o.status === "shipped").length;
  const totalBags = (stock ?? []).reduce((s, i) => s + i.bags, 0);

  const debtors = list.filter((o) => !o.is_fully_paid && o.status !== "cancelled");

  return (
    <AppShell title="Отчёты">
      <div className="grid grid-cols-3 gap-4">
        <KpiCard label="Поступило оплат" value={`${formatMoney(revenue)} ₸`} sub="всего по заказам" />
        <KpiCard label="Отгружено заказов" value={String(shipped)} sub="завершено" />
        <KpiCard label="Остаток на складе" value={`${formatMoney(totalBags)}`} sub="мешков" />
      </div>
      <Card className="mt-6">
        <CardHeader><CardTitle>Дебиторская задолженность</CardTitle></CardHeader>
        <CardContent>
          {debtors.length === 0 ? (
            <p className="py-6 text-center text-sm text-[var(--muted-foreground)]">Долгов нет.</p>
          ) : (
            <Table>
              <THead><TR><TH>Заказ</TH><TH>Сумма</TH><TH>Оплачено</TH><TH>Остаток</TH></TR></THead>
              <TBody>
                {debtors.map((o) => (
                  <TR key={o.id}>
                    <TD className="font-medium">#{o.id}</TD>
                    <TD className="tabular-nums">{formatMoney(o.total_amount)} ₸</TD>
                    <TD className="tabular-nums">{formatMoney(o.paid_total)} ₸</TD>
                    <TD className="tabular-nums font-medium text-[var(--destructive)]">
                      {formatMoney(Number(o.total_amount) - Number(o.paid_total))} ₸
                    </TD>
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
