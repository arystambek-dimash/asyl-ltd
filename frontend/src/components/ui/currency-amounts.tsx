import type { ReactNode } from "react";
import { finiteMoney } from "@/lib/currency-map";
import { cn, formatCurrency } from "@/lib/utils";

type MoneyValue = string | number;

interface CurrencyAmountsProps {
  byCurrency?: Readonly<Record<string, MoneyValue>>;
  fallbackAmount?: MoneyValue;
  fallbackCurrency?: string;
  empty?: ReactNode;
  className?: string;
  amountClassName?: string;
}

/**
 * Render monetary values without ever adding unlike currencies.
 *
 * Non-zero currencies are shown on separate lines. If a server breakdown is
 * empty, the legacy flat value is used only with its explicit fallback
 * currency. This keeps the compatibility path in one place.
 */
export function CurrencyAmounts({
  byCurrency = {},
  fallbackAmount,
  fallbackCurrency = "KZT",
  empty = "—",
  className,
  amountClassName,
}: CurrencyAmountsProps) {
  const rawEntries = Object.entries(byCurrency).map(([currency, value]) => [currency, finiteMoney(value)] as const);
  const nonZeroEntries = rawEntries.filter(([, amount]) => amount !== 0);

  let entries: ReadonlyArray<readonly [string, number]> = nonZeroEntries;
  if (entries.length === 0 && rawEntries.length > 0) {
    const zeroCurrency = rawEntries.some(([currency]) => currency === fallbackCurrency)
      ? fallbackCurrency
      : rawEntries.map(([currency]) => currency).sort()[0];
    entries = [[zeroCurrency, 0]];
  } else if (entries.length === 0 && fallbackAmount !== undefined) {
    entries = [[fallbackCurrency, finiteMoney(fallbackAmount)]];
  }

  if (entries.length === 0) {
    return <>{empty}</>;
  }

  const ordered = [...entries].sort(([left], [right]) => {
    if (left === right) return 0;
    if (left === fallbackCurrency) return -1;
    if (right === fallbackCurrency) return 1;
    return left.localeCompare(right);
  });

  return (
    <span className={cn("inline-flex flex-col items-end gap-0.5", className)}>
      {ordered.map(([currency, amount], index) => (
        <span
          key={currency}
          className={cn(
            "whitespace-nowrap",
            // Две валюты — не одна сумма: вторую показываем мельче и
            // приглушённо, чтобы ₸ и $ не выглядели двумя большими итогами.
            index > 0 && "text-xs font-normal text-[var(--muted-foreground)]",
            amountClassName,
          )}
        >
          {index > 0 && "+ "}
          {formatCurrency(amount, currency)}
        </span>
      ))}
    </span>
  );
}
