import { amountForCurrency, finiteMoney, otherCurrencyAmounts } from "@/lib/currency-map";
import type { ReportDay, ReportSummary } from "@/lib/types";
import { formatCurrency, formatIsoDayMonth } from "@/lib/utils";

/** Текущее финансовое состояние заказов, отгруженных в выбранном периоде. */
interface ShipmentSettlement {
  currency: string;
  revenue: number;
  debt: number;
  paidToDate: number;
  /** Доля долга в основной валюте, целые проценты; null — отгрузок не было. */
  debtSharePct: number | null;
  /** Те же числа для остальных валют — их нельзя смешивать с основной. */
  others: { currency: string; revenue: number; debt: number; paidToDate: number }[];
}

function settlementFor(shipped: ReportSummary["shipped"], currency: string) {
  return {
    revenue: Math.max(amountForCurrency(shipped.revenue_by_currency, currency), 0),
    debt: Math.max(amountForCurrency(shipped.debt_amount_by_currency, currency), 0),
    paidToDate: Math.max(amountForCurrency(shipped.paid_amount_by_currency, currency), 0),
  };
}

export function shipmentSettlement(shipped: ReportSummary["shipped"]): ShipmentSettlement {
  const currency = shipped.currency || "KZT";
  const primary = settlementFor(shipped, currency);
  const others = otherCurrencyAmounts(shipped.revenue_by_currency, currency).map(([other]) => ({
    currency: other,
    ...settlementFor(shipped, other),
  }));
  return {
    currency,
    ...primary,
    debtSharePct: primary.revenue > 0 ? Math.min(Math.round((primary.debt / primary.revenue) * 100), 100) : null,
    others,
  };
}

interface IncomeTotals {
  currency: string;
  total: number;
  cash: number;
  cashless: number;
  gross: number;
  refunded: number;
  payments: number;
  otherCurrencies: [string, number][];
  otherRefunds: [string, number][];
  grossFor: (currency: string) => number;
}

/** Итоги поступлений в основной валюте; прочие валюты — отдельными парами, без сложения. */
export function incomeTotals(summary: Pick<ReportSummary, "income"> | null): IncomeTotals {
  const income = summary?.income;
  const byCurrency = income?.by_currency ?? {};
  const currency = income?.currency || Object.keys(byCurrency)[0] || "KZT";
  const pick = (map: Record<string, string> | undefined) => amountForCurrency(map ?? {}, currency);
  return {
    currency,
    total: pick(byCurrency),
    cash: pick(income?.cash_by_currency),
    cashless: pick(income?.cashless_by_currency),
    gross: pick(income?.gross_by_currency),
    refunded: pick(income?.refunded_by_currency),
    payments: income?.payments ?? 0,
    otherCurrencies: otherCurrencyAmounts(byCurrency, currency),
    otherRefunds: otherCurrencyAmounts(income?.refunded_by_currency ?? {}, currency),
    grossFor: (unit) => amountForCurrency(income?.gross_by_currency ?? {}, unit),
  };
}

/** Строки карточки поступлений после нал/безнала: прочие валюты и возвраты по каждой валюте. */
export function incomeDetailRows(income: IncomeTotals): { label: string; value: string }[] {
  return [
    ...income.otherCurrencies.map(([currency, value]) => ({
      label: "Также чистыми",
      value: formatCurrency(value, currency),
    })),
    ...(income.refunded > 0
      ? [
          { label: "Поступило до возвратов", value: formatCurrency(income.gross, income.currency) },
          { label: "Возвращено", value: formatCurrency(income.refunded, income.currency) },
        ]
      : []),
    ...income.otherRefunds.flatMap(([currency, value]) => [
      { label: `Поступило до возвратов, ${currency}`, value: formatCurrency(income.grossFor(currency), currency) },
      { label: `Возвращено, ${currency}`, value: formatCurrency(value, currency) },
    ]),
  ];
}

interface ReportChartPoint {
  date: string;
  label: string;
  revenue: number;
  received: number;
}

/** Валюты, в которых графику действительно есть что показать. */
export function reportChartCurrencies(data: ReportSummary): string[] {
  const active = new Set<string>();
  for (const day of data.days) {
    for (const [currency, value] of Object.entries(day.revenue_by_currency)) {
      if (finiteMoney(value) !== 0) active.add(currency);
    }
    for (const [currency, value] of Object.entries(day.received_by_currency)) {
      if (finiteMoney(value) !== 0) active.add(currency);
    }
  }
  const preferred = data.shipped.currency || data.income.currency || "KZT";
  if (active.size === 0) active.add(preferred);
  return [...active].sort((left, right) => {
    if (left === preferred) return -1;
    if (right === preferred) return 1;
    return left.localeCompare(right);
  });
}

/** Дни API идут по убыванию даты — график требует хронологию и одну валюту. */
export function reportChartSeries(days: readonly ReportDay[], currency: string): ReportChartPoint[] {
  return days
    .map((day) => ({
      date: day.date,
      label: formatIsoDayMonth(day.date),
      revenue: amountForCurrency(day.revenue_by_currency, currency),
      received: amountForCurrency(day.received_by_currency, currency),
    }))
    .sort((a, b) => (a.date < b.date ? -1 : 1));
}
