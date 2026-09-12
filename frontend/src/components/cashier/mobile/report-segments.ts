import type { IncomeSummary } from "@/components/cashier/totals";
import type { DonutSegment } from "@/components/ui/donut-chart";
import { PAYMENT_METHOD_LABELS } from "@/lib/constants";
import { amountForCurrency, finiteMoney } from "@/lib/currency-map";

export type Breakdown = "departments" | "methods";

/** Цвета способов — токены темы, чтобы кольцо читалось и в тёмной теме. */
export const METHOD_COLORS: Record<string, string> = {
  cash: "var(--success)",
  kaspi: "var(--ring)",
  invoice: "var(--warning)",
  card: "var(--muted-foreground)",
};

export interface ReportBreakdown {
  segments: DonutSegment[];
  /** Число оплат по ключу сегмента, если бэкенд его отдаёт. */
  counts: Record<string, number>;
  /** true — старый бэкенд без by_method: показаны только «Наличные/Безналичные». */
  fallback: boolean;
}

const byValueDesc = (a: DonutSegment, b: DonutSegment) => b.value - a.value;

export function reportSegments(summary: IncomeSummary | null, breakdown: Breakdown, currency: string): ReportBreakdown {
  if (!summary) return { segments: [], counts: {}, fallback: false };
  if (breakdown === "departments") {
    const counts: Record<string, number> = {};
    const segments = (summary.departments ?? []).map((row) => {
      if (row.payments !== undefined) counts[row.code] = row.payments;
      return {
        key: row.code,
        label: row.name,
        value: finiteMoney(row.net_by_currency[currency] ?? 0),
        color: row.color,
      };
    });
    return { segments: segments.sort(byValueDesc), counts, fallback: false };
  }
  const byMethod = summary.income.by_method_by_currency;
  if (byMethod) {
    const segments = Object.entries(byMethod[currency] ?? {}).map(([method, value]) => ({
      key: method,
      label: PAYMENT_METHOD_LABELS[method] ?? method,
      value: finiteMoney(value),
      color: METHOD_COLORS[method] ?? "var(--muted-foreground)",
    }));
    return { segments: segments.sort(byValueDesc), counts: summary.income.payments_by_method ?? {}, fallback: false };
  }
  // Старый бэкенд без разбивки по способам: показываем хотя бы нал/безнал.
  return {
    segments: [
      {
        key: "cash",
        label: "Наличные",
        value: amountForCurrency(summary.income.cash_by_currency ?? {}, summary.income.cash, currency),
        color: METHOD_COLORS.cash,
      },
      {
        key: "cashless",
        label: "Безналичные",
        value: amountForCurrency(summary.income.cashless_by_currency ?? {}, summary.income.cashless, currency),
        color: METHOD_COLORS.kaspi,
      },
    ],
    counts: {},
    fallback: true,
  };
}
