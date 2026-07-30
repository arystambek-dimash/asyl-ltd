import { finiteMoney, otherCurrencyAmounts } from "@/lib/currency-map";
import type { ReportDay, ReportSummary } from "@/lib/types";

/** Раскладка отгруженного за период: сразу оплачено против ушедшего в долг. */
export interface PaidSplit {
  currency: string;
  revenue: number;
  debt: number;
  paidNow: number;
  /** Доля долга в основной валюте, целые проценты; null — отгрузок не было. */
  debtSharePct: number | null;
  /** Те же три числа для остальных валют — их нельзя смешивать с основной. */
  others: { currency: string; revenue: number; debt: number; paidNow: number }[];
}

function splitFor(revenue: number, debt: number) {
  // Кривые данные (долг больше отгрузки) не должны рисовать отрицательное
  // «оплачено сразу» — зажимаем в ноль, а долю в 100%.
  return { revenue, debt, paidNow: Math.max(revenue - debt, 0) };
}

export function paidSplit(shipped: ReportSummary["shipped"]): PaidSplit {
  const currency = shipped.currency || "KZT";
  const primary = splitFor(
    finiteMoney(shipped.revenue_by_currency[currency] ?? shipped.revenue),
    finiteMoney(shipped.debt_amount_by_currency[currency] ?? 0),
  );
  const others = otherCurrencyAmounts(shipped.revenue_by_currency, currency).map(([other, revenue]) => ({
    currency: other,
    ...splitFor(revenue, finiteMoney(shipped.debt_amount_by_currency[other] ?? 0)),
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

/** Дни API идут по убыванию даты — график требует хронологию и одну валюту. */
export function reportChartSeries(days: readonly ReportDay[], currency: string): ReportChartPoint[] {
  return days
    .map((day) => ({
      date: day.date,
      label: day.date.slice(8, 10) + "." + day.date.slice(5, 7),
      revenue: finiteMoney(day.revenue_by_currency[currency] ?? 0),
      received: finiteMoney(day.received_by_currency[currency] ?? 0),
    }))
    .sort((a, b) => (a.date < b.date ? -1 : 1));
}
