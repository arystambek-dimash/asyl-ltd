/** Четыре понятных статуса; ключ группы = реальный статус модели,
 * поэтому выбор в селекте отправляется на бэк без маппинга. */
export const ORDER_STATUS_GROUPS: Record<string, string> = {
  draft: "pending",
  pending: "pending",
  confirmed: "confirmed",
  arrived: "confirmed",
  loading: "confirmed",
  loaded: "shipped",
  shipped: "shipped",
  rejected: "cancelled",
  cancelled: "cancelled",
};

export const ORDER_STATUS_LABELS: Record<string, string> = {
  draft: "На рассмотрении",
  pending: "На рассмотрении",
  confirmed: "Ожидает загрузки",
  arrived: "Ожидает загрузки",
  loading: "Ожидает загрузки",
  loaded: "Завершён",
  shipped: "Завершён",
  rejected: "Отменён",
  cancelled: "Отменён",
};

export const ORDER_PUBLIC_STATUSES = ["pending", "confirmed", "shipped", "cancelled"] as const;

export function orderStatusGroup(status: string): string {
  return ORDER_STATUS_GROUPS[status] ?? status;
}

export function orderStatusLabel(status: string): string {
  return ORDER_STATUS_LABELS[status] ?? status;
}

/** Переводит внутренние коды в сообщениях журнала и сворачивает скрытые этапы. */
export function translateOrderStatusMessage(
  message: string,
  payload?: Record<string, unknown>,
): string {
  const from = typeof payload?.from === "string" ? payload.from : null;
  const to = typeof payload?.to === "string" ? payload.to : null;
  if (to) {
    const fromLabel = from ? orderStatusLabel(from) : null;
    const toLabel = orderStatusLabel(to);
    return fromLabel && fromLabel !== toLabel
      ? `Статус заказа: ${fromLabel} → ${toLabel}`
      : `Статус заказа: ${toLabel}`;
  }
  return message.replace(
    /\b(draft|pending|confirmed|arrived|loading|loaded|shipped|rejected|cancelled)\b/g,
    (status) => orderStatusLabel(status),
  );
}

export const ORDER_STATUS_TONE: Record<string, "muted" | "primary" | "success" | "warning" | "destructive"> = {
  draft: "warning",
  pending: "warning",
  confirmed: "warning",
  arrived: "warning",
  loading: "warning",
  loaded: "success",
  shipped: "success",
  rejected: "destructive",
  cancelled: "destructive",
};

export const NON_FINANCIAL_ORDER_STATUSES = new Set(["draft", "pending", "rejected", "cancelled"]);

export function isFinancialOrderStatus(status: string): boolean {
  return !NON_FINANCIAL_ORDER_STATUSES.has(status);
}

export const PAYMENT_STATUS_LABELS: Record<string, string> = {
  unpaid: "Не оплачен",
  partial: "Частично оплачен",
  settled: "Оплачен",
};

export const PAYMENT_STATUS_TONE: Record<string, "muted" | "primary" | "success" | "warning" | "destructive"> = {
  unpaid: "destructive",
  partial: "warning",
  settled: "success",
};

// Цепочка подтверждения оплаты: каждый шаг фиксируется с автором и временем.
// accountant_ok — легаси-стадия (схлопнута в confirmed), подпись для старых записей.
export const PAYMENT_STAGE_LABELS: Record<string, string> = {
  requested: "Ожидается",
  received: "На проверке",
  accountant_ok: "Подтверждена",
  confirmed: "Оплачено",
  rejected: "Отклонена",
};

export const PAYMENT_STAGE_TONE: Record<string, "muted" | "primary" | "success" | "warning" | "destructive"> = {
  requested: "muted",
  received: "warning",
  accountant_ok: "success",
  confirmed: "success",
  rejected: "destructive",
};

export const PAYMENT_METHOD_LABELS: Record<string, string> = {
  invoice: "Счет на оплату",
  kaspi: "Kaspi",
  cash: "Наличные",
  debt: "Долг",
  // Легаси-способ внутренних банковских оплат.
  card: "Карта",
};

export const CASHIER_PAYMENT_METHODS = ["cash", "kaspi", "invoice"] as const;
export const CASHIER_PAYMENT_METHOD_LABELS: Record<string, string> = {
  cash: "Наличные",
  kaspi: "QR",
  invoice: "Счет на оплату",
};

export const PORTAL_PAYMENT_METHOD_LABELS: Record<string, string> = {
  pending: "Способ не выбран",
  invoice: "Счет на оплату",
  kaspi: "Каспи",
  cash: "Наличными",
  debt: "В долг",
};
