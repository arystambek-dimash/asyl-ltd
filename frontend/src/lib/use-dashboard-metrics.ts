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

export type DashboardAttentionKey = "overdue" | "payments" | "orders" | "stock";
type DashboardAttentionItem = { key: DashboardAttentionKey; count: number };

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
    ? `/orders/dashboard-operational/?date_from=${reportRange.from}&date_to=${reportRange.to}`
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
  } = useApi<ReportSummary>(
    canFinance ? `/reports/summary/?date_from=${reportRange.from}&date_to=${reportRange.to}` : null,
  );
  const [lastUpdatedAt, setLastUpdatedAt] = useState<Date | null>(null);
  // Пока сводка и заказы не пришли, все счётчики равны нулю — и «требует
  // внимания» показывал бы зелёное «ничего срочного» на пустых данных.
  // Управляющий уходил бы со спокойным экраном за секунду до красного.
  const loading = (canOrders && operational == null) || (canStock && stock == null) || (canFinance && report == null);

  // Ошибка видна всегда; если данные уже есть, дашборд помечается устаревшим (stale).
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

  // Позиции склада по продуктам. Минусовые остатки — ошибка учёта: молчать
  // о них нельзя, поэтому отдаём отдельным списком.
  const { negativeStock, stockPositionCount } = useMemo(() => {
    const byProduct: Record<string, number> = {};
    (stock ?? []).forEach((i) => {
      byProduct[i.product_label] = (byProduct[i.product_label] ?? 0) + i.bags;
    });
    const rows = Object.entries(byProduct).map(([name, bags]) => ({ name, bags }));
    return {
      negativeStock: rows.filter((row) => row.bags < 0).sort((a, b) => a.bags - b.bags),
      stockPositionCount: rows.length,
    };
  }, [stock]);

  // Долги: общая сумма и отдельно просроченное.
  // Просрочка важнее общей суммы, но её основная валюта может отличаться от
  // валюты всей дебиторки. Сводки выбирают валюту независимо и не показывают
  // ложный ноль, когда обычный долг в KZT, а просрочка — в USD.
  const { debtTotal, debtCurrency, overdueTotal, overdueCurrency, overdueClients } = useMemo(
    () => adaptDashboardDebt(report?.debt_now),
    [report?.debt_now],
  );

  // Что требует действия прямо сейчас — один список для шапки и блока «Нужно
  // решить». Проверять права здесь не нужно: чужие разделы не запрашиваются,
  // а оплаты на подтверждение бэкенд без payments.confirm отдаёт нулём.
  const attentionRows: DashboardAttentionItem[] = [
    { key: "overdue", count: overdueClients },
    { key: "payments", count: operational?.attention.pending_payments ?? 0 },
    { key: "orders", count: operational?.attention.awaiting_review ?? 0 },
    { key: "stock", count: negativeStock.length },
  ];
  const attention = attentionRows.filter((item) => item.count > 0);
  const attentionCount = attention.reduce((sum, item) => sum + item.count, 0);

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
    stockPositionCount,
    negativeStock,
    debtTotal,
    debtCurrency,
    overdueTotal,
    overdueCurrency,
    overdueClients,
    attention,
    attentionCount,
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
