import { CurrencyAmounts } from "@/components/ui/currency-amounts";
import { finiteMoney, positiveAmounts } from "@/lib/currency-map";
import type { Client } from "@/lib/types";
import { formatCurrency } from "@/lib/utils";

type ClientMoney = Pick<
  Client,
  "currency" | "debt_total" | "debt_currency" | "debt_by_currency" | "overpaid_by_currency"
>;

/** Сколько мы должны клиенту в основной валюте его долга — для сортировки «ниже нуля». */
export function owedToClientAmount(client: ClientMoney): number {
  return finiteMoney(client.overpaid_by_currency?.[client.debt_currency ?? client.currency]);
}

/**
 * «Задолженность» клиента в списке: его долг — красным; переплата («мы должны
 * клиенту») — со знаком минус, а не «0 ₸». Обе суммы — с сервера, не пересчитываются.
 */
export function ClientDebtAmounts({ client }: { client: ClientMoney }) {
  const owed = positiveAmounts(client.overpaid_by_currency ?? {});
  const hasDebt = finiteMoney(client.debt_total) > 0 || positiveAmounts(client.debt_by_currency ?? {}).length > 0;

  return (
    <span className="inline-flex flex-col items-start gap-0.5 font-medium tabular-nums">
      {(hasDebt || owed.length === 0) && (
        <CurrencyAmounts
          byCurrency={client.debt_by_currency}
          fallbackAmount={client.debt_total}
          fallbackCurrency={client.debt_currency ?? client.currency}
          equal
          className="items-start text-[var(--destructive)]"
        />
      )}
      {owed.map(([currency, amount]) => (
        <span key={currency} className="whitespace-nowrap text-[var(--warning)]">
          −{formatCurrency(amount, currency)}
        </span>
      ))}
      {owed.length > 0 && (
        <span className="text-[11px] font-normal text-[var(--muted-foreground)]">мы должны клиенту</span>
      )}
    </span>
  );
}
