import type { DashboardOperationalSummary, ReportSummary } from "@/lib/types";
import { amountForCurrency, finiteMoney, otherCurrencyAmounts } from "@/lib/currency-map";

const DEFAULT_DASHBOARD_CURRENCY = "KZT";

type OperationalDay = DashboardOperationalSummary["days"][number];

interface DashboardShipments {
  shippedToday: number;
  shippedTodayOrders: number;
  truckBagsToday: number;
  trainBagsToday: number;
  /** Вес отгруженного сегодня по фасовке, кг. */
  shippedTodayKg: number;
  shippedYesterday: number;
}

interface DashboardIncome {
  receivedToday: number;
  receivedTodayCount: number;
  moneyCurrency: string;
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

/**
 * Сегодня и вчера из отгрузок по дням, которые посчитал сервер (откаты и
 * повторные отгрузки он уже учёл). День без отгрузок в ответе отсутствует — нули.
 */
export function todayShipments(days: readonly OperationalDay[], today: string, yesterday: string): DashboardShipments {
  const current = days.find((day) => day.date === today);
  const previous = days.find((day) => day.date === yesterday);
  return {
    shippedToday: finiteMoney(current?.bags ?? 0),
    shippedTodayOrders: finiteMoney(current?.orders ?? 0),
    truckBagsToday: finiteMoney(current?.truck_bags ?? 0),
    trainBagsToday: finiteMoney(current?.train_bags ?? 0),
    shippedTodayKg: finiteMoney(current?.kg ?? 0),
    shippedYesterday: finiteMoney(previous?.bags ?? 0),
  };
}

/** Поступления за сегодня: отчёт запрошен ровно за сегодняшний день, валюту выбрал сервер. */
export function todayIncome(report: ReportSummary | null | undefined): DashboardIncome {
  const moneyCurrency = report?.income.currency || DEFAULT_DASHBOARD_CURRENCY;
  return {
    receivedToday: amountForCurrency(report?.income.by_currency ?? {}, moneyCurrency),
    receivedTodayCount: report?.income.payments ?? 0,
    moneyCurrency,
  };
}
