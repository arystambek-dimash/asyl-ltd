"use client";
import { useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { ErrorAlert } from "@/components/ui/data-state";
import { useApi } from "@/lib/use-api";
import { api, apiError } from "@/lib/api";
import { TrainFront, Package, User } from "lucide-react";
import type { Order } from "@/lib/types";

function TrainPageInner() {
  const { data: orders, error, reload } = useApi<Order[]>("/orders/train/queue/");
  const queue = orders ?? [];

  return (
    <AppShell title="Поезда" section="Работа" description="Очередь поездов на загрузку: старт сессии, подсчёт мешков, завершение.">
      <div className="mb-4 flex items-center gap-2 text-sm">
        <TrainFront className="size-4 text-[var(--muted-foreground)]" />
        <span className="text-[var(--muted-foreground)]">В очереди:</span>
        <span className="font-semibold">{queue.length}</span>
      </div>

      {error && !orders ? (
        <ErrorAlert message={error} onRetry={reload} />
      ) : queue.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center text-sm text-[var(--muted-foreground)]">
            Нет поездов в очереди. Заказы появляются здесь после подтверждения.
          </CardContent>
        </Card>
      ) : (
        <div className="flex flex-col gap-3">
          {queue.map((o) => <TrainRow key={o.id} order={o} onChange={reload} />)}
        </div>
      )}
    </AppShell>
  );
}

function TrainRow({ order, onChange }: { order: Order; onChange: () => void }) {
  const [bags, setBags] = useState(order.bags_loaded ? String(order.bags_loaded) : "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function act(action: string, extra?: Record<string, unknown>) {
    setBusy(true); setError("");
    try {
      await api.post(`/orders/${order.id}/train/`, { action, ...extra });
      onChange();
    } catch (e) { setError(apiError(e)); } finally { setBusy(false); }
  }

  const started = order.status === "loading";

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between gap-2 pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <TrainFront className="size-4 text-[var(--muted-foreground)]" />
          Заказ #{order.id}
          <Badge tone={started ? "warning" : "muted"} dot={started}>
            {started ? "Загрузка идёт" : "Ожидает старта"}
          </Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div className="flex items-center gap-2 text-sm">
          <User className="size-4 text-[var(--muted-foreground)]" />
          <span className="font-medium">{order.client_name || "—"}</span>
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

        {!started ? (
          <Button disabled={busy} onClick={() => act("start")}>Начать загрузку</Button>
        ) : (
          <div className="flex flex-col gap-2 border-t pt-3">
            <Label>Количество мешков</Label>
            <div className="flex gap-2">
              <Input type="number" min={0} placeholder="Мешков" value={bags}
                onChange={(e) => setBags(e.target.value)} />
              <Button size="sm" variant="outline" disabled={busy || !bags}
                onClick={() => act("count", { bags: Number(bags) })}>Сохранить</Button>
            </div>
            <Button disabled={busy || !bags}
              onClick={() => act("finish")}>Завершить и отгрузить</Button>
          </div>
        )}
        {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
      </CardContent>
    </Card>
  );
}

export default function TrainPage() {
  return <RequirePerm perm="train.view" title="Поезда"><TrainPageInner /></RequirePerm>;
}
