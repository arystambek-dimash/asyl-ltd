"use client";
import { useMemo } from "react";
import { useApi } from "@/lib/use-api";
import { isFinancialOrderStatus } from "@/lib/constants";
import type { EventLog, Order, Payment, StockItem } from "@/lib/types";

function confirmedPayments(orders: Order[]): Payment[] {
  return orders.flatMap((order) => order.payments ?? [])
    .filter((payment) => payment.status === "confirmed");
}

/** Все данные «Командного центра»: очередь, мешки за день, выручка 14д. */
export function useDashboardMetrics() {
  const { data: orders } = useApi<Order[]>("/orders/");
  const { data: stock } = useApi<StockItem[]>("/stock/");
  const { data: events } = useApi<EventLog[]>("/events/");

  const list = useMemo(() => orders ?? [], [orders]);
  const queue = useMemo(
    () => list.filter((o) => ["arrived", "loading", "loaded"].includes(o.status)),
    [list],
  );
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
  }, [list, events]);

  // Спарклайн: выручка по заказам за последние 14 дней + поступления за период.
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
  }, [list]);

  return {
    queue, totalBags,
    shippedTotal, shippedToday, shippedTodayOrders,
    spark, periodRevenue, periodReceived,
  };
}
