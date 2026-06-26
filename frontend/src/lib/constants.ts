export const ORDER_STATUS_LABELS: Record<string, string> = {
  draft: "Черновик",
  pending: "На рассмотрении",
  confirmed: "Ожидает въезда",
  arrived: "Ожидает загрузки",
  loading: "Загрузка",
  loaded: "Загружен",
  shipped: "Отгружен",
  rejected: "Отклонён",
  cancelled: "Отменён",
};

export const ORDER_STATUS_TONE: Record<string, "muted" | "primary" | "success" | "warning" | "destructive"> = {
  draft: "muted",
  pending: "warning",
  confirmed: "warning",
  arrived: "warning",
  loading: "warning",
  loaded: "primary",
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

export const ROLE_LABELS: Record<string, string> = {
  manager: "Менеджер",
  accountant: "Бухгалтер",
  operator: "Оператор",
  boss: "Начальник",
};
