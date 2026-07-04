"use client";
import { useMemo } from "react";
import { useApi } from "@/lib/use-api";
import { isFinancialOrderStatus } from "@/lib/constants";
import type { EventLog, Order, Payment, StockItem } from "@/lib/types";

export interface ClientDebt {
  client_id: number;
  client_name: string;
  client_phone: string;
  debt_total: string;
  orders_count: number;
  unpaid_count: number;
  partial_count: number;
  stores_count: number;
  overdue_count: number;
}

function confirmedPayments(orders: Order[]): Payment[] {
  return orders.flatMap((order) => order.payments ?? [])
    .filter((payment) => payment.status === "confirmed");
}

function dayKey(d: Date): string {
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
}

/** Все данные «Командного центра». Вызывать один раз на странице. */
export function useDashboardMetrics() {
  const { data: orders } = useApi<Order[]>("/orders/");
  const { data: stock } = useApi<StockItem[]>("/stock/");
  const { data: events } = useApi<EventLog[]>("/events/");
  const { data: debts } = useApi<ClientDebt[]>("/clients/debts/");

  const list = useMemo(() => orders ?? [], [orders]);
  const queue = useMemo(
    () => list.filter((o) => ["arrived", "loading", "loaded"].includes(o.status)),
    [list],
  );
  const totalBags = (stock ?? []).reduce((s, i) => s + i.bags, 0);

  // Отгрузки по дням за 14 дней (мешки) + «сегодня/вчера» для дельты.
  const { shippedByDay, shippedToday, shippedYesterday, shippedTodayOrders, shippedTotal } = useMemo(() => {
    const bagsOf = (orderId: number) => list.find((o) => o.id === orderId)?.bags_loaded ?? 0;
    const days = 14;
    const start = new Date(); start.setHours(0, 0, 0, 0); start.setDate(start.getDate() - (days - 1));
    const slots: Record<string, { label: string; bags: number; orders: number }> = {};
    for (let i = 0; i < days; i++) {
      const d = new Date(start); d.setDate(start.getDate() + i);
      slots[dayKey(d)] = { label: String(d.getDate()).padStart(2, "0"), bags: 0, orders: 0 };
    }
    (events ?? []).forEach((e) => {
      if (e.event_type !== "shipment" || !e.order) return;
      const k = dayKey(new Date(e.created_at));
      if (slots[k]) { slots[k].bags += bagsOf(e.order); slots[k].orders += 1; }
    });
    const arr = Object.values(slots);
    const today = arr[arr.length - 1];
    const yesterday = arr[arr.length - 2];
    let total = 0;
    list.forEach((o) => { if (o.status === "shipped") total += o.bags_loaded ?? 0; });
    return {
      shippedByDay: arr,
      shippedToday: today?.bags ?? 0,
      shippedYesterday: yesterday?.bags ?? 0,
      shippedTodayOrders: today?.orders ?? 0,
      shippedTotal: total,
    };
  }, [list, events]);

  // Финансы за 14 дней: выручка по заказам + подтверждённые поступления.
  const { spark, periodRevenue, periodReceived } = useMemo(() => {
    const days = 14;
    const today = new Date(); today.setHours(23, 59, 59, 999);
    const start = new Date(today); start.setDate(today.getDate() - (days - 1)); start.setHours(0, 0, 0, 0);
    const slots: Record<string, { label: string; revenue: number; received: number }> = {};
    for (let i = 0; i < days; i++) {
      const d = new Date(start); d.setDate(start.getDate() + i);
      slots[dayKey(d)] = { label: String(d.getDate()).padStart(2, "0"), revenue: 0, received: 0 };
    }
    list.forEach((o) => {
      if (!isFinancialOrderStatus(o.status)) return;
      const d = new Date(o.created_at);
      if (d >= start && d <= today) { const k = dayKey(d); if (slots[k]) slots[k].revenue += Number(o.total_amount); }
    });
    confirmedPayments(list).forEach((payment) => {
      const d = new Date(payment.paid_at);
      if (d >= start && d <= today) { const k = dayKey(d); if (slots[k]) slots[k].received += Number(payment.amount); }
    });
    const arr = Object.values(slots);
    return {
      spark: arr,
      periodRevenue: arr.reduce((s, x) => s + x.revenue, 0),
      periodReceived: arr.reduce((s, x) => s + x.received, 0),
    };
  }, [list]);

  // Заказы в работе по статусам (воронка текущего дня).
  const pipeline = useMemo(() => {
    const active = ["pending", "confirmed", "arrived", "loading", "loaded"] as const;
    return active.map((status) => ({
      status,
      count: list.filter((o) => o.status === status).length,
    }));
  }, [list]);

  // Склад в разрезе продуктов (топ-5 + «прочее»).
  const stockByProduct = useMemo(() => {
    const byProduct: Record<string, number> = {};
    (stock ?? []).forEach((i) => {
      byProduct[i.product_label] = (byProduct[i.product_label] ?? 0) + i.bags;
    });
    const sorted = Object.entries(byProduct)
      .map(([name, bags]) => ({ name, bags }))
      .sort((a, b) => b.bags - a.bags);
    if (sorted.length <= 6) return sorted;
    const top = sorted.slice(0, 5);
    const rest = sorted.slice(5).reduce((s, x) => s + x.bags, 0);
    return [...top, { name: "Прочее", bags: rest }];
  }, [stock]);

  // Долги: общая сумма и топ должников.
  const { debtTotal, topDebtors } = useMemo(() => {
    const rows = debts ?? [];
    return {
      debtTotal: rows.reduce((s, r) => s + Number(r.debt_total), 0),
      topDebtors: [...rows].sort((a, b) => Number(b.debt_total) - Number(a.debt_total)).slice(0, 5),
    };
  }, [debts]);

  return {
    queue, totalBags,
    shippedByDay, shippedToday, shippedYesterday, shippedTodayOrders, shippedTotal,
    spark, periodRevenue, periodReceived,
    pipeline, stockByProduct, debtTotal, topDebtors,
  };
}

export type DashboardMetrics = ReturnType<typeof useDashboardMetrics>;
