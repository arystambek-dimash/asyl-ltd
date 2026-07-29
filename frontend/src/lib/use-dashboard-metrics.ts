"use client";
import { useMemo } from "react";
import { useApi } from "@/lib/use-api";
import { useAuth } from "@/store/auth";
import { can } from "@/lib/can";
import { useLocalDay } from "@/lib/use-local-day";
import { isFinancialOrderStatus, orderStatusGroup } from "@/lib/constants";
import { primaryDebtCurrency, sumDebtByCurrency } from "@/lib/utils";
import type { ClientDebt, EventLog, Order, Payment, StockItem } from "@/lib/types";

function confirmedPayments(orders: Order[]): Payment[] {
  return orders.flatMap((order) => order.payments ?? []).filter((payment) => payment.status === "confirmed");
}

function dayKey(d: Date): string {
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
}

/** Все данные «Командного центра». Вызывать один раз на странице. */
export function useDashboardMetrics(periodDays = 14) {
  const currentDay = useLocalDay();
  // Главная открывается каждому сотруднику сразу после входа, а права у всех
  // разные: у менеджера нет склада, у оператора — финансов, у загрузчика —
  // почти ничего. Запрашиваем только разрешённое — иначе первый экран смены
  // встречал человека колонкой 403-ошибок.
  const { me } = useAuth();
  const canOrders = can(me, "orders.view");
  const canStock = can(me, "warehouse.view");
  const canEvents = can(me, "events.view");
  const canFinance = can(me, "reports.view");
  const canPayments = can(me, "payments.confirm");
  const { data: orders, error: ordersErr, reload: reloadOrders } = useApi<Order[]>(canOrders ? "/orders/" : null);
  const { data: stock, error: stockErr, reload: reloadStock } = useApi<StockItem[]>(canStock ? "/stock/" : null);
  const { data: events, error: eventsErr, reload: reloadEvents } = useApi<EventLog[]>(canEvents ? "/events/" : null);
  const {
    data: debts,
    error: debtsErr,
    reload: reloadDebts,
  } = useApi<ClientDebt[]>(canFinance ? "/clients/debts/" : null);

  // Пока долги и заказы не пришли, все счётчики равны нулю — и «требует
  // внимания» показывал бы зелёное «ничего срочного» на пустых данных.
  // Управляющий уходил бы со спокойным экраном за секунду до красного.
  const loading = (canOrders && orders == null) || (canFinance && debts == null);

  // Ошибка видна, только пока соответствующих данных нет совсем — частичный дашборд не глушим.
  const loadError =
    (canOrders && orders == null && ordersErr) ||
    (canStock && stock == null && stockErr) ||
    (canEvents && events == null && eventsErr) ||
    (canFinance && debts == null && debtsErr) ||
    "";
  const reload = () => {
    reloadOrders();
    reloadStock();
    reloadEvents();
    reloadDebts();
  };

  const list = useMemo(() => orders ?? [], [orders]);
  const queue = useMemo(() => list.filter((o) => ["arrived", "loading"].includes(o.status)), [list]);
  const totalBags = (stock ?? []).reduce((s, i) => s + i.bags, 0);

  // Отгрузки по дням за 14 дней (мешки) + «сегодня/вчера» для дельты.
  const { shippedByDay, shippedToday, shippedYesterday, shippedTodayOrders } = useMemo(() => {
    // Events reference orders by id. A map keeps aggregation O(orders + events)
    // instead of scanning the full order list for every shipment event.
    const bagsByOrder = new Map(list.map((order) => [order.id, order.bags_loaded ?? 0]));
    const days = periodDays;
    const start = new Date(`${currentDay}T00:00:00`);
    start.setDate(start.getDate() - (days - 1));
    const slots: Record<string, { label: string; bags: number; orders: number }> = {};
    for (let i = 0; i < days; i++) {
      const d = new Date(start);
      d.setDate(start.getDate() + i);
      slots[dayKey(d)] = { label: String(d.getDate()).padStart(2, "0"), bags: 0, orders: 0 };
    }
    (events ?? []).forEach((e) => {
      if (e.event_type !== "shipment" || !e.order) return;
      const k = dayKey(new Date(e.created_at));
      if (slots[k]) {
        slots[k].bags += bagsByOrder.get(e.order) ?? 0;
        slots[k].orders += 1;
      }
    });
    const arr = Object.values(slots);
    const today = arr[arr.length - 1];
    const yesterday = arr[arr.length - 2];
    return {
      shippedByDay: arr,
      shippedToday: today?.bags ?? 0,
      shippedYesterday: yesterday?.bags ?? 0,
      shippedTodayOrders: today?.orders ?? 0,
    };
  }, [currentDay, list, events, periodDays]);

  // Финансы за 14 дней: выручка по заказам + подтверждённые поступления.
  const { spark, periodRevenue, periodReceived, receivedToday, receivedTodayCount } = useMemo(() => {
    const days = periodDays;
    const today = new Date(`${currentDay}T23:59:59.999`);
    const start = new Date(today);
    start.setDate(today.getDate() - (days - 1));
    start.setHours(0, 0, 0, 0);
    const slots: Record<string, { label: string; revenue: number; received: number }> = {};
    for (let i = 0; i < days; i++) {
      const d = new Date(start);
      d.setDate(start.getDate() + i);
      slots[dayKey(d)] = { label: String(d.getDate()).padStart(2, "0"), revenue: 0, received: 0 };
    }
    list.forEach((o) => {
      if (!isFinancialOrderStatus(o.status)) return;
      if ((o.currency ?? "KZT") !== "KZT") return;
      const d = new Date(o.created_at);
      if (d >= start && d <= today) {
        const k = dayKey(d);
        if (slots[k]) slots[k].revenue += Number(o.total_amount);
      }
    });
    confirmedPayments(list).forEach((payment) => {
      if ((payment.currency ?? "KZT") !== "KZT") return;
      const d = new Date(payment.paid_at);
      if (d >= start && d <= today) {
        const k = dayKey(d);
        if (slots[k]) slots[k].received += Number(payment.amount);
      }
    });
    const arr = Object.values(slots);
    const todayKey = dayKey(new Date(`${currentDay}T12:00:00`));
    let receivedToday = 0;
    let receivedTodayCount = 0;
    confirmedPayments(list).forEach((payment) => {
      if ((payment.currency ?? "KZT") !== "KZT") return;
      const recognized = new Date(payment.confirmed_at ?? payment.paid_at);
      if (dayKey(recognized) === todayKey) {
        receivedToday += Number(payment.amount);
        receivedTodayCount += 1;
      }
    });
    return {
      spark: arr,
      periodRevenue: arr.reduce((s, x) => s + x.revenue, 0),
      periodReceived: arr.reduce((s, x) => s + x.received, 0),
      receivedToday,
      receivedTodayCount,
    };
  }, [currentDay, list, periodDays]);

  // Единая пользовательская воронка: четыре статуса вместо внутренних этапов.
  const pipeline = useMemo(() => {
    const publicStatuses = ["pending", "loading", "shipped", "cancelled"] as const;
    return publicStatuses.map((status) => ({
      status,
      count: list.filter((o) => orderStatusGroup(o.status) === status).length,
    }));
  }, [list]);

  // Склад в разрезе продуктов (топ-5 + «прочее»). Минусовые остатки — ошибка
  // учёта, а не доля склада: в пончике им не место (отрицательный сектор
  // ломает диаграмму), но молчать о них нельзя — отдаём отдельным списком.
  const { stockByProduct, negativeStock } = useMemo(() => {
    const byProduct: Record<string, number> = {};
    (stock ?? []).forEach((i) => {
      byProduct[i.product_label] = (byProduct[i.product_label] ?? 0) + i.bags;
    });
    const rows = Object.entries(byProduct).map(([name, bags]) => ({ name, bags }));
    const negative = rows.filter((row) => row.bags < 0).sort((a, b) => a.bags - b.bags);
    const sorted = rows.filter((row) => row.bags > 0).sort((a, b) => b.bags - a.bags);
    if (sorted.length <= 6) return { stockByProduct: sorted, negativeStock: negative };
    const top = sorted.slice(0, 5);
    const rest = sorted.slice(5).reduce((s, x) => s + x.bags, 0);
    return { stockByProduct: [...top, { name: "Прочее", bags: rest }], negativeStock: negative };
  }, [stock]);

  // Долги: общая сумма, топ должников и отдельно просроченное.
  const { debtTotal, debtCurrency, topDebtors, overdueTotal, overdueClients } = useMemo(() => {
    const rows = debts ?? [];
    // Просрочка важнее общей суммы: 4,86 млрд долга — это норма работы в долг,
    // а вот «сегодня день оплаты, а деньги не пришли» требует звонка.
    const overdue = rows.filter((r) => r.overdue_count > 0);
    // Валюты не складываются. Плитка показывает основную валюту, а сортировка
    // должников идёт внутри неё: $5 000 и 100 000 ₸ несравнимы напрямую.
    const byCurrency = sumDebtByCurrency(rows);
    const currency = primaryDebtCurrency(byCurrency);
    const amountIn = (row: ClientDebt) => {
      const exact = row.debt_by_currency?.[currency];
      if (exact != null) return Number(exact);
      return (row.debt_currency ?? "KZT") === currency ? Number(row.debt_total) : 0;
    };
    return {
      debtTotal: byCurrency[currency] ?? 0,
      debtCurrency: currency,
      topDebtors: [...rows].sort((a, b) => amountIn(b) - amountIn(a)).slice(0, 5),
      overdueTotal: sumDebtByCurrency(overdue)[currency] ?? 0,
      overdueClients: overdue.length,
    };
  }, [debts]);

  // Что требует действия прямо сейчас. Каждая строка ведёт на свой экран,
  // поэтому в блоке только то, по чему есть куда нажать.
  const attention = useMemo(() => {
    const pendingPayments = list
      .flatMap((order) => order.payments ?? [])
      .filter((payment) => payment.status === "received").length;
    const awaitingReview = list.filter((o) => orderStatusGroup(o.status) === "pending").length;
    const stuckInLoading = list.filter((o) => o.status === "loading").length;
    return { pendingPayments, awaitingReview, stuckInLoading };
  }, [list]);

  return {
    queue,
    totalBags,
    shippedByDay,
    shippedToday,
    shippedYesterday,
    shippedTodayOrders,
    spark,
    periodRevenue,
    periodReceived,
    receivedToday,
    receivedTodayCount,
    pipeline,
    stockByProduct,
    negativeStock,
    debtTotal,
    debtCurrency,
    topDebtors,
    overdueTotal,
    overdueClients,
    attention,
    loading,
    loadError,
    reload,
    canOrders,
    canStock,
    canEvents,
    canFinance,
    canPayments,
  };
}

export type DashboardMetrics = ReturnType<typeof useDashboardMetrics>;
