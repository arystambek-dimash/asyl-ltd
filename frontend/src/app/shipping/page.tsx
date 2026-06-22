"use client";
import { useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { StatusBadge } from "@/components/status-badge";
import { useApi } from "@/lib/use-api";
import { can } from "@/lib/can";
import { useAuth } from "@/store/auth";
import { api, apiError } from "@/lib/api";
import { formatMoney } from "@/lib/utils";
import {
  Truck, ChevronDown, User, Phone, Package, Scale, CheckCircle2, Circle,
  AlertTriangle,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { Order } from "@/lib/types";

const QUEUE_STATUSES = ["paid", "arrived", "loading"];

// шаги жизненного цикла на посту
const STEPS = [
  { key: "paid", label: "Оплачен" },
  { key: "arrived", label: "Прибытие" },
  { key: "loading", label: "Загрузка" },
  { key: "shipped", label: "Выезд" },
];
function stepIndex(status: string) {
  if (status === "confirmed") return 0;
  const i = STEPS.findIndex((s) => s.key === status);
  return i < 0 ? 0 : i;
}

function Stepper({ status, compact = false }: { status: string; compact?: boolean }) {
  const current = stepIndex(status);
  return (
    <div className="flex items-center">
      {STEPS.map((s, i) => {
        const done = i < current;
        const active = i === current;
        return (
          <div key={s.key} className="flex items-center">
            <div className="flex flex-col items-center gap-1">
              {done ? (
                <CheckCircle2 className={cn(compact ? "size-3.5" : "size-5", "text-[var(--success)]")} />
              ) : (
                <Circle className={cn(compact ? "size-3.5" : "size-5",
                  active ? "text-[var(--primary)]" : "text-[var(--muted-foreground)]/40")}
                  {...(active ? { fill: "currentColor", fillOpacity: 0.15 } : {})} />
              )}
              {!compact && (
                <span className={cn("text-[11px]",
                  active ? "font-medium text-[var(--foreground)]"
                    : done ? "text-[var(--success)]" : "text-[var(--muted-foreground)]")}>
                  {s.label}
                </span>
              )}
            </div>
            {i < STEPS.length - 1 && (
              <div className={cn(compact ? "mx-1 w-4" : "mx-2 w-10 mb-4", "h-0.5 rounded-full",
                i < current ? "bg-[var(--success)]" : "bg-[var(--border)]")} />
            )}
          </div>
        );
      })}
    </div>
  );
}

export default function ShippingPage() {
  const { data: orders, reload } = useApi<Order[]>("/orders/");
  const { me } = useAuth();
  const isBoss = can(me, "shipping.debt_override");
  const [openId, setOpenId] = useState<number | null>(null);

  const queue = (orders ?? []).filter((o) =>
    QUEUE_STATUSES.includes(o.status) || (o.status === "confirmed" && isBoss)
  );

  return (
    <AppShell title="Пост отгрузки" section="Работа" description="Очередь машин на отгрузку: прибытие, загрузка, выезд и расчёт нетто по весам.">
      <div className="mb-4 flex items-center gap-2 text-sm">
        <Truck className="size-4 text-[var(--muted-foreground)]" />
        <span className="text-[var(--muted-foreground)]">Очередь машин:</span>
        <span className="font-semibold">{queue.length}</span>
      </div>

      {queue.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center text-sm text-[var(--muted-foreground)]">
            Нет машин в очереди. Заказы появляются здесь после оплаты.
          </CardContent>
        </Card>
      ) : (
        <div className="flex flex-col gap-3">
          {queue.map((o) => (
            <QueueRow key={o.id} order={o} isBoss={!!isBoss}
              open={openId === o.id}
              onToggle={() => setOpenId(openId === o.id ? null : o.id)}
              onChange={reload} />
          ))}
        </div>
      )}
    </AppShell>
  );
}

function QueueRow({
  order, isBoss, open, onToggle, onChange,
}: {
  order: Order; isBoss: boolean; open: boolean;
  onToggle: () => void; onChange: () => void;
}) {
  const [truck, setTruck] = useState(order.truck_number);
  const [weighIn, setWeighIn] = useState("");
  const [bags, setBags] = useState("");
  const [weighOut, setWeighOut] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const needsPayWarn = !order.is_fully_paid;

  async function act(fn: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await fn(); onChange(); }
    catch (e) { setError(apiError(e)); }
    finally { setBusy(false); }
  }

  return (
    <Card className="overflow-hidden">
      {/* строка (свёрнуто) */}
      <button onClick={onToggle}
        className="flex w-full items-center gap-4 px-5 py-4 text-left transition-colors hover:bg-[var(--muted)]/40">
        <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-[var(--secondary)]">
          <Truck className="size-5 text-[var(--muted-foreground)]" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="font-semibold">{order.truck_number || `Заказ #${order.id}`}</span>
            <span className="text-sm text-[var(--muted-foreground)]">· {order.client_name || "—"}</span>
          </div>
          <div className="text-xs text-[var(--muted-foreground)]">
            #{order.id} · {formatMoney(order.total_amount)} ₸
          </div>
        </div>
        <div className="hidden sm:block"><Stepper status={order.status} compact /></div>
        <StatusBadge status={order.status} />
        <ChevronDown className={cn("size-4 text-[var(--muted-foreground)] transition-transform",
          open && "rotate-180")} />
      </button>

      {/* детали (раскрыто) */}
      {open && (
        <div className="border-t bg-[var(--muted)]/20 px-5 py-5">
          {/* большой stepper */}
          <div className="mb-5 flex justify-center">
            <Stepper status={order.status} />
          </div>

          <div className="grid gap-5 lg:grid-cols-2">
            {/* инфо */}
            <div className="flex flex-col gap-3">
              <div className="flex items-center gap-2 text-sm">
                <User className="size-4 text-[var(--muted-foreground)]" />
                <span className="font-medium">{order.client_name || "—"}</span>
              </div>
              <div className="flex items-center gap-2 text-sm text-[var(--muted-foreground)]">
                <Phone className="size-4" /> {order.client_phone || "—"}
              </div>
              <div className="flex items-start gap-2 text-sm">
                <Package className="mt-0.5 size-4 text-[var(--muted-foreground)]" />
                <div className="flex flex-col gap-0.5">
                  {order.items.map((it, i) => (
                    <span key={i}>{it.product_label || `Товар #${it.product}`}
                      <span className="text-[var(--muted-foreground)]"> × {it.quantity} меш.</span>
                    </span>
                  ))}
                </div>
              </div>
              {(order.weigh_in_kg || order.weigh_out_kg) && (
                <div className="flex flex-wrap gap-4 border-t pt-3 text-sm">
                  {order.weigh_in_kg && (
                    <span className="flex items-center gap-1.5">
                      <Scale className="size-4 text-[var(--muted-foreground)]" />
                      Въезд: <span className="tabular-nums font-medium">{formatMoney(order.weigh_in_kg)} кг</span>
                    </span>
                  )}
                  {order.net_weight_kg && (
                    <span>Нетто: <span className="tabular-nums font-medium text-[var(--success)]">{formatMoney(order.net_weight_kg)} кг</span></span>
                  )}
                </div>
              )}
              {needsPayWarn && order.status !== "shipped" && (
                <div className="flex items-center gap-2 rounded-md bg-[var(--warning)]/12 px-3 py-2 text-xs text-[var(--warning)]">
                  <AlertTriangle className="size-4 shrink-0" />
                  Заказ не оплачен. {isBoss ? "Можно отгрузить в долг." : "Въезд запрещён без оплаты."}
                </div>
              )}
            </div>

            {/* действие текущего шага */}
            <div className="flex flex-col gap-3 rounded-lg border bg-[var(--card)] p-4">
              {(order.status === "paid" || order.status === "confirmed") && (
                <>
                  <Label>Прибытие машины</Label>
                  <Input placeholder="Номер машины" value={truck} onChange={(e) => setTruck(e.target.value)} />
                  <Input type="number" placeholder="Вес въезда, кг" value={weighIn}
                    onChange={(e) => setWeighIn(e.target.value)} />
                  <Button disabled={busy || !weighIn}
                    onClick={() => act(() => api.post(`/orders/${order.id}/arrive/`, {
                      truck_number: truck, weigh_in_kg: weighIn,
                      debt_override: needsPayWarn && isBoss,
                    }))}>
                    {needsPayWarn && isBoss ? "Принять (в долг)" : "Принять машину"}
                  </Button>
                </>
              )}

              {order.status === "arrived" && (
                <>
                  <Label>Загрузка</Label>
                  <Input type="number" placeholder="Загружено мешков" value={bags}
                    onChange={(e) => setBags(e.target.value)} />
                  <Button disabled={busy || !bags}
                    onClick={() => act(() => api.post(`/orders/${order.id}/load/`, { bags }))}>
                    Зафиксировать загрузку
                  </Button>
                </>
              )}

              {order.status === "loading" && (
                <>
                  <Label>Выезд</Label>
                  <Input type="number" placeholder="Вес выезда, кг" value={weighOut}
                    onChange={(e) => setWeighOut(e.target.value)} />
                  <Button disabled={busy || !weighOut}
                    onClick={() => act(() => api.post(`/orders/${order.id}/ship/`, { weigh_out_kg: weighOut }))}>
                    Отгрузить (выезд)
                  </Button>
                </>
              )}

              {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
            </div>
          </div>
        </div>
      )}
    </Card>
  );
}
