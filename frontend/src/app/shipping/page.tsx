"use client";
import { useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { formatPlate } from "@/components/ui/license-plate-input";
import { StatusBadge } from "@/components/status-badge";
import { useApi } from "@/lib/use-api";
import { api, apiError } from "@/lib/api";
import { formatMoney } from "@/lib/utils";
import {
  Truck, ChevronDown, User, Phone, Package, Scale, CheckCircle2, Circle,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { Order } from "@/lib/types";

const QUEUE_STATUSES = ["confirmed", "arrived", "loading", "loaded"];

// шаги жизненного цикла на посту: прибытие → начальный вес → загрузка → выезд
// (оплата после отгрузки, на посту её нет)
const STEPS = [
  { key: "confirmed", label: "Прибытие" },
  { key: "arrived", label: "Начальный вес" },
  { key: "loading", label: "Загрузка" },
  { key: "shipped", label: "Выезд" },
];
function stepIndex(status: string) {
  if (status === "confirmed") return 0; // прибыла, ждём взвешивания
  if (status === "arrived") return 1;   // вес КАМАЗа зафиксирован, готова грузиться
  if (status === "loading") return 2;   // идёт загрузка
  if (status === "loaded") return 3;    // загрузка завершена, ждём выезд
  if (status === "shipped") return 3;
  return 0;
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
  const [openId, setOpenId] = useState<number | null>(null);

  const queue = (orders ?? []).filter((o) => QUEUE_STATUSES.includes(o.status));

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
            Нет машин в очереди. Заказы появляются здесь после подтверждения.
          </CardContent>
        </Card>
      ) : (
        <div className="flex flex-col gap-3">
          {queue.map((o) => (
            <QueueRow key={o.id} order={o}
              open={openId === o.id}
              onToggle={() => setOpenId(openId === o.id ? null : o.id)}
              onChange={reload} />
          ))}
        </div>
      )}
    </AppShell>
  );
}

// Сравнение фактического веса груза (выезд − въезд) с ожидаемым (мешки × вес).
// Авторитетный расчёт делает бэкенд (eventlog); здесь — предпросмотр для оператора.

function QueueRow({
  order, open, onToggle, onChange,
}: {
  order: Order; open: boolean;
  onToggle: () => void; onChange: () => void;
}) {
  const [weighIn, setWeighIn] = useState("");
  const [bags, setBags] = useState(order.bags_loaded ? String(order.bags_loaded) : "");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

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
            <span className="font-semibold tabular-nums">{order.truck_number ? formatPlate(order.truck_number) : `Заказ #${order.id}`}</span>
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
              {order.weigh_in_kg && (
                <div className="flex flex-wrap gap-4 border-t pt-3 text-sm">
                  <span className="flex items-center gap-1.5">
                    <Scale className="size-4 text-[var(--muted-foreground)]" />
                    Вес КАМАЗа: <span className="tabular-nums font-medium">{formatMoney(order.weigh_in_kg)} кг</span>
                  </span>
                </div>
              )}
            </div>

            {/* действие текущего шага */}
            <div className="flex flex-col gap-3 rounded-lg border bg-[var(--card)] p-4">
              {/* Прибытие */}
              {order.status === "confirmed" && (
                <>
                  <Label>Прибытие машины</Label>
                  <div className="text-sm text-[var(--muted-foreground)]">
                    Номер: <b className="text-[var(--foreground)] tabular-nums">
                      {order.truck_number ? formatPlate(order.truck_number) : "—"}</b>
                  </div>
                  <Input type="number" placeholder="Вес КАМАЗа, кг" value={weighIn}
                    onChange={(e) => setWeighIn(e.target.value)} />
                  <Button disabled={busy || !weighIn}
                    onClick={() => act(() => api.post(`/orders/${order.id}/arrive/`, {
                      weigh_in_kg: weighIn,
                    }))}>
                    Принять машину
                  </Button>
                </>
              )}

              {/* Машина въехала — оператор сразу начинает загрузку (оплата после отгрузки). */}
              {order.status === "arrived" && (
                <>
                  <Label>Загрузка</Label>
                  <Input type="number" min={0} placeholder="Количество мешков" value={bags}
                    onChange={(e) => setBags(e.target.value)} />
                  <Button disabled={busy || !bags}
                    onClick={() => act(() => api.post(`/orders/${order.id}/load/`, { bags }))}>
                    Начать загрузку
                  </Button>
                </>
              )}

              {/* Идёт загрузка: можно обновить количество и завершить. */}
              {order.status === "loading" && (
                <>
                  <Label>Загрузка</Label>
                  <Input type="number" min={0} placeholder="Количество мешков" value={bags}
                    onChange={(e) => setBags(e.target.value)} />
                  <Button disabled={busy || !bags} variant="outline"
                    onClick={() => act(() => api.post(`/orders/${order.id}/load/`, { bags }))}>
                    Сохранить мешки
                  </Button>
                  <Button disabled={busy}
                    onClick={() => act(() => api.post(`/orders/${order.id}/finish-loading/`, {}))}>
                    Загрузка завершена
                  </Button>
                </>
              )}

              {/* Загрузка завершена: вес выезда + сравнение. */}
              {order.status === "loaded" && (
                <>
                  <Label>Выезд</Label>
                  <div className="text-sm text-[var(--muted-foreground)]">
                    Посчитано мешков: <b className="text-[var(--foreground)] tabular-nums">
                      {order.bags_loaded ?? 0}</b>
                  </div>
                  <Button disabled={busy}
                    onClick={() => act(() => api.post(`/orders/${order.id}/ship/`, {}))}>
                    Отгрузить (выезд)
                  </Button>
                </>
              )}

              {order.status === "shipped" && (
                <>
                  <Label>Отгружено</Label>
                  <div className="text-sm">
                    Вес КАМАЗа: <b className="tabular-nums text-[var(--success)]">
                      {order.weigh_in_kg ? `${formatMoney(order.weigh_in_kg)} кг` : "—"}</b>
                  </div>
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
