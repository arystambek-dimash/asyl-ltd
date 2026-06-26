"use client";
import Link from "next/link";
import { useMemo } from "react";
import { Activity, ChevronRight } from "lucide-react";
import { ResponsiveContainer, AreaChart, Area } from "recharts";
import { useApi } from "@/lib/use-api";
import { formatMoney, cn } from "@/lib/utils";
import { isFinancialOrderStatus } from "@/lib/constants";
import type { Order, StockItem, EventLog, Payment } from "@/lib/types";

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

function confirmedPayments(orders: Order[]): Payment[] {
  return orders.flatMap((order) => order.payments ?? [])
    .filter((payment) => payment.status === "confirmed");
}

export function SurveillancePanels() {
  const { data: orders } = useApi<Order[]>("/orders/");
  const { data: stock } = useApi<StockItem[]>("/stock/");
  const { data: events } = useApi<EventLog[]>("/events/");

  const list = orders ?? [];
  const totalBags = (stock ?? []).reduce((s, i) => s + i.bags, 0);

  // Метрики «сегодня» по мешкам.
  const { shippedTotal, shippedToday, shippedTodayOrders } = useMemo(() => {
    const bagsOf = (orderId: number) => list.find((o) => o.id === orderId)?.bags_loaded ?? 0;
    const startToday = new Date(); startToday.setHours(0, 0, 0, 0);
    let total = 0, today = 0, todayOrders = 0;
    // всего отгружено мешков = bags_loaded по отгруженным заказам
    list.forEach((o) => { if (o.status === "shipped") total += o.bags_loaded ?? 0; });
    // отгружено сегодня = bags_loaded заказов с событием отгрузки сегодня
    (events ?? []).forEach((e) => {
      if (e.event_type !== "shipment" || !e.order) return;
      if (new Date(e.created_at) >= startToday) { today += bagsOf(e.order); todayOrders += 1; }
    });
    return { shippedTotal: total, shippedToday: today, shippedTodayOrders: todayOrders };
  }, [orders, events]);

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
      if (!isFinancialOrderStatus(o.status)) return;
      const d = new Date(o.created_at);
      if (d >= start && d <= today) { const k = key(d); if (slots[k]) slots[k].revenue += Number(o.total_amount); }
    });
    confirmedPayments(list).forEach((payment) => {
      const d = new Date(payment.paid_at);
      if (d >= start && d <= today) { const k = key(d); if (slots[k]) slots[k].received += Number(payment.amount); }
    });
    const arr = Object.values(slots);
    return {
      spark: arr,
      periodRevenue: arr.reduce((s, x) => s + x.revenue, 0),
      periodReceived: arr.reduce((s, x) => s + x.received, 0),
    };
  }, [orders]);

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

      {/* Сводка по мешкам */}
      <Panel title="Сводка за день" icon={Activity}>
        <div className="grid grid-cols-2 gap-3">
          {[
            { l: "Осталось на складе", v: `${formatMoney(totalBags)} меш.`, hint: "текущий остаток" },
            { l: "Ушло сегодня", v: `${formatMoney(shippedToday)} меш.`, hint: "отгружено за сегодня", accent: true },
            { l: "Отгружено всего", v: `${formatMoney(shippedTotal)} меш.`, hint: "за всё время" },
            { l: "Отгружено сегодня", v: `${shippedTodayOrders} зак.`, hint: "заказов за сегодня" },
          ].map((k) => (
            <div key={k.l} className={cn("flex flex-col gap-1 rounded-lg border p-3",
              k.accent ? "border-[var(--ring)]/20 bg-[var(--ring)]/10" : "border-[var(--border)] bg-[var(--secondary)]/40")}>
              <div className="text-[10px] font-semibold uppercase tracking-[0.08em] text-[var(--muted-foreground)]">{k.l}</div>
              <div className={cn("text-lg font-bold tabular-nums leading-none", k.accent && "text-[var(--ring)]")}>{k.v}</div>
              <div className="text-[10px] text-[var(--muted-foreground)]">{k.hint}</div>
            </div>
          ))}
        </div>
      </Panel>
    </div>
  );
}
