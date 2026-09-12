import type { Client, Order } from "@/lib/types";

/** Магазин клиента с расписанием оплат: вне окна оплата по его заказам закрыта. */
export interface DebtStore {
  id: number;
  name: string;
  payment_schedule_type: "none" | "monthly" | "weekly";
  payment_days: number[];
  window_open: boolean;
}

/** Ответ `/clients/{id}/debt-detail/`. */
export interface ClientDebtDetail {
  client: Client;
  /** Суммы в основной валюте клиента — её же показывают плитки сверху. */
  debt_total: string;
  debt_currency?: "KZT" | "USD";
  debt_by_currency?: Record<string, string>;
  lifetime_total?: string;
  lifetime_paid?: string;
  lifetime_by_currency?: Record<string, { total: string; paid: string }>;
  overdue_total?: string;
  overdue_by_currency?: Record<string, string>;
  orders_count: number;
  unpaid_count: number;
  partial_count: number;
  stores: DebtStore[];
  orders: Order[];
}

export function remainingOf(order: Order): number {
  return Number(order.remaining_amount ?? Number(order.total_amount) - Number(order.paid_total));
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

/** Магазин, чьё закрытое окно оплаты блокирует заказ; null — оплата открыта. */
export function blockingStore(order: Order, stores: readonly DebtStore[]): DebtStore | null {
  if (order.store == null) return null;
  const store = stores.find((row) => row.id === order.store);
  if (!store || store.payment_schedule_type === "none" || store.window_open) return null;
  return store;
}
