import type { Order } from "@/lib/types";

export function orderedBagCount(order: Pick<Order, "items">): number {
  return order.items.reduce((total, item) => total + Number(item.quantity), 0);
}
