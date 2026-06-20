"use client";
import { useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { StatusBadge } from "@/components/status-badge";
import { useApi } from "@/lib/use-api";
import { useAuth } from "@/store/auth";
import { api, apiError } from "@/lib/api";
import { formatMoney } from "@/lib/utils";
import type { Order } from "@/lib/types";

const STAGES = ["paid", "arrived", "loading"];

export default function ShippingPage() {
  const { data: orders, reload } = useApi<Order[]>("/orders/");
  const { me } = useAuth();
  const isBoss = me?.is_superuser || me?.roles.includes("boss");

  const queue = (orders ?? []).filter((o) =>
    STAGES.includes(o.status) || (o.status === "confirmed" && isBoss)
  );

  return (
    <AppShell title="Пост отгрузки">
      <p className="mb-4 text-sm text-[var(--muted-foreground)]">
        Очередь машин: {queue.length}
      </p>
      <div className="grid grid-cols-2 gap-6">
        {queue.map((o) => (
          <ShippingCard key={o.id} order={o} isBoss={!!isBoss} onChange={reload} />
        ))}
        {queue.length === 0 && (
          <p className="col-span-2 py-10 text-center text-sm text-[var(--muted-foreground)]">
            Нет машин в очереди. Заказы появляются здесь после оплаты.
          </p>
        )}
      </div>
    </AppShell>
  );
}

function ShippingCard({ order, isBoss, onChange }: { order: Order; isBoss: boolean; onChange: () => void }) {
  const [truck, setTruck] = useState(order.truck_number);
  const [weighIn, setWeighIn] = useState("");
  const [bags, setBags] = useState("");
  const [weighOut, setWeighOut] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function act(fn: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await fn(); onChange(); }
    catch (e) { setError(apiError(e)); }
    finally { setBusy(false); }
  }

  const needsPayWarn = !order.is_fully_paid;

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle>Заказ #{order.id}</CardTitle>
        <StatusBadge status={order.status} />
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div className="flex justify-between text-sm">
          <span className="text-[var(--muted-foreground)]">Сумма</span>
          <span className="tabular-nums">{formatMoney(order.total_amount)} ₸</span>
        </div>

        {(order.status === "paid" || order.status === "confirmed") && (
          <div className="flex flex-col gap-2 border-t pt-3">
            <Label>Прибытие машины</Label>
            <Input placeholder="Номер машины" value={truck} onChange={(e) => setTruck(e.target.value)} />
            <Input type="number" placeholder="Вес въезда, кг" value={weighIn}
              onChange={(e) => setWeighIn(e.target.value)} />
            {needsPayWarn && (
              <p className="text-xs text-[var(--warning)]">
                Заказ не оплачен. {isBoss ? "Можно разрешить в долг." : "Въезд запрещён без оплаты."}
              </p>
            )}
            <Button size="sm" disabled={busy || !weighIn}
              onClick={() => act(() => api.post(`/orders/${order.id}/arrive/`, {
                truck_number: truck, weigh_in_kg: weighIn,
                debt_override: needsPayWarn && isBoss,
              }))}>
              {needsPayWarn && isBoss ? "Принять (в долг)" : "Принять машину"}
            </Button>
          </div>
        )}

        {order.status === "arrived" && (
          <div className="flex flex-col gap-2 border-t pt-3">
            <Label>Загрузка</Label>
            <Input type="number" placeholder="Загружено мешков" value={bags}
              onChange={(e) => setBags(e.target.value)} />
            <Button size="sm" disabled={busy || !bags}
              onClick={() => act(() => api.post(`/orders/${order.id}/load/`, { bags }))}>
              Зафиксировать загрузку
            </Button>
          </div>
        )}

        {order.status === "loading" && (
          <div className="flex flex-col gap-2 border-t pt-3">
            <Label>Выезд</Label>
            <Input type="number" placeholder="Вес выезда, кг" value={weighOut}
              onChange={(e) => setWeighOut(e.target.value)} />
            <Button size="sm" disabled={busy || !weighOut}
              onClick={() => act(() => api.post(`/orders/${order.id}/ship/`, { weigh_out_kg: weighOut }))}>
              Отгрузить (выезд)
            </Button>
          </div>
        )}

        {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
      </CardContent>
    </Card>
  );
}
