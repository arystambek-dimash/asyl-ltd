import type { DashboardOperationalSummary, ReportSummary } from "@/lib/types";
import { amountForCurrency, finiteMoney, primaryMoneyCurrency } from "@/lib/currency-map";
import { toLocalIsoDate } from "@/lib/utils";

const DEFAULT_DASHBOARD_CURRENCY = "KZT";

interface DashboardShipmentPoint {
  label: string;
  bags: number;
  orders: number;
}

interface DashboardMoneyPoint {
  label: string;
  revenue: number;
  received: number;
}

export interface DashboardShipmentMetrics {
  shippedByDay: DashboardShipmentPoint[];
  shippedToday: number;
  shippedYesterday: number;
  shippedTodayOrders: number;
}

export interface DashboardReportMetrics extends DashboardShipmentMetrics {
  spark: DashboardMoneyPoint[];
  moneyCurrency: string;
  periodRevenue: number;
  periodReceived: number;
  receivedToday: number;
  receivedTodayCount: number;
}

export interface DashboardDebtMetrics {
  debtTotal: number;
  debtCurrency: string;
  overdueTotal: number;
  overdueCurrency: string;
  overdueClients: number;
}

export function adaptDashboardDebt(debt: ReportSummary["debt_now"] | undefined): DashboardDebtMetrics {
  const allByCurrency = debt?.by_currency ?? {};
  const overdueByCurrency = debt?.overdue_by_currency ?? {};
  const debtCurrency = debt?.currency || primaryMoneyCurrency(allByCurrency);
  const overdueCurrency = debt?.overdue_currency || primaryMoneyCurrency(overdueByCurrency);

  return {
    debtTotal: amountForCurrency(allByCurrency, debt?.total ?? 0, debtCurrency),
    debtCurrency,
    overdueTotal: amountForCurrency(overdueByCurrency, 0, overdueCurrency),
    overdueCurrency,
    overdueClients: debt?.overdue_clients ?? 0,
  };
}

function normalizedPeriodDays(periodDays: number): number {
  return Math.max(1, Math.trunc(periodDays) || 1);
}

function localDate(day: string): Date {
  const [year, month, date] = day.split("-").map(Number);
  return new Date(year, month - 1, date, 12);
}

export function dashboardReportRange(currentDay: string, periodDays: number): { from: string; to: string } {
  const start = localDate(currentDay);
  start.setDate(start.getDate() - (normalizedPeriodDays(periodDays) - 1));
  return { from: toLocalIsoDate(start), to: currentDay };
}

function periodSlots<T>(currentDay: string, periodDays: number, create: (label: string) => T): Map<string, T> {
  const { from } = dashboardReportRange(currentDay, periodDays);
  const start = localDate(from);
  const slots = new Map<string, T>();

  for (let index = 0; index < normalizedPeriodDays(periodDays); index += 1) {
    const date = new Date(start);
    date.setDate(start.getDate() + index);
    slots.set(toLocalIsoDate(date), create(String(date.getDate()).padStart(2, "0")));
  }

  return slots;
}

function shipmentMetrics(points: DashboardShipmentPoint[]): DashboardShipmentMetrics {
  const today = points.at(-1);
  const yesterday = points.at(-2);
  return {
    shippedByDay: points,
    shippedToday: today?.bags ?? 0,
    shippedYesterday: yesterday?.bags ?? 0,
    shippedTodayOrders: today?.orders ?? 0,
  };
}

/**
 * Convert the server-owned accounting report into the fixed dashboard series.
 * Empty dates are restored here only for chart continuity; business totals and
 * recognition dates stay owned by the backend report.
 */
export function adaptReportSummary(
  report: ReportSummary,
  currentDay: string,
  periodDays: number,
): DashboardReportMetrics {
  // One chart must never overlay values measured in different currencies.
  // The report's primary income currency is the most useful operator view;
  // revenue is projected into that same currency via the server breakdown.
  const moneyCurrency = report.income.currency || report.shipped.currency || DEFAULT_DASHBOARD_CURRENCY;
  const shipmentSlots = periodSlots(currentDay, periodDays, (label) => ({ label, bags: 0, orders: 0 }));
  const moneySlots = periodSlots(currentDay, periodDays, (label) => ({ label, revenue: 0, received: 0 }));

  for (const day of report.days) {
    const shipment = shipmentSlots.get(day.date);
    if (shipment) {
      shipment.bags = finiteMoney(day.bags);
      shipment.orders = finiteMoney(day.orders);
    }

    const money = moneySlots.get(day.date);
    if (money) {
      money.revenue = amountForCurrency(day.revenue_by_currency, day.revenue, moneyCurrency);
      money.received = amountForCurrency(day.received_by_currency, day.received, moneyCurrency);
    }
  }

  const spark = [...moneySlots.values()];
  const today = report.days.find((day) => day.date === currentDay);
  return {
    ...shipmentMetrics([...shipmentSlots.values()]),
    spark,
    moneyCurrency,
    periodRevenue: spark.reduce((total, point) => total + point.revenue, 0),
    periodReceived: spark.reduce((total, point) => total + point.received, 0),
    receivedToday: today ? amountForCurrency(today.received_by_currency, today.received, moneyCurrency) : 0,
    receivedTodayCount: today?.payments ?? 0,
  };
}

/**
 * Operators without reports.view still get a continuous operational chart.
 * The backend has already reconciled rollbacks and repeated shipments; this
 * adapter only restores empty calendar dates for the chart.
 */
export function adaptOperationalShipments(
  days: readonly DashboardOperationalSummary["days"][number][],
  currentDay: string,
  periodDays: number,
): DashboardShipmentMetrics {
  const slots = periodSlots(currentDay, periodDays, (label) => ({ label, bags: 0, orders: 0 }));
  for (const day of days) {
    const slot = slots.get(day.date);
    if (!slot) continue;
    slot.bags = finiteMoney(day.bags);
    slot.orders = finiteMoney(day.orders);
  }

  return shipmentMetrics([...slots.values()]);
}
