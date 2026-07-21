"use client";
import Link from "next/link";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/status-badge";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { useApi } from "@/lib/use-api";
import { formatPortalMoney } from "@/lib/utils";
import { Plus } from "lucide-react";
import type { PortalOrder } from "@/lib/types";

export default function PortalOrdersPage() {
  const { data: orders, loading } = useApi<PortalOrder[]>("/portal/orders/");
  return (
    <AppShell title="Мои заказы" portal
      actions={
        <Link href="/portal/orders/new"><Button size="sm" aria-label="Новый заказ"><Plus className="size-4" /> <span className="hidden sm:inline">Новый заказ</span></Button></Link>
      }>
      <div className="mb-4">
        <p className="text-sm text-[var(--muted-foreground)]">{orders?.length ?? 0} заказов</p>
      </div>
      <Card>
        <CardContent className="pt-6">
          {loading ? (
            <p className="py-6 text-center text-sm text-[var(--muted-foreground)]">Загрузка…</p>
          ) : (orders ?? []).length === 0 ? (
            <p className="py-6 text-center text-sm text-[var(--muted-foreground)]">У вас пока нет заказов.</p>
          ) : (
            <Table>
              <THead><TR><TH>№</TH><TH>Сумма</TH><TH>Оплачено</TH><TH>Статус</TH></TR></THead>
              <TBody>
                {(orders ?? []).map((o) => (
                  <TR key={o.id}>
                    <TD className="font-medium">
                      <Link href={`/portal/orders/${o.id}`} className="underline">#{o.id}</Link>
                    </TD>
                    <TD className={o.total_amount == null ? "text-[var(--muted-foreground)]" : "tabular-nums"}>
                      {formatPortalMoney(o.total_amount, o.currency)}
                    </TD>
                    <TD className={o.paid_total == null
                      ? "text-[var(--muted-foreground)]"
                      : "tabular-nums text-[var(--muted-foreground)]"}>
                      {formatPortalMoney(o.paid_total, o.currency)}
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
