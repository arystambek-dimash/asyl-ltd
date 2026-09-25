import { apiErrorCode } from "@/lib/api";
import { moneyCents } from "@/lib/debt-orders";
import type { Order } from "@/lib/types";
import { formatCurrency } from "@/lib/utils";

type OrderLine = Order["items"][number];

/** Сумма, которую нельзя посчитать: «0 ₸» выглядел бы как бесплатный заказ. */
export const UNPRICED_TOTAL = "Не рассчитана";

/** Подпись клиента заказа: у заказа без имени клиента — его номер. */
export function clientLabel(order: Pick<Order, "client" | "client_name">): string {
  return order.client_name || `Клиент #${order.client}`;
}

export function orderedBagCount(order: Pick<Order, "items">): number {
  return order.items.reduce((total, item) => total + Number(item.quantity), 0);
}

/** «Мука × 10, Отруби × 5 и ещё 2» — первые две позиции заказа одной строкой. */
export function orderItemsSummary(order: Pick<Order, "items">): string {
  const shown = order.items
    .slice(0, 2)
    .map((item) => `${item.product_label ?? "Товар"} × ${item.quantity}`)
    .join(", ");
  return order.items.length > 2 ? `${shown} и ещё ${order.items.length - 2}` : shown;
}

/** У позиции нет договорной цены — сумма заказа ещё не рассчитана. */
export function hasUnpricedItems(items: Pick<OrderLine, "unit_price">[]): boolean {
  return items.some((item) => item.unit_price == null);
}

interface RequestEstimate {
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
  // Считаем в тиынах, как priceCart: сумма во float набегала бы копейками.
  let cents: number | null = 0;
  for (const item of items) {
    const key = String(item.id);
    const quantity = Number(overrides.quantities?.[key] ?? item.quantity) || 0;
    const raw =
      overrides.prices && key in overrides.prices ? overrides.prices[key] : (item.unit_price ?? item.client_price);
    const price = Number(raw);
    bags += quantity;
    cents = cents === null || !raw || !(price > 0) ? null : cents + Math.round(moneyCents(price) * quantity);
  }
  return { bags, amount: cents === null ? null : cents / 100 };
}

/** Сумма оценки для людей; без цены у какой-то позиции — «Не рассчитана». */
export function formatEstimate(amount: number | null, currency: string, { approx = false } = {}): string {
  if (amount === null) return UNPRICED_TOTAL;
  return `${approx ? "≈ " : ""}${formatCurrency(amount, currency)}`;
}

/** 400 подтверждения `invalid_item`: состав заявки изменился, пока было открыто окно. */
export function isChangedRequestError(error: unknown): boolean {
  return apiErrorCode(error) === "invalid_item";
}
