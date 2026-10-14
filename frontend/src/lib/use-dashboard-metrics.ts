"use client";
import { useEffect, useMemo, useState } from "react";
import { useApi } from "@/lib/use-api";
import { useAuth } from "@/store/auth";
import { can } from "@/lib/can";
import { useLocalDay } from "@/lib/use-local-day";
import { useVisiblePolling } from "@/lib/use-visible-polling";
import { finiteMoney } from "@/lib/currency-map";
import { adaptDashboardDebt, todayIncome, todayShipments } from "@/lib/dashboard-analytics";
import { productParts, productTitle, twinKey, twinKeys } from "@/lib/product-parts";
import { readStoredChoice, storeChoice, userChoiceKey } from "@/lib/stored-choice";
import { shiftIsoDate } from "@/lib/utils";
import { pickWarehouse } from "@/lib/warehouses";
import type {
  DashboardIntake,
  DashboardOperationalSummary,
  DashboardSettings,
  ReportSummary,
  StockByProduct,
  Warehouse,
} from "@/lib/types";

/** Главную держат открытой на ТВ: данные обновляются сами, без кликов. */
const DASHBOARD_POLL_MS = 30_000;
/** Приход с камер транслируется почти вживую — чаще остальной сводки. */
const INTAKE_POLL_MS = 10_000;
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
  const canIntake = can(me, "monoblock.view");

  // Какие отделы и склады показывать, решает суперпользователь; пусто — всё.
  const {
    data: settings,
    error: settingsErr,
    reload: reloadSettings,
    setData: commitSettings,
  } = useApi<DashboardSettings>("/dashboard/settings/");
  const departmentParam = settings?.departments.length
    ? `&department=${settings.departments.map((department) => encodeURIComponent(department.code)).join(",")}`
    : "";

  const {
    data: operational,
    error: operationalErr,
    reload: reloadOperational,
  } = useApi<DashboardOperationalSummary>(
    settings && canOrders
      ? `/orders/dashboard-operational/?date_from=${yesterday}&date_to=${today}${departmentParam}`
      : null,
  );
  const {
    data: report,
    error: reportErr,
    reload: reloadReport,
  } = useApi<ReportSummary>(
    settings && canFinance ? `/reports/summary/?date_from=${today}&date_to=${today}${departmentParam}` : null,
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
  const shownWarehouses = useMemo(() => {
    const allowed = new Set(settings?.warehouses.map((item) => item.id));
    return (warehouseList ?? []).filter((item) => allowed.size === 0 || allowed.has(item.id));
  }, [settings?.warehouses, warehouseList]);
  const warehouse = settings ? pickWarehouse(shownWarehouses, warehouseChoice) : null;
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
    data: intake,
    error: intakeError,
    reload: reloadIntake,
  } = useApi<DashboardIntake>(canIntake ? "/cameras/always-on-intake/" : null);
  useVisiblePolling(reloadIntake, INTAKE_POLL_MS, canIntake);

  const [lastUpdatedAt, setLastUpdatedAt] = useState<Date | null>(null);
  // Пока ответы не пришли, все счётчики равны нулю — экран не должен выдавать их за данные.
  const loading =
    settings == null ||
    (canOrders && operational == null) ||
    (canStock && stock == null) ||
    (canFinance && report == null);

  // Ошибка видна всегда; если данные уже есть, сводка помечается устаревшей.
  const loadError =
    settingsErr ||
    (canOrders && operationalErr) ||
    (canStock && (warehousesErr || stockErr)) ||
    (canFinance && reportErr) ||
    "";

  useEffect(() => {
    if (loading || loadError) return;
    if (!canOrders && !canStock && !canFinance) return;
    setLastUpdatedAt(new Date());
  }, [canFinance, canOrders, canStock, loadError, loading, operational, report, stock]);
  const reload = () =>
    Promise.all([reloadSettings(), reloadOperational(), reloadWarehouses(), reloadStock(), reloadReport()]);
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
  const debt = useMemo(() => adaptDashboardDebt(report?.debt_now), [report?.debt_now]);

  return {
    today,
    settings,
    commitSettings,
    queue: operational?.queue ?? [],
    ...todayShipments(operational?.days ?? [], today, yesterday),
    ...todayIncome(report),
    ...debt,
    stockTotal: stock?.total_bags ?? 0,
    stockKg: finiteMoney(stock?.total_kg ?? 0),
    stockRows,
    stockWarehouse: stock?.warehouse.name ?? "",
    warehouses: shownWarehouses.filter((item) => item.is_active),
    warehouseId: warehouse?.id ?? null,
    chooseWarehouse,
    intake,
    intakeError,
    loading,
    lastUpdatedAt,
    loadError,
    reload,
    canOrders,
    canStock,
    canFinance,
    canIntake,
  };
}

export type DashboardMetrics = ReturnType<typeof useDashboardMetrics>;
