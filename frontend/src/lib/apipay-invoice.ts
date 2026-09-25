/** Статусы счёта ApiPay (ApiPayInvoice.status, backend/apps/orders/apipay.py) — один словарь для кассы, POS и кабинета. */

const LABELS: Record<string, string> = {
  creating: "Создаётся",
  processing: "Ожидает оплаты",
  pending: "Ожидает оплаты",
  paid: "Оплачен",
  cancelling: "Отменяется",
  cancelled: "Отменён",
  expired: "Истёк",
  superseded: "Заменён",
  error: "Ошибка",
  partially_refunded: "Частично возвращён",
};

const PAYABLE = new Set(["creating", "processing", "pending"]);
const CLOSED = new Set(["expired", "cancelled", "error", "superseded"]);

/** Счёт ещё можно оплатить: QR и счёт на телефоне живы. */
export function invoiceIsPayable(status: string): boolean {
  return PAYABLE.has(status);
}

/** Счёт ещё не закрыт: можно оплатить или ждём подтверждения отмены. */
export function invoiceIsActive(status: string): boolean {
  return PAYABLE.has(status) || status === "cancelling";
}

/** Счёт закрыт без денег. */
export function invoiceIsClosed(status: string): boolean {
  return CLOSED.has(status);
}

export function invoiceStatusLabel(status: string): string {
  return LABELS[status] ?? status;
}
