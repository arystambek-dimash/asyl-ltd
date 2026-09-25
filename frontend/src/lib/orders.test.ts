import { describe, expect, it } from "vitest";
import {
  UNPRICED_TOTAL,
  clientLabel,
  formatEstimate,
  hasUnpricedItems,
  orderItemsSummary,
  requestEstimate,
} from "./orders";
import { formatCurrency } from "./utils";

describe("requestEstimate", () => {
  const items = [
    { id: 1, quantity: 10, unit_price: "100.00" },
    { id: 2, quantity: 4, unit_price: null, client_price: "50" },
  ];

  it("counts bags and prices a request by fixed price, then by the client price", () => {
    expect(requestEstimate(items)).toEqual({ bags: 14, amount: 1200 });
  });

  it("applies the prices and quantities edited in the confirmation window", () => {
    expect(requestEstimate(items, { prices: { "1": "120" }, quantities: { "1": 8 } })).toEqual({
      bags: 12,
      amount: 1160,
    });
  });

  it("is not priced when any line has no price", () => {
    expect(requestEstimate([{ id: 1, quantity: 2, unit_price: null, client_price: null }])).toEqual({
      bags: 2,
      amount: null,
    });
    // Стёртая в окне цена — «нет цены», а не возврат к прайсу клиента.
    expect(requestEstimate(items, { prices: { "2": "" } }).amount).toBeNull();
    expect(requestEstimate(items, { prices: { "2": "0" } }).amount).toBeNull();
  });

  it("prices form rows without ids and an empty request as zero", () => {
    expect(requestEstimate([{ quantity: "3", unit_price: "10" }])).toEqual({ bags: 3, amount: 30 });
    expect(requestEstimate([])).toEqual({ bags: 0, amount: 0 });
  });

  it("sums money in tiyns so the total does not drift by fractions", () => {
    // Во float 0.1 * 3 + 0.2 * 3 = 0.9000000000000001.
    const lines = [
      { quantity: 3, unit_price: "0.10" },
      { quantity: 3, unit_price: "0.20" },
    ];
    expect(requestEstimate(lines)).toEqual({ bags: 6, amount: 0.9 });
  });
});

it("formats an estimate or says it is not calculated", () => {
  expect(formatEstimate(null, "KZT")).toBe(UNPRICED_TOTAL);
  expect(formatEstimate(1200, "KZT")).toBe(formatCurrency(1200, "KZT"));
  expect(formatEstimate(1200, "USD", { approx: true })).toBe(`≈ ${formatCurrency(1200, "USD")}`);
});

it("flags an order total with unpriced lines", () => {
  expect(hasUnpricedItems([{ unit_price: "1" }, { unit_price: null }])).toBe(true);
  expect(hasUnpricedItems([{ unit_price: "1" }])).toBe(false);
});

describe("clientLabel", () => {
  it("names the client and falls back to its number", () => {
    expect(clientLabel({ client: 5, client_name: "ТОО Цех" })).toBe("ТОО Цех");
    expect(clientLabel({ client: 5, client_name: "" })).toBe("Клиент #5");
  });
});

describe("orderItemsSummary", () => {
  const line = (product_label: string | undefined, quantity: number) =>
    ({ product_label, quantity }) as unknown as Parameters<typeof orderItemsSummary>[0]["items"][number];

  it("shows the first two lines and counts the rest", () => {
    expect(orderItemsSummary({ items: [line("Мука", 10), line(undefined, 5)] })).toBe("Мука × 10, Товар × 5");
    expect(orderItemsSummary({ items: [line("Мука", 10), line("Отруби", 5), line("Сечка", 1), line("Жмых", 2)] })).toBe(
      "Мука × 10, Отруби × 5 и ещё 2",
    );
    expect(orderItemsSummary({ items: [] })).toBe("");
  });
});
