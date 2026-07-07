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

export const DEPARTMENT_LABELS: Record<string, string> = {
  main: "Отдел 1",
  field: "Сити",
};

// Цепочка подтверждения оплаты: каждый шаг фиксируется с автором и временем.
export const PAYMENT_STAGE_LABELS: Record<string, string> = {
  requested: "Запрошена",
  received: "Принята",
  accountant_ok: "Сверена бухгалтером",
  confirmed: "Подтверждена кассиром",
  rejected: "Отклонена",
};

export const PAYMENT_STAGE_TONE: Record<string, "muted" | "primary" | "success" | "warning" | "destructive"> = {
  requested: "muted",
  received: "warning",
  accountant_ok: "primary",
  confirmed: "success",
  rejected: "destructive",
};

export const PAYMENT_METHOD_LABELS: Record<string, string> = {
  cash: "Наличные",
  card: "Карта",
  kaspi: "Kaspi",
  debt: "Долг",
};
