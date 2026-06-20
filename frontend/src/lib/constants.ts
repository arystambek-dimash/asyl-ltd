export const ORDER_STATUS_LABELS: Record<string, string> = {
  draft: "Черновик",
  confirmed: "Подтверждён",
  paid: "Оплачен",
  arrived: "Прибыл",
  loading: "Загрузка",
  shipped: "Отгружен",
  cancelled: "Отменён",
};

export const ORDER_STATUS_TONE: Record<string, "muted" | "primary" | "success" | "warning" | "destructive"> = {
  draft: "muted",
  confirmed: "warning",
  paid: "primary",
  arrived: "warning",
  loading: "warning",
  shipped: "success",
  cancelled: "destructive",
};

export const ROLE_LABELS: Record<string, string> = {
  manager: "Менеджер",
  accountant: "Бухгалтер",
  operator: "Оператор",
  boss: "Начальник",
};
