"use client";
import { use, useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { StatusBadge } from "@/components/status-badge";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { useApi } from "@/lib/use-api";
import { useAuth } from "@/store/auth";
import { api, apiError } from "@/lib/api";
import { formatMoney } from "@/lib/utils";
import type { Order, Payment } from "@/lib/types";

export default function OrderDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { me } = useAuth();
  const { data: order, reload } = useApi<Order>(`/orders/${id}/`);
  const { data: payments, reload: reloadPay } = useApi<Payment[]>(null);
  const [amount, setAmount] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const isManager = me?.is_superuser || me?.roles.includes("manager");
  const isAccountant = me?.is_superuser || me?.roles.includes("accountant");

  async function act(fn: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await fn(); await reload(); reloadPay(); }
    catch (e) { setError(apiError(e)); }
    finally { setBusy(false); }
  }

  if (!order) return <AppShell title="Заказ"><p className="text-sm text-[var(--muted-foreground)]">Загрузка…</p></AppShell>;

  const remaining = Number(order.total_amount) - Number(order.paid_total);

  return (
    <AppShell title={`Заказ #${order.id}`}>
      <div className="grid grid-cols-3 gap-6">
        <div className="col-span-2 flex flex-col gap-6">
          <Card>
            <CardHeader className="flex-row items-center justify-between">
              <CardTitle>Позиции</CardTitle>
              <StatusBadge status={order.status} />
            </CardHeader>
            <CardContent>
              <Table>
                <THead><TR><TH>Товар</TH><TH>Мешков</TH></TR></THead>
                <TBody>
                  {order.items.map((it, i) => (
                    <TR key={i}><TD>Товар #{it.product}</TD><TD className="tabular-nums">{it.quantity}</TD></TR>
                  ))}
                </TBody>
              </Table>
              <div className="mt-4 flex items-center justify-between border-t pt-4">
                <span className="text-sm text-[var(--muted-foreground)]">Сумма заказа</span>
                <span className="text-lg font-bold tabular-nums">{formatMoney(order.total_amount)} ₸</span>
              </div>
            </CardContent>
          </Card>
        </div>

        <div className="flex flex-col gap-6">
          <Card>
            <CardHeader><CardTitle>Оплата</CardTitle></CardHeader>
            <CardContent className="flex flex-col gap-3">
              <div className="flex justify-between text-sm">
                <span className="text-[var(--muted-foreground)]">Оплачено</span>
                <span className="tabular-nums">{formatMoney(order.paid_total)} ₸</span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-[var(--muted-foreground)]">Остаток</span>
                <span className="tabular-nums font-medium">{formatMoney(remaining)} ₸</span>
              </div>
              {isAccountant && remaining > 0 && (
                <div className="flex gap-2 border-t pt-3">
                  <Input type="number" placeholder="Сумма" value={amount}
                    onChange={(e) => setAmount(e.target.value)} />
                  <Button size="sm" disabled={busy || !amount}
                    onClick={() => act(async () => {
                      await api.post(`/orders/${order.id}/payments/`, { amount });
                      setAmount("");
                    })}>Внести</Button>
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle>Действия</CardTitle></CardHeader>
            <CardContent className="flex flex-col gap-2">
              {isManager && order.status === "draft" && (
                <Button variant="outline" disabled={busy}
                  onClick={() => act(() => api.post(`/orders/${order.id}/confirm/`))}>
                  Подтвердить заказ
                </Button>
              )}
              {order.status === "paid" && (
                <p className="text-sm text-[var(--success)]">Заказ оплачен, готов к отгрузке.</p>
              )}
              {order.status === "shipped" && (
                <p className="text-sm text-[var(--muted-foreground)]">Заказ отгружен.</p>
              )}
              {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
            </CardContent>
          </Card>
        </div>
      </div>
    </AppShell>
  );
}
