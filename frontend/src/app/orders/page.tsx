"use client";
import Link from "next/link";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/status-badge";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { useApi } from "@/lib/use-api";
import { useAuth } from "@/store/auth";
import { formatMoney } from "@/lib/utils";
import { Plus } from "lucide-react";
import type { Order } from "@/lib/types";

export default function OrdersPage() {
  const { data: orders, loading } = useApi<Order[]>("/orders/");
  const { me } = useAuth();
  const canCreate = me?.is_superuser || me?.roles.includes("manager");

  return (
    <AppShell title="Заказы">
      <div className="mb-4 flex items-center justify-between">
        <p className="text-sm text-[var(--muted-foreground)]">
          {orders?.length ?? 0} заказов
        </p>
        {canCreate && (
          <Link href="/orders/new">
            <Button size="sm"><Plus className="size-4" /> Новый заказ</Button>
          </Link>
        )}
      </div>
      <Card>
        <CardContent className="pt-6">
          {loading ? (
            <p className="py-6 text-center text-sm text-[var(--muted-foreground)]">Загрузка…</p>
          ) : (
            <Table>
              <THead>
                <TR><TH>№</TH><TH>Клиент</TH><TH>Машина</TH><TH>Сумма</TH><TH>Оплачено</TH><TH>Статус</TH></TR>
              </THead>
              <TBody>
                {(orders ?? []).map((o) => (
                  <TR key={o.id} className="cursor-pointer">
                    <TD className="font-medium">
                      <Link href={`/orders/${o.id}`} className="hover:underline">#{o.id}</Link>
                    </TD>
                    <TD>Клиент #{o.client}</TD>
                    <TD>{o.truck_number || "—"}</TD>
                    <TD className="tabular-nums">{formatMoney(o.total_amount)} ₸</TD>
                    <TD className="tabular-nums text-[var(--muted-foreground)]">{formatMoney(o.paid_total)} ₸</TD>
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
