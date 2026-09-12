import type { ClientDebt } from "@/lib/types";

export function debtPaymentState(row: ClientDebt) {
  if (row.partial_count > 0 && row.unpaid_count > 0) {
    return { label: "Есть частичные", tone: "warning" as const };
  }
  if (row.partial_count > 0) {
    return { label: "Частично оплачен", tone: "warning" as const };
  }
  return { label: "Не оплачен", tone: "destructive" as const };
}

/** Локальный поиск по списку должников: имя или телефон. */
export function matchesDebtQuery(row: ClientDebt, query: string): boolean {
  return !query || `${row.client_name} ${row.client_phone}`.toLowerCase().includes(query.toLowerCase());
}
