import type { AxiosError } from "axios";
import type { Order } from "@/lib/types";
import { formatCurrency } from "@/lib/utils";

type OrderLine = Order["items"][number];

/** Сумма, которую нельзя посчитать: «0 ₸» выглядел бы как бесплатный заказ. */
export const UNPRICED_TOTAL = "Не рассчитана";

export function orderedBagCount(order: Pick<Order, "items">): number {
  return order.items.reduce((total, item) => total + Number(item.quantity), 0);
}

/** У позиции нет договорной цены — сумма заказа ещё не рассчитана. */
export function hasUnpricedItems(items: Pick<OrderLine, "unit_price">[]): boolean {
  return items.some((item) => item.unit_price == null);
}

export interface RequestEstimate {
  bags: number;
  /** null — не у каждой позиции есть цена. */
  amount: number | null;
}

type EstimateLine = {
  id?: number;
  quantity: number | string;
  unit_price?: string | null;
  client_price?: string | null;
};

/**
 * Мешки и сумма заявки до подтверждения. Цена позиции — правка в окне
 * подтверждения (`overrides.prices`, ключ — id позиции), иначе зафиксированная
 * цена, иначе личный прайс клиента. Количество — правка или запрошенное.
 */
export function requestEstimate(
  items: EstimateLine[],
  overrides: { prices?: Record<string, string>; quantities?: Record<string, string | number> } = {},
): RequestEstimate {
  let bags = 0;
  let amount: number | null = 0;
  for (const item of items) {
    const key = String(item.id);
    const quantity = Number(overrides.quantities?.[key] ?? item.quantity) || 0;
    const raw =
      overrides.prices && key in overrides.prices ? overrides.prices[key] : (item.unit_price ?? item.client_price);
    const price = Number(raw);
    bags += quantity;
    amount = amount === null || !raw || !(price > 0) ? null : amount + price * quantity;
  }
  return { bags, amount };
}

/** Сумма оценки для людей; без цены у какой-то позиции — «Не рассчитана». */
export function formatEstimate(amount: number | null, currency: string, { approx = false } = {}): string {
  if (amount === null) return UNPRICED_TOTAL;
  return `${approx ? "≈ " : ""}${formatCurrency(amount, currency)}`;
}

/** 400 подтверждения `invalid_item`: состав заявки изменился, пока было открыто окно. */
export function isChangedRequestError(error: unknown): boolean {
  return (error as AxiosError<{ code?: unknown }> | undefined)?.response?.data?.code === "invalid_item";
}
