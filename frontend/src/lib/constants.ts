import type { Payment } from "./types";

/** Тон бейджа (`components/ui/badge`) — общий для всех словарей тонов. */
export type BadgeTone = "muted" | "primary" | "success" | "warning" | "destructive" | "outline";

/** Пять понятных статусов; ключ группы = реальный статус модели,
 * поэтому выбор в селекте отправляется на бэк без маппинга. */
const ORDER_STATUS_GROUPS: Record<string, string> = {
  draft: "pending",
  pending: "pending",
  confirmed: "confirmed",
  arrived: "confirmed",
  loading: "confirmed",
  loaded: "loaded",
  shipped: "shipped",
  rejected: "cancelled",
  cancelled: "cancelled",
};

/** Подписи пяти публичных статусов; сырой статус переводится через `orderStatusLabel`. */
export const ORDER_STATUS_LABELS: Record<string, string> = {
  pending: "На рассмотрении",
  confirmed: "Ожидает загрузки",
  loaded: "Готов к выезду",
  shipped: "Отгружено",
  cancelled: "Отменён",
};

/** Единственная карта цветов статусов заказа: бейдж, селект и полоса долей в /orders
 * берут цвет отсюда. «На рассмотрении» и «Ожидает загрузки» различаются. */
const ORDER_STATUS_TONE: Record<string, BadgeTone> = {
  pending: "muted",
  confirmed: "warning",
  loaded: "primary",
  shipped: "success",
  cancelled: "destructive",
};

export const ORDER_PUBLIC_STATUSES = ["pending", "confirmed", "loaded", "shipped", "cancelled"] as const;

// `loaded` показываем в фильтрах, но этот этап ставит только завершение
// погрузки. Ручной селект не должен подменять доменную операцию.
export const ORDER_MANUAL_STATUSES = ["pending", "confirmed", "shipped", "cancelled"] as const;

// Наборы статусов для правил «что можно менять» — те же, что проверяет сервер.
/** Новая заявка: её подтверждают или отклоняют. */
export const ORDER_REVIEWABLE_STATUSES: readonly string[] = ["draft", "pending"];
/** Машина на посту: заехала, грузится или погружена. */
export const ORDER_LOADING_STATUSES: readonly string[] = ["arrived", "loading", "loaded"];
/** Подтверждён и ещё не выехал — ждёт отгрузки. */
export const ORDER_AWAITING_SHIPMENT_STATUSES: readonly string[] = ["confirmed", ...ORDER_LOADING_STATUSES];

export function orderStatusGroup(status: string): string {
  return ORDER_STATUS_GROUPS[status] ?? status;
}

export function orderStatusLabel(status: string): string {
  return ORDER_STATUS_LABELS[orderStatusGroup(status)] ?? status;
}

export function orderStatusTone(status: string): BadgeTone {
  return ORDER_STATUS_TONE[orderStatusGroup(status)] ?? "muted";
}

/** Сообщение журнала бэк пишет уже с подписями статусов (`_status_message`).
 * Сырые коды остались только в старых записях EventLog — их переводим. */
export function translateOrderStatusMessage(message: string): string {
  return message.replace(/\b(draft|pending|confirmed|arrived|loading|loaded|shipped|rejected|cancelled)\b/g, (status) =>
    orderStatusLabel(status),
  );
}

export const PAYMENT_STATUS_LABELS: Record<string, string> = {
  unpaid: "Не оплачен",
  partial: "Частично оплачен",
  settled: "Оплачен",
};

export const PAYMENT_STATUS_TONE: Record<string, BadgeTone> = {
  unpaid: "destructive",
  partial: "warning",
  settled: "success",
};

// Тон этапа оплаты; подпись приходит с бэка (status_label из labels.py).
export const PAYMENT_STAGE_TONE: Record<string, BadgeTone> = {
  requested: "muted",
  received: "warning",
  confirmed: "success",
  rejected: "destructive",
};

// Тон состояний счёта платёжного провайдера: журнал транзакций показывает
// их тем же столбцом, что и этапы кассы.
const PROVIDER_STAGE_TONE: Record<string, BadgeTone> = {
  awaiting_customer: "warning",
  cancellation_pending: "warning",
  payment_error: "destructive",
  refund_pending: "warning",
  partially_refunded: "primary",
  refunded: "muted",
};

/** Подпись и тон состояния оплаты — кассового или провайдерского; подпись с бэка. */
export function paymentStage(payment: Pick<Payment, "status" | "effective_status" | "effective_status_label">): {
  label: string;
  tone: BadgeTone;
} {
  const status = payment.effective_status ?? payment.status;
  return {
    label: payment.effective_status_label,
    tone: PAYMENT_STAGE_TONE[status] ?? PROVIDER_STAGE_TONE[status] ?? "muted",
  };
}
