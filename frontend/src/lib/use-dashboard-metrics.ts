"use client";
import { useEffect, useMemo, useState } from "react";
import { useApi } from "@/lib/use-api";
import { useAuth } from "@/store/auth";
import { can } from "@/lib/can";
import { useLocalDay } from "@/lib/use-local-day";
import {
  adaptDashboardDebt,
  adaptOperationalShipments,
  adaptReportSummary,
  dashboardReportRange,
} from "@/lib/dashboard-analytics";
import type { DashboardOperationalSummary, ReportSummary, StockItem } from "@/lib/types";

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
  const canFinance = can(me, "reports.view");
  const canPayments = can(me, "payments.confirm");
  const reportRange = useMemo(() => dashboardReportRange(currentDay, periodDays), [currentDay, periodDays]);
  const operationalUrl = canOrders
    ? `/orders/dashboard-operational/?from=${reportRange.from}&to=${reportRange.to}`
    : null;
  const {
    data: operational,
    error: operationalErr,
    reload: reloadOperational,
  } = useApi<DashboardOperationalSummary>(operationalUrl);
  const { data: stock, error: stockErr, reload: reloadStock } = useApi<StockItem[]>(canStock ? "/stock/" : null);
  const {
    data: report,
    error: reportErr,
    reload: reloadReport,
  } = useApi<ReportSummary>(canFinance ? `/reports/summary/?from=${reportRange.from}&to=${reportRange.to}` : null);
  const [lastUpdatedAt, setLastUpdatedAt] = useState<Date | null>(null);
  // Пока сводка и заказы не пришли, все счётчики равны нулю — и «требует
  // внимания» показывал бы зелёное «ничего срочного» на пустых данных.
  // Управляющий уходил бы со спокойным экраном за секунду до красного.
  const loading = (canOrders && operational == null) || (canStock && stock == null) || (canFinance && report == null);

  // Ошибка видна, только пока соответствующих данных нет совсем — частичный дашборд не глушим.
  const loadError = (canOrders && operationalErr) || (canStock && stockErr) || (canFinance && reportErr) || "";
  const stale = Boolean(loadError) && !loading;

  useEffect(() => {
    if (loading || loadError) return;
    if (!canOrders && !canStock && !canFinance) return;
    setLastUpdatedAt(new Date());
  }, [canFinance, canOrders, canStock, loadError, loading, operational, report, stock]);
  const reload = () => {
    reloadOperational();
    reloadStock();
    reloadReport();
  };

  const queue = useMemo(() => operational?.queue ?? [], [operational?.queue]);
  const totalBags = (stock ?? []).reduce((s, i) => s + i.bags, 0);

  const operationalShipments = useMemo(
    () => adaptOperationalShipments(operational?.days ?? [], currentDay, periodDays),
    [currentDay, operational?.days, periodDays],
  );
  const reportMetrics = useMemo(
    () => (report ? adaptReportSummary(report, currentDay, periodDays) : null),
    [currentDay, periodDays, report],
  );
  const shipmentMetrics = reportMetrics ?? operationalShipments;
  const spark =
    reportMetrics?.spark ?? operationalShipments.shippedByDay.map(({ label }) => ({ label, revenue: 0, received: 0 }));
  const periodRevenue = reportMetrics?.periodRevenue ?? 0;
  const periodReceived = reportMetrics?.periodReceived ?? 0;
  const receivedToday = reportMetrics?.receivedToday ?? 0;
  const receivedTodayCount = reportMetrics?.receivedTodayCount ?? 0;
  const moneyCurrency = reportMetrics?.moneyCurrency ?? "KZT";

  // Склад в разрезе продуктов (топ-5 + «прочее»). Минусовые остатки — ошибка
  // учёта, а не доля склада: в пончике им не место (отрицательный сектор
  // ломает диаграмму), но молчать о них нельзя — отдаём отдельным списком.
  const { stockByProduct, negativeStock, stockPositionCount } = useMemo(() => {
    const byProduct: Record<string, number> = {};
    (stock ?? []).forEach((i) => {
      byProduct[i.product_label] = (byProduct[i.product_label] ?? 0) + i.bags;
    });
    const rows = Object.entries(byProduct).map(([name, bags]) => ({ name, bags }));
    const negative = rows.filter((row) => row.bags < 0).sort((a, b) => a.bags - b.bags);
    const sorted = rows.filter((row) => row.bags > 0).sort((a, b) => b.bags - a.bags);
    if (sorted.length <= 6) {
      return {
        stockByProduct: sorted,
        negativeStock: negative,
        stockPositionCount: rows.length,
      };
    }
    const top = sorted.slice(0, 5);
    const rest = sorted.slice(5).reduce((s, x) => s + x.bags, 0);
    return {
      stockByProduct: [...top, { name: "Прочее", bags: rest }],
      negativeStock: negative,
      stockPositionCount: rows.length,
    };
  }, [stock]);

  // Долги: общая сумма, топ должников и отдельно просроченное.
  // Просрочка важнее общей суммы, но её основная валюта может отличаться от
  // валюты всей дебиторки. Сводки выбирают валюту независимо и не показывают
  // ложный ноль, когда обычный долг в KZT, а просрочка — в USD.
  const { debtTotal, debtCurrency, overdueTotal, overdueCurrency, overdueClients } = useMemo(
    () => adaptDashboardDebt(report?.debt_now),
    [report?.debt_now],
  );

  // Что требует действия прямо сейчас. Каждая строка ведёт на свой экран,
  // поэтому в блоке только то, по чему есть куда нажать.
  const attention = {
    pendingPayments: operational?.attention.pending_payments ?? 0,
    awaitingReview: operational?.attention.awaiting_review ?? 0,
    stuckInLoading: operational?.attention.stuck_in_loading ?? 0,
  };

  return {
    queue,
    totalBags,
    ...shipmentMetrics,
    spark,
    periodRevenue,
    periodReceived,
    receivedToday,
    receivedTodayCount,
    moneyCurrency,
    stockByProduct,
    stockPositionCount,
    negativeStock,
    debtTotal,
    debtCurrency,
    overdueTotal,
    overdueCurrency,
    overdueClients,
    attention,
    loading,
    stale,
    lastUpdatedAt,
    loadError,
    reload,
    canOrders,
    canStock,
    canFinance,
    canPayments,
  };
}

export type DashboardMetrics = ReturnType<typeof useDashboardMetrics>;
