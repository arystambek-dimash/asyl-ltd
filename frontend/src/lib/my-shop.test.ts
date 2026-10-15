import { describe, expect, it } from "vitest";
import { repeatLines } from "@/lib/my-shop";
import type { MyShopOrder } from "@/lib/types";

const item = (product: number | null, quantity: number, is_bonus = false) => ({
  id: quantity,
  product,
  product_label: "Д1с",
  quantity,
  unit_price: is_bonus ? "0.00" : "5000.00",
  is_bonus,
});

describe("repeatLines", () => {
  it("keeps paid lines of products that can still be ordered", () => {
    const order = { items: [item(1, 30), item(1, 2, true), item(2, 5), item(null, 4)] } as MyShopOrder;

    expect(repeatLines(order, new Set([1]))).toEqual([{ product: 1, quantity: 30 }]);
  });
});
