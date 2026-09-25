import { primaryMoneyCurrency } from "@/lib/currency-map";
import type { ClientDebt, PaymentQueueItem, ReportSummary } from "@/lib/types";
import { formatCompactCurrency, sumDebtByCurrency, sumMoneyByCurrency } from "@/lib/utils";

export type IncomeSummary = Pick<ReportSummary, "income" | "departments" | "from" | "to">;
/** Строка `/orders/payments-queue/?summary=1`: сумма и число оплат на валюту+способ. */
export type QueueTotal = Pick<PaymentQueueItem, "amount" | "currency" | "method"> & { count: number };
/** Строка `/orders/awaiting-payment/?summary=1`: остаток и число заказов, которые ждут оплаты, на валюту. */
export type AwaitingTotal = Pick<PaymentQueueItem, "amount" | "currency"> & { count: number };

interface QueueTotals {
  currency: string;
  total: number;
  cash: number;
  count: number;
  other: [string, number][];
}

export function queueTotals(rows: readonly (QueueTotal | AwaitingTotal)[]): QueueTotals {
  const byCurrency = sumMoneyByCurrency(
    rows,
    (row) => row.amount,
    (row) => row.currency,
  );
  const cashByCurrency = sumMoneyByCurrency(
    rows.filter((row) => "method" in row && row.method === "cash"),
    (row) => row.amount,
    (row) => row.currency,
  );
  const currency = primaryMoneyCurrency(byCurrency);
  return {
    currency,
    total: byCurrency[currency] ?? 0,
    cash: cashByCurrency[currency] ?? 0,
    count: rows.reduce((sum, row) => sum + row.count, 0),
    other: Object.entries(byCurrency).filter(([unit, value]) => unit !== currency && value > 0),
  };
}

interface DebtTotals {
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

/** Итог по валютам коротко и без сложения валют: «1,2 млн ₸ + 500 $». */
export function formatCompactTotals({
  total,
  currency,
  other,
}: Pick<DebtTotals, "total" | "currency" | "other">): string {
  return [
    formatCompactCurrency(total, currency),
    ...other.map(([unit, value]) => formatCompactCurrency(value, unit)),
  ].join(" + ");
}
