"use client";
import Link from "next/link";
import { useMemo } from "react";
import { Camera, Bell, Activity, Truck, ChevronRight } from "lucide-react";
import { ResponsiveContainer, AreaChart, Area } from "recharts";
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
  const debt = list.filter((o) => !o.is_fully_paid && o.status !== "draft" && o.status !== "cancelled")
    .reduce((s, o) => s + (Number(o.total_amount) - Number(o.paid_total)), 0);
  const onlineCount = WAREHOUSE_CAMERAS.filter((c) => c.url).length;

  // Мини-спарклайн: выручка по заказам за последние 14 дней + поступления за период.
  const { spark, periodRevenue, periodReceived } = useMemo(() => {
    const days = 14;
    const today = new Date(); today.setHours(23, 59, 59, 999);
    const start = new Date(today); start.setDate(today.getDate() - (days - 1)); start.setHours(0, 0, 0, 0);
    const key = (d: Date) => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
    const slots: Record<string, { revenue: number; received: number }> = {};
    for (let i = 0; i < days; i++) {
      const d = new Date(start); d.setDate(start.getDate() + i);
      slots[key(d)] = { revenue: 0, received: 0 };
    }
    list.forEach((o) => {
      if (o.status === "cancelled") return;
      const d = new Date(o.created_at);
      if (d >= start && d <= today) { const k = key(d); if (slots[k]) slots[k].revenue += Number(o.total_amount); }
    });
    (events ?? []).forEach((e) => {
      if (e.event_type !== "payment") return;
      const d = new Date(e.created_at);
      if (d >= start && d <= today) { const k = key(d); if (slots[k]) slots[k].received += Number((e.payload?.amount as string) ?? 0); }
    });
    const arr = Object.values(slots);
    return {
      spark: arr,
      periodRevenue: arr.reduce((s, x) => s + x.revenue, 0),
      periodReceived: arr.reduce((s, x) => s + x.received, 0),
    };
  }, [orders, events]);

  return (
    <div className="flex flex-col gap-4">
      {/* Аналитика — быстрый доступ + мини-график */}
      <Link href="/reports"
        className="group block rounded-xl border bg-[var(--card)] p-4 shadow-sm transition-colors hover:border-[var(--ring)]/40">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="flex size-9 items-center justify-center rounded-lg bg-[var(--ring)]/10">
              <Activity className="size-4 text-[var(--ring)]" />
            </div>
            <div>
              <div className="text-sm font-semibold">Аналитика</div>
              <div className="text-[11px] text-[var(--muted-foreground)]">Выручка, поступления, динамика</div>
            </div>
          </div>
          <ChevronRight className="size-4 text-[var(--muted-foreground)] transition-transform group-hover:translate-x-0.5" />
        </div>
        <div className="mt-3 grid grid-cols-2 gap-3">
          <div>
            <div className="text-[10px] font-semibold uppercase tracking-[0.1em] text-[var(--muted-foreground)]">Выручка 14д</div>
            <div className="text-sm font-bold tabular-nums">{formatMoney(String(periodRevenue))} ₸</div>
          </div>
          <div>
            <div className="text-[10px] font-semibold uppercase tracking-[0.1em] text-[var(--muted-foreground)]">Поступило 14д</div>
            <div className="text-sm font-bold tabular-nums text-[var(--success)]">{formatMoney(String(periodReceived))} ₸</div>
          </div>
        </div>
        <div className="mt-2 h-[44px] w-full">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={spark} margin={{ top: 2, right: 0, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id="spark-rev" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--ring)" stopOpacity={0.35} />
                  <stop offset="100%" stopColor="var(--ring)" stopOpacity={0} />
                </linearGradient>
              </defs>
              <Area type="monotone" dataKey="revenue" stroke="var(--ring)" strokeWidth={2} fill="url(#spark-rev)" />
              <Area type="monotone" dataKey="received" stroke="var(--success)" strokeWidth={1.5} fillOpacity={0} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </Link>

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
