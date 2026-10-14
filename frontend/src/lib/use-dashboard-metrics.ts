"use client";
import { useEffect, useMemo, useState } from "react";
import { useApi } from "@/lib/use-api";
import { useAuth } from "@/store/auth";
import { can } from "@/lib/can";
import { useLocalDay } from "@/lib/use-local-day";
import { useVisiblePolling } from "@/lib/use-visible-polling";
import { adaptDashboardDebt, todayIncome, todayShipments } from "@/lib/dashboard-analytics";
import { productParts, productTitle, twinKey, twinKeys } from "@/lib/product-parts";
import { readStoredChoice, storeChoice, userChoiceKey } from "@/lib/stored-choice";
import { shiftIsoDate } from "@/lib/utils";
import { pickWarehouse } from "@/lib/warehouses";
import type { DashboardOperationalSummary, ReportSummary, StockByProduct, Warehouse } from "@/lib/types";

export type DashboardAttentionKey = "overdue" | "payments" | "orders" | "stock";
type DashboardAttentionItem = { key: DashboardAttentionKey; count: number };

/** Главную держат открытой на ТВ: данные обновляются сами, без кликов. */
const DASHBOARD_POLL_MS = 30_000;
// Склад сводки помнит браузер: ТВ на «Мельнице» и ТВ на Нью-Сити показывают каждый свой.
const WAREHOUSE_STORAGE_KEY = "dashboard:warehouse";

/** Сводка дня для «Главной» и режима ТВ. Вызывать один раз на странице. */
export function useDashboardMetrics() {
  const today = useLocalDay();
  const yesterday = shiftIsoDate(today, -1);
  // Права у всех разные: у менеджера нет склада, у оператора — финансов.
  // Запрашиваем только разрешённое — иначе экран встречал бы колонкой 403.
  const { me } = useAuth();
  const canOrders = can(me, "orders.view");
  const canStock = can(me, "warehouse.view");
  const canFinance = can(me, "reports.view");
  const canPayments = can(me, "payments.confirm");
  const {
    data: operational,
    error: operationalErr,
    reload: reloadOperational,
  } = useApi<DashboardOperationalSummary>(
    canOrders ? `/orders/dashboard-operational/?date_from=${yesterday}&date_to=${today}` : null,
  );
  const {
    data: warehouseList,
    error: warehousesErr,
    reload: reloadWarehouses,
  } = useApi<Warehouse[]>(canStock ? "/warehouses/" : null);
  const warehouseKey = userChoiceKey(WAREHOUSE_STORAGE_KEY, me?.id);
  const [warehouseChoice, setWarehouseChoice] = useState<string | null>(null);
  useEffect(() => {
    setWarehouseChoice(readStoredChoice(warehouseKey));
  }, [warehouseKey]);
  const warehouses = useMemo(() => (warehouseList ?? []).filter((item) => item.is_active), [warehouseList]);
  const warehouse = pickWarehouse(warehouseList ?? [], warehouseChoice);
  const chooseWarehouse = (id: number) => {
    setWarehouseChoice(String(id));
    storeChoice(warehouseKey, String(id));
  };
  // Склады не складываются: остаток всегда одного склада, по умолчанию — основного.
  const {
    data: stock,
    error: stockErr,
    reload: reloadStock,
  } = useApi<StockByProduct>(canStock && warehouse ? `/stock/by-product/?warehouse=${warehouse.id}` : null);
  const {
    data: report,
    error: reportErr,
    reload: reloadReport,
  } = useApi<ReportSummary>(canFinance ? `/reports/summary/?date_from=${today}&date_to=${today}` : null);
  const [lastUpdatedAt, setLastUpdatedAt] = useState<Date | null>(null);
  // Пока ответы не пришли, все счётчики равны нулю — и «Нужно решить» показал
  // бы «нечего решать» на пустых данных.
  const loading = (canOrders && operational == null) || (canStock && stock == null) || (canFinance && report == null);

  // Ошибка видна всегда; если данные уже есть, сводка помечается устаревшей.
  const loadError =
    (canOrders && operationalErr) || (canStock && (warehousesErr || stockErr)) || (canFinance && reportErr) || "";

  useEffect(() => {
    if (loading || loadError) return;
    if (!canOrders && !canStock && !canFinance) return;
    setLastUpdatedAt(new Date());
  }, [canFinance, canOrders, canStock, loadError, loading, operational, report, stock]);
  const reload = () => Promise.all([reloadOperational(), reloadWarehouses(), reloadStock(), reloadReport()]);
  useVisiblePolling(reload, DASHBOARD_POLL_MS, canOrders || canStock || canFinance);

  const stockRows = useMemo(() => {
    // Подпись — как на «Складе»: марка, сорт, фасовка; цвет — только у двойников.
    const rows = (stock?.products ?? []).map((row) => ({ ...row, parts: productParts(row.name, row.weight_kg) }));
    const twins = twinKeys(rows.map(({ parts }) => parts));
    return rows.map(({ product, bags, parts, color_label }) => ({
      product,
      bags,
      title: productTitle(parts, twins.has(twinKey(parts)) ? color_label : undefined),
    }));
  }, [stock?.products]);
  const negativeStock = stockRows.filter((row) => row.bags < 0).length;
  const debt = useMemo(() => adaptDashboardDebt(report?.debt_now), [report?.debt_now]);

  // Что требует действия — один список для числа в шапке и блока «Нужно решить».
  // Права здесь не проверяются: чужие разделы не запрашиваются, а оплаты на
  // подтверждение бэкенд без payments.confirm отдаёт нулём.
  const attention: DashboardAttentionItem[] = (
    [
      { key: "overdue", count: debt.overdueClients },
      { key: "payments", count: operational?.attention.pending_payments ?? 0 },
      { key: "orders", count: operational?.attention.awaiting_review ?? 0 },
      { key: "stock", count: negativeStock },
    ] satisfies DashboardAttentionItem[]
  ).filter((item) => item.count > 0);

  return {
    today,
    queue: operational?.queue ?? [],
    ...todayShipments(operational?.days ?? [], today, yesterday),
    ...todayIncome(report),
    ...debt,
    stockTotal: stock?.total_bags ?? 0,
    stockRows,
    stockWarehouse: stock?.warehouse.name ?? "",
    warehouses,
    warehouseId: warehouse?.id ?? null,
    chooseWarehouse,
    attention,
    attentionCount: attention.reduce((sum, item) => sum + item.count, 0),
    loading,
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
