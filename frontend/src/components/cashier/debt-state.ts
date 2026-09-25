import { PAYMENT_STATUS_LABELS, PAYMENT_STATUS_TONE, type BadgeTone } from "@/lib/constants";
import type { ClientDebt } from "@/lib/types";

export function debtPaymentState(row: ClientDebt): { label: string; tone: BadgeTone } {
  if (row.partial_count > 0 && row.unpaid_count > 0) {
    return { label: "Есть частичные", tone: PAYMENT_STATUS_TONE.partial };
  }
  const status = row.partial_count > 0 ? "partial" : "unpaid";
  return { label: PAYMENT_STATUS_LABELS[status], tone: PAYMENT_STATUS_TONE[status] };
}

/** Локальный поиск по списку должников: имя или телефон. */
export function matchesDebtQuery(row: ClientDebt, query: string): boolean {
  return !query || `${row.client_name} ${row.client_phone}`.toLowerCase().includes(query.toLowerCase());
}

/** Карточка долга клиента: заказы открываются внутри неё. */
export const debtHref = (row: Pick<ClientDebt, "client_id">) => `/accounting/debts/clients/${row.client_id}`;
