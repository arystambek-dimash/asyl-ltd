import { amountForCurrency, otherCurrencyAmounts, primaryMoneyCurrency } from "@/lib/currency-map";
import type { ClientDebt, PaymentQueueItem, ReportSummary } from "@/lib/types";
import { sumDebtByCurrency, sumMoneyByCurrency } from "@/lib/utils";

export type IncomeSummary = Pick<ReportSummary, "income" | "departments" | "from" | "to">;
/** Строка `/orders/payments-queue/?summary=1`: сумма и число оплат на валюту+способ. */
export type QueueTotal = Pick<PaymentQueueItem, "amount" | "currency" | "method"> & { count?: number };

export interface IncomeTotals {
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
export function incomeTotals(summary: IncomeSummary | null): IncomeTotals {
  const income = summary?.income;
  const byCurrency = income?.by_currency ?? {};
  const currency = income?.currency || Object.keys(byCurrency)[0] || "KZT";
  const pick = (map: Record<string, string> | undefined, legacy: string | undefined) =>
    amountForCurrency(map ?? {}, legacy ?? "0", currency);
  return {
    currency,
    total: pick(byCurrency, income?.total),
    cash: pick(income?.cash_by_currency, income?.cash),
    cashless: pick(income?.cashless_by_currency, income?.cashless),
    gross: pick(income?.gross_by_currency, income?.gross ?? income?.total),
    refunded: pick(income?.refunded_by_currency, income?.refunded),
    payments: income?.payments ?? 0,
    otherCurrencies: otherCurrencyAmounts(byCurrency, currency),
    otherRefunds: otherCurrencyAmounts(income?.refunded_by_currency ?? {}, currency),
    grossFor: (unit) => amountForCurrency(income?.gross_by_currency ?? {}, "0", unit),
  };
}

export interface QueueTotals {
  currency: string;
  total: number;
  cash: number;
  count: number;
  other: [string, number][];
}

export function queueTotals(rows: readonly QueueTotal[]): QueueTotals {
  const byCurrency = sumMoneyByCurrency(
    rows,
    (row) => row.amount,
    (row) => row.currency,
  );
  const cashByCurrency = sumMoneyByCurrency(
    rows.filter((row) => row.method === "cash"),
    (row) => row.amount,
    (row) => row.currency,
  );
  const currency = primaryMoneyCurrency(byCurrency);
  return {
    currency,
    total: byCurrency[currency] ?? 0,
    cash: cashByCurrency[currency] ?? 0,
    count: rows.reduce((sum, row) => sum + (row.count ?? 1), 0),
    other: Object.entries(byCurrency).filter(([unit, value]) => unit !== currency && value > 0),
  };
}

export interface DebtTotals {
  currency: string;
  total: number;
  other: [string, number][];
  clients: number;
  overdue: number;
}

export function debtTotals(rows: readonly ClientDebt[]): DebtTotals {
  const byCurrency = sumDebtByCurrency(rows);
  const currency = primaryMoneyCurrency(byCurrency);
  return {
    currency,
    total: byCurrency[currency] ?? 0,
    other: Object.entries(byCurrency).filter(([unit, value]) => unit !== currency && value > 0),
    clients: rows.length,
    overdue: rows.filter((row) => row.overdue_count > 0).length,
  };
}
