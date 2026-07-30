/** Convert an API money value without allowing NaN to poison aggregates. */
export function finiteMoney(value: unknown): number {
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) ? number : 0;
}

/**
 * Read one currency from a server breakdown. The legacy flat value is valid
 * only when an older response has no breakdown at all.
 */
export function amountForCurrency(
  byCurrency: Readonly<Record<string, string | number>>,
  legacy: string | number,
  currency: string,
): number {
  if (Object.prototype.hasOwnProperty.call(byCurrency, currency)) return finiteMoney(byCurrency[currency]);
  return Object.keys(byCurrency).length === 0 ? finiteMoney(legacy) : 0;
}

export function otherCurrencyAmounts(
  byCurrency: Readonly<Record<string, string | number>>,
  primary: string,
): [currency: string, amount: number][] {
  return Object.entries(byCurrency)
    .filter(([currency]) => currency !== primary)
    .map(([currency, value]) => [currency, finiteMoney(value)] as [string, number])
    .filter(([, value]) => value !== 0);
}

/**
 * Prefer the configured business currency, then a deterministic available
 * currency. Nominal KZT and USD amounts are never compared because that would
 * silently invent an exchange rate.
 */
export function primaryMoneyCurrency(byCurrency: Readonly<Record<string, string | number>>, fallback = "KZT"): string {
  if (finiteMoney(byCurrency[fallback]) !== 0) return fallback;
  const available = Object.keys(byCurrency)
    .filter((currency) => finiteMoney(byCurrency[currency]) !== 0)
    .sort();
  return available[0] ?? fallback;
}
