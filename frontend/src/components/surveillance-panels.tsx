"use client";
import { Camera, Bell, Activity, Truck } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { StatusBadge } from "@/components/status-badge";
import { useApi } from "@/lib/use-api";
import { formatMoney, cn } from "@/lib/utils";
import { WAREHOUSE_CAMERAS } from "@/components/camera-wall";
import type { Order, StockItem, EventLog } from "@/lib/types";

function Panel({
  title, icon: Icon, children,
}: { title: string; icon: React.ElementType; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border bg-[var(--card)] shadow-sm">
      <div className="flex items-center gap-2 border-b px-4 py-3 text-sm font-semibold">
        <Icon className="size-4 text-[var(--muted-foreground)]" />
        {title}
      </div>
      <div className="p-4">{children}</div>
    </div>
  );
}

const TYPE_LABELS: Record<string, string> = {
  status: "Статус", payment: "Оплата", receipt: "Приёмка", arrival: "Прибытие",
  loading: "Загрузка", shipment: "Отгрузка", debt_override: "Долг", stock_adjust: "Склад",
};

export function SurveillancePanels() {
  const { data: orders } = useApi<Order[]>("/orders/");
  const { data: stock } = useApi<StockItem[]>("/stock/");
  const { data: events } = useApi<EventLog[]>("/events/");

  const list = orders ?? [];
  const active = list.filter((o) => !["shipped", "cancelled"].includes(o.status));
  const queue = list.filter((o) => ["arrived", "loading"].includes(o.status));
  const shipped = list.filter((o) => o.status === "shipped").length;
  const totalBags = (stock ?? []).reduce((s, i) => s + i.bags, 0);
  const debt = list.filter((o) => !o.is_fully_paid && o.status !== "cancelled")
    .reduce((s, o) => s + (Number(o.total_amount) - Number(o.paid_total)), 0);
  const onlineCount = WAREHOUSE_CAMERAS.filter((c) => c.url).length;

  return (
    <div className="flex flex-col gap-4">
      {/* KPI */}
      <Panel title="Сводка" icon={Activity}>
        <div className="grid grid-cols-2 gap-3">
          {[
            { l: "Активные", v: String(active.length) },
            { l: "Мешков", v: formatMoney(totalBags) },
            { l: "Отгружено", v: String(shipped) },
            { l: "Дебиторка", v: `${formatMoney(debt)} ₸` },
          ].map((k) => (
            <div key={k.l} className="rounded-lg border bg-[var(--secondary)]/40 p-3">
              <div className="text-[10px] font-semibold uppercase tracking-[0.1em] text-[var(--muted-foreground)]">{k.l}</div>
              <div className="mt-1 text-lg font-bold tabular-nums">{k.v}</div>
            </div>
          ))}
        </div>
      </Panel>

      {/* Camera status */}
      <Panel title="Статус камер" icon={Camera}>
        <ul className="flex flex-col gap-2">
          {WAREHOUSE_CAMERAS.map((c) => (
            <li key={c.id} className="flex items-center justify-between text-sm">
              <span className="flex items-center gap-2">
                <span className={cn("size-2 rounded-full",
                  c.url ? "bg-[var(--success)]" : "bg-red-500")} />
                {c.zone}
              </span>
              <span className={cn("text-xs font-medium",
                c.url ? "text-[var(--success)]" : "text-[var(--muted-foreground)]")}>
                {c.url ? "онлайн" : "оффлайн"}
              </span>
            </li>
          ))}
          <li className="mt-1 border-t pt-2 text-xs text-[var(--muted-foreground)]">
            {onlineCount} из {WAREHOUSE_CAMERAS.length} камер активны
          </li>
        </ul>
      </Panel>

      {/* Shipping queue */}
      <Panel title="Очередь отгрузки" icon={Truck}>
        {queue.length === 0 ? (
          <p className="py-2 text-center text-sm text-[var(--muted-foreground)]">Нет машин в работе.</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {queue.map((o) => (
              <li key={o.id} className="flex items-center justify-between text-sm">
                <span>
                  <span className="font-medium">#{o.id}</span>
                  <span className="ml-2 text-[var(--muted-foreground)]">{o.truck_number || "—"}</span>
                </span>
                <StatusBadge status={o.status} />
              </li>
            ))}
          </ul>
        )}
      </Panel>

      {/* Alerts / activity */}
      <Panel title="События" icon={Bell}>
        {(events ?? []).length === 0 ? (
          <p className="py-2 text-center text-sm text-[var(--muted-foreground)]">Событий пока нет.</p>
        ) : (
          <ul className="flex flex-col gap-2.5">
            {(events ?? []).slice(0, 8).map((e) => (
              <li key={e.id} className="flex items-start gap-2">
                <Badge tone="muted" className="mt-0.5 shrink-0 text-[10px]">
                  {TYPE_LABELS[e.event_type] ?? e.event_type}
                </Badge>
                <div className="min-w-0">
                  <p className="truncate text-sm">{e.message}</p>
                  <p className="text-[11px] text-[var(--muted-foreground)]">
                    {new Date(e.created_at).toLocaleTimeString("ru-RU")}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}
