import type { IncomeSummary } from "@/components/cashier/totals";
import type { DonutSegment } from "@/components/ui/donut-chart";
import { amountForCurrency, finiteMoney } from "@/lib/currency-map";

export type Breakdown = "departments" | "methods";

/** Цвета способов — токены темы, чтобы кольцо читалось и в тёмной теме. */
const METHOD_COLORS: Record<string, string> = {
  cash: "var(--success)",
  kaspi: "var(--ring)",
  invoice: "var(--warning)",
};

interface ReportBreakdown {
  segments: DonutSegment[];
  /** Число оплат по ключу сегмента. */
  counts: Record<string, number>;
}

const byValueDesc = (a: DonutSegment, b: DonutSegment) => b.value - a.value;

export function reportSegments(summary: IncomeSummary | null, breakdown: Breakdown, currency: string): ReportBreakdown {
  if (!summary) return { segments: [], counts: {} };
  if (breakdown === "departments") {
    const counts: Record<string, number> = {};
    const segments = summary.departments.map((row) => {
      counts[row.code] = row.payments;
      return {
        key: row.code,
        label: row.name,
        value: amountForCurrency(row.net_by_currency, currency),
        color: row.color,
      };
    });
    return { segments: segments.sort(byValueDesc), counts };
  }
  const segments = Object.entries(summary.income.by_method_by_currency[currency] ?? {}).map(([method, value]) => ({
    key: method,
    label: summary.income.method_labels[method] ?? method,
    value: finiteMoney(value),
    color: METHOD_COLORS[method] ?? "var(--muted-foreground)",
  }));
  return { segments: segments.sort(byValueDesc), counts: summary.income.payments_by_method };
}
