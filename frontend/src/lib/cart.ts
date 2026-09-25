import type { PortalProduct } from "@/lib/types";

export type CartCurrency = PortalProduct["currency"];

export interface CartLine {
  product: number;
  quantity: number;
}

// Те же пределы, что у POST /portal/orders/ (MAX_PORTAL_ITEM_QUANTITY, MAX_PORTAL_ORDER_ITEMS).
export const MAX_CART_QUANTITY = 1_000_000;
const MAX_CART_LINES = 100;

/** Количество мешков: целое, не меньше 0 и не больше предела заказа. */
export function clampQuantity(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.min(MAX_CART_QUANTITY, Math.max(0, Math.floor(value)));
}

/** Новый список строк: 0 убирает товар, новый товар встаёт в конец. */
export function withQuantity(lines: CartLine[], product: number, quantity: number): CartLine[] {
  const next = clampQuantity(quantity);
  const index = lines.findIndex((line) => line.product === product);
  if (next === 0) return index === -1 ? lines : lines.filter((line) => line.product !== product);
  if (index === -1) return lines.length >= MAX_CART_LINES ? lines : [...lines, { product, quantity: next }];
  return lines.map((line, i) => (i === index ? { ...line, quantity: next } : line));
}

export interface PricedCartRow {
  line: CartLine;
  /** null — товара больше нет в каталоге клиента (закончился или снят с продажи). */
  product: PortalProduct | null;
  /** Сумма позиции; null — цена ещё не закреплена в прайсе клиента. */
  total: number | null;
}

export interface PricedCart {
  rows: PricedCartRow[];
  /** Мешков в позициях, которые можно заказать. */
  quantity: number;
  total: number;
  hasUnpriced: boolean;
  hasUnavailable: boolean;
  /** Позиции для POST /portal/orders/ — только доступные товары. */
  orderItems: CartLine[];
}

/** Считает корзину по живому каталогу: цены не хранятся в браузере и не устаревают. */
export function priceCart(lines: CartLine[], products: PortalProduct[] | null | undefined): PricedCart {
  const byId = new Map((products ?? []).map((product) => [product.id, product]));
  let totalCents = 0;
  let quantity = 0;
  let hasUnpriced = false;
  const orderItems: CartLine[] = [];
  const rows = lines.map((line) => {
    const product = byId.get(line.product) ?? null;
    if (!product) return { line, product, total: null };
    quantity += line.quantity;
    orderItems.push(line);
    if (product.price == null) {
      hasUnpriced = true;
      return { line, product, total: null };
    }
    // Считаем в тиынах/центах: сумма в float набегала бы копейками.
    const cents = Math.round(Number(product.price) * 100) * line.quantity;
    totalCents += cents;
    return { line, product, total: cents / 100 };
  });
  return {
    rows,
    quantity,
    total: totalCents / 100,
    hasUnpriced,
    hasUnavailable: products != null && rows.some((row) => !row.product),
    orderItems,
  };
}

export const cartQuantity = (lines: CartLine[]) => lines.reduce((sum, line) => sum + line.quantity, 0);
