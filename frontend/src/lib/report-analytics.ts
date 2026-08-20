import { finiteMoney, otherCurrencyAmounts } from "@/lib/currency-map";
import type { ReportDay, ReportSummary } from "@/lib/types";

/** Текущее финансовое состояние заказов, отгруженных в выбранном периоде. */
export interface ShipmentSettlement {
  currency: string;
  revenue: number;
  debt: number;
  paidToDate: number;
  awaiting: number;
  /** Доля долга в основной валюте, целые проценты; null — отгрузок не было. */
  debtSharePct: number | null;
  /** Те же числа для остальных валют — их нельзя смешивать с основной. */
  others: { currency: string; revenue: number; debt: number; paidToDate: number; awaiting: number }[];
}

function settlementFor(revenue: number, paid: number, debt: number, awaiting: number, explicit: boolean) {
  const normalizedRevenue = Math.max(revenue, 0);
  const normalizedDebt = Math.max(debt, 0);
  if (!explicit) {
    // Совместимость со старым API: раньше отдельных paid/awaiting не было.
    return {
      revenue: normalizedRevenue,
      debt: normalizedDebt,
      paidToDate: Math.max(normalizedRevenue - normalizedDebt, 0),
      awaiting: 0,
    };
  }
  return {
    revenue: normalizedRevenue,
    debt: normalizedDebt,
    paidToDate: Math.max(paid, 0),
    awaiting: Math.max(awaiting, 0),
  };
}

export function shipmentSettlement(shipped: ReportSummary["shipped"]): ShipmentSettlement {
  const currency = shipped.currency || "KZT";
  const primaryExplicit =
    Object.prototype.hasOwnProperty.call(shipped.paid_amount_by_currency ?? {}, currency) ||
    Object.prototype.hasOwnProperty.call(shipped.awaiting_amount_by_currency ?? {}, currency) ||
    shipped.paid_amount !== undefined ||
    shipped.awaiting_amount !== undefined;
  const primary = settlementFor(
    finiteMoney(shipped.revenue_by_currency[currency] ?? shipped.revenue),
    finiteMoney(shipped.paid_amount_by_currency?.[currency] ?? shipped.paid_amount),
    finiteMoney(shipped.debt_amount_by_currency[currency] ?? 0),
    finiteMoney(shipped.awaiting_amount_by_currency?.[currency] ?? shipped.awaiting_amount),
    primaryExplicit,
  );
  const others = otherCurrencyAmounts(shipped.revenue_by_currency, currency).map(([other, revenue]) => ({
    currency: other,
    ...settlementFor(
      revenue,
      finiteMoney(shipped.paid_amount_by_currency?.[other] ?? 0),
      finiteMoney(shipped.debt_amount_by_currency[other] ?? 0),
      finiteMoney(shipped.awaiting_amount_by_currency?.[other] ?? 0),
      Object.prototype.hasOwnProperty.call(shipped.paid_amount_by_currency ?? {}, other) ||
        Object.prototype.hasOwnProperty.call(shipped.awaiting_amount_by_currency ?? {}, other),
    ),
  }));
  return {
    currency,
    ...primary,
    debtSharePct: primary.revenue > 0 ? Math.min(Math.round((primary.debt / primary.revenue) * 100), 100) : null,
    others,
  };
}

export interface ReportChartPoint {
  date: string;
  label: string;
  revenue: number;
  received: number;
}

/** Валюты, в которых графику действительно есть что показать. */
export function reportChartCurrencies(data: ReportSummary): string[] {
  const active = new Set<string>();
  let hasRevenueMap = false;
  let hasReceivedMap = false;
  for (const day of data.days) {
    hasRevenueMap ||= Object.keys(day.revenue_by_currency).length > 0;
    hasReceivedMap ||= Object.keys(day.received_by_currency).length > 0;
    for (const [currency, value] of Object.entries(day.revenue_by_currency)) {
      if (finiteMoney(value) !== 0) active.add(currency);
    }
    for (const [currency, value] of Object.entries(day.received_by_currency)) {
      if (finiteMoney(value) !== 0) active.add(currency);
    }
  }
  if (!hasRevenueMap && finiteMoney(data.shipped.revenue) !== 0) {
    active.add(data.shipped.currency || "KZT");
  }
  if (!hasReceivedMap && finiteMoney(data.income.total) !== 0) {
    active.add(data.income.currency || "KZT");
  }
  const preferred = data.shipped.currency || data.income.currency || "KZT";
  if (active.size === 0) active.add(preferred);
  return [...active].sort((left, right) => {
    if (left === preferred) return -1;
    if (right === preferred) return 1;
    return left.localeCompare(right);
  });
}

function chartAmount(
  byCurrency: Readonly<Record<string, string | number>>,
  legacy: string | number,
  currency: string,
  legacyCurrency: string,
): number {
  if (Object.keys(byCurrency).length === 0) {
    return currency === legacyCurrency ? finiteMoney(legacy) : 0;
  }
  return finiteMoney(byCurrency[currency] ?? 0);
}

/** Дни API идут по убыванию даты — график требует хронологию и одну валюту. */
export function reportChartSeries(
  days: readonly ReportDay[],
  currency: string,
  revenueCurrency = currency,
  incomeCurrency = currency,
): ReportChartPoint[] {
  return days
    .map((day) => ({
      date: day.date,
      label: day.date.slice(8, 10) + "." + day.date.slice(5, 7),
      revenue: chartAmount(day.revenue_by_currency, day.revenue, currency, revenueCurrency),
      received: chartAmount(day.received_by_currency, day.received, currency, incomeCurrency),
    }))
    .sort((a, b) => (a.date < b.date ? -1 : 1));
}
