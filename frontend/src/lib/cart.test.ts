import { describe, expect, it } from "vitest";
import type { PortalProduct } from "@/lib/types";
import { MAX_CART_QUANTITY, clampQuantity, priceCart, withQuantity } from "./cart";

const product = (id: number, price: string | null): PortalProduct => ({
  id,
  label: `Товар ${id}`,
  weight_kg: "25.00",
  price,
  currency: "KZT",
});

describe("количество в корзине", () => {
  it("не бывает отрицательным, дробным или больше предела заказа", () => {
    expect(clampQuantity(-3)).toBe(0);
    expect(clampQuantity(2.7)).toBe(2);
    expect(clampQuantity(Number.NaN)).toBe(0);
    expect(clampQuantity(MAX_CART_QUANTITY + 5)).toBe(MAX_CART_QUANTITY);
  });

  it("ноль убирает товар, новый встаёт в конец, существующий меняется на месте", () => {
    let lines = withQuantity([], 1, 2);
    lines = withQuantity(lines, 5, 1);
    lines = withQuantity(lines, 1, 3);
    expect(lines).toEqual([
      { product: 1, quantity: 3 },
      { product: 5, quantity: 1 },
    ]);
    expect(withQuantity(lines, 1, 0)).toEqual([{ product: 5, quantity: 1 }]);
    expect(withQuantity(lines, 1, -1)).toEqual([{ product: 5, quantity: 1 }]);
  });
});

describe("priceCart", () => {
  it("считает позиции и итог по живому прайсу", () => {
    const priced = priceCart(
      [
        { product: 1, quantity: 2 },
        { product: 5, quantity: 1 },
      ],
      [product(1, "25000.00"), product(5, "50000.00")],
    );

    expect(priced.rows.map((row) => row.total)).toEqual([50000, 50000]);
    expect(priced.quantity).toBe(3);
    expect(priced.total).toBe(100000);
    expect(priced.orderItems).toEqual([
      { product: 1, quantity: 2 },
      { product: 5, quantity: 1 },
    ]);
  });

  it("не набегает копейками на долларовых ценах", () => {
    expect(priceCart([{ product: 1, quantity: 3 }], [product(1, "0.10")]).total).toBe(0.3);
  });

  it("товар без цены заказывается, недоступный — нет", () => {
    const priced = priceCart(
      [
        { product: 1, quantity: 4 },
        { product: 9, quantity: 7 },
      ],
      [product(1, null)],
    );

    expect(priced.hasUnpriced).toBe(true);
    expect(priced.hasUnavailable).toBe(true);
    expect(priced.quantity).toBe(4);
    expect(priced.total).toBe(0);
    expect(priced.orderItems).toEqual([{ product: 1, quantity: 4 }]);
  });

  it("пока каталог грузится, товары не считаются недоступными", () => {
    expect(priceCart([{ product: 1, quantity: 1 }], null).hasUnavailable).toBe(false);
  });
});
