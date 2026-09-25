import type { DashboardOperationalSummary, ReportSummary } from "@/lib/types";
import { amountForCurrency, finiteMoney, otherCurrencyAmounts } from "@/lib/currency-map";
import { reportChartSeries } from "@/lib/report-analytics";
import { shiftIsoDate } from "@/lib/utils";

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

interface DashboardShipmentMetrics {
  shippedByDay: DashboardShipmentPoint[];
  shippedToday: number;
  shippedYesterday: number;
  shippedTodayOrders: number;
}

interface DashboardReportMetrics extends DashboardShipmentMetrics {
  spark: DashboardMoneyPoint[];
  moneyCurrency: string;
  periodRevenue: number;
  periodReceived: number;
  receivedToday: number;
  receivedTodayCount: number;
}

interface DashboardDebtMetrics {
  debtTotal: number;
  debtCurrency: string;
  /** Долг в остальных валютах — отдельными парами, к основной не прибавляется. */
  debtOthers: [string, number][];
  overdueTotal: number;
  overdueCurrency: string;
  overdueOthers: [string, number][];
  overdueClients: number;
}

export function adaptDashboardDebt(debt: ReportSummary["debt_now"] | undefined): DashboardDebtMetrics {
  // Основную валюту долга и просрочки выбирает сервер (common.money.primary_currency).
  const debtCurrency = debt?.currency ?? DEFAULT_DASHBOARD_CURRENCY;
  const overdueCurrency = debt?.overdue_currency ?? DEFAULT_DASHBOARD_CURRENCY;

  return {
    debtTotal: amountForCurrency(debt?.by_currency ?? {}, debtCurrency),
    debtCurrency,
    debtOthers: otherCurrencyAmounts(debt?.by_currency ?? {}, debtCurrency),
    overdueTotal: amountForCurrency(debt?.overdue_by_currency ?? {}, overdueCurrency),
    overdueCurrency,
    overdueOthers: otherCurrencyAmounts(debt?.overdue_by_currency ?? {}, overdueCurrency),
    overdueClients: debt?.overdue_clients ?? 0,
  };
}

function normalizedPeriodDays(periodDays: number): number {
  return Math.max(1, Math.trunc(periodDays) || 1);
}

export function dashboardReportRange(currentDay: string, periodDays: number): { from: string; to: string } {
  return { from: shiftIsoDate(currentDay, 1 - normalizedPeriodDays(periodDays)), to: currentDay };
}

function periodSlots<T>(currentDay: string, periodDays: number, create: (label: string) => T): Map<string, T> {
  const { from } = dashboardReportRange(currentDay, periodDays);
  const slots = new Map<string, T>();

  for (let index = 0; index < normalizedPeriodDays(periodDays); index += 1) {
    const day = shiftIsoDate(from, index);
    slots.set(day, create(day.slice(8, 10)));
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
  const moneySlots = periodSlots(currentDay, periodDays, (label) => ({ label, revenue: 0, received: 0 }));

  // Денежную серию строит тот же адаптер, что и график отчётов; здесь только слоты календаря.
  for (const point of reportChartSeries(report.days, moneyCurrency)) {
    const money = moneySlots.get(point.date);
    if (!money) continue;
    money.revenue = point.revenue;
    money.received = point.received;
  }

  const today = report.days.find((day) => day.date === currentDay);
  return {
    ...adaptOperationalShipments(report.days, currentDay, periodDays),
    spark: [...moneySlots.values()],
    moneyCurrency,
    // Итоги периода — серверные: отчёт запрошен за те же даты, что и слоты графика.
    periodRevenue: amountForCurrency(report.shipped.revenue_by_currency, moneyCurrency),
    periodReceived: amountForCurrency(report.income.by_currency, moneyCurrency),
    receivedToday: today ? amountForCurrency(today.received_by_currency, moneyCurrency) : 0,
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
