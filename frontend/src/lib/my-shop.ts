import type { CartLine } from "@/lib/cart";
import type { MyShopOrder } from "@/lib/types";

/** «Повторить»: платные строки заказа с товарами, которые сейчас можно заказать по прайсу магазина. */
export function repeatLines(order: MyShopOrder, orderable: ReadonlySet<number>): CartLine[] {
  const quantities = new Map<number, number>();
  for (const item of order.items) {
    if (item.is_bonus || item.product == null || !orderable.has(item.product)) continue;
    quantities.set(item.product, (quantities.get(item.product) ?? 0) + item.quantity);
  }
  return [...quantities].map(([product, quantity]) => ({ product, quantity }));
}
