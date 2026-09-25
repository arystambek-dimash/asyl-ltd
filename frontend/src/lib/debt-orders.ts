import type { Client, Order, Store } from "@/lib/types";
import { formatPaymentSchedule } from "@/lib/payment-schedule";

/** Магазин клиента с расписанием оплат: вне окна оплата по его заказам закрыта. */
export type DebtStore = Pick<Store, "id" | "name" | "payment_schedule_type" | "payment_days"> & {
  window_open: boolean;
};

/** Ответ `/clients/{id}/debt-detail/`. */
export interface ClientDebtDetail {
  client: Client;
  /** Суммы в основной валюте клиента — её же показывают плитки сверху. */
  debt_total: string;
  debt_currency: "KZT" | "USD";
  debt_by_currency: Record<string, string>;
  /** Без reports.view сервер отдаёт ноль и пустую разбивку. */
  overdue_total: string;
  overdue_by_currency: Record<string, string>;
  stores: DebtStore[];
  orders: Order[];
}

export function remainingOf(order: Order): number {
  return Number(order.remaining_amount);
}

/** Уже зарезервировано незавершёнными оплатами (ждут клиента или кассу). */
export function pendingSum(order: Order): number {
  return (order.pending_payments ?? []).reduce((s, p) => s + Number(p.amount), 0);
}

export function moneyCents(value: number | string): number {
  const amount = Number(value);
  return Number.isFinite(amount) ? Math.round(amount * 100) : 0;
}

/** Сколько ещё можно принять по заказу, в тиынах: остаток минус резервы. */
export function availableCents(order: Order): number {
  return Math.max(0, moneyCents(remainingOf(order)) - moneyCents(pendingSum(order)));
}

/** Магазин, чьё закрытое окно оплаты блокирует заказ; null — оплата открыта.
 * Окно — график погашения долга: предоплату до отгрузки магазин вносит в любой день.
 * Без графика сервер отдаёт окно открытым. */
export function blockingStore(order: Order, stores: readonly DebtStore[]): DebtStore | null {
  if (order.store == null || order.status !== "shipped") return null;
  const store = stores.find((row) => row.id === order.store);
  return store && !store.window_open ? store : null;
}

/** Почему оплата по заказам магазина закрыта: один текст для кассы, POS и страниц долга. */
export function storeBlockReason(store: Pick<DebtStore, "name" | "payment_schedule_type" | "payment_days">): string {
  return `Магазин «${store.name}» платит по расписанию: ${formatPaymentSchedule(store, "payment")}`;
}
