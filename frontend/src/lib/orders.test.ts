import { describe, expect, it } from "vitest";
import {
  UNPRICED_TOTAL,
  clientLabel,
  formatEstimate,
  hasUnpricedItems,
  orderItemLabel,
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

  it("counts bonus bags but no money for them", () => {
    const lines = [
      { id: 1, quantity: 300, unit_price: "100.00" },
      { id: 2, quantity: 3, unit_price: "0.00", is_bonus: true },
    ];
    expect(requestEstimate(lines)).toEqual({ bags: 303, amount: 30000 });
    // Окно подтверждения не шлёт цену бонуса — без неё сумма всё равно считается.
    expect(requestEstimate(lines, { prices: { "1": "120" } })).toEqual({ bags: 303, amount: 36000 });
    expect(requestEstimate([{ quantity: "2", unit_price: "", is_bonus: true }])).toEqual({ bags: 2, amount: 0 });
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

it("flags an order total with unpriced paid lines", () => {
  expect(hasUnpricedItems([{ unit_price: "1" }, { unit_price: null }])).toBe(true);
  expect(hasUnpricedItems([{ unit_price: "1" }])).toBe(false);
  expect(hasUnpricedItems([{ unit_price: "1" }, { unit_price: null, is_bonus: true }])).toBe(false);
});

it("marks a bonus line in its label", () => {
  expect(orderItemLabel({ product: 2, product_label: "Мука 50 кг" })).toBe("Мука 50 кг");
  expect(orderItemLabel({ product: 2, product_label: "Мука 50 кг", is_bonus: true })).toBe("Мука 50 кг (бонус)");
  expect(orderItemLabel({ product: 2 })).toBe("Товар #2");
});

describe("clientLabel", () => {
  it("names the client and falls back to its number", () => {
    expect(clientLabel({ client: 5, client_name: "ТОО Цех" })).toBe("ТОО Цех");
    expect(clientLabel({ client: 5, client_name: "" })).toBe("Клиент #5");
  });
});

describe("orderItemsSummary", () => {
  const line = (product_label: string | undefined, quantity: number, is_bonus = false) =>
    ({ product: 7, product_label, quantity, is_bonus }) as Parameters<typeof orderItemsSummary>[0]["items"][number];

  it("shows the first two lines and counts the rest", () => {
    expect(orderItemsSummary({ items: [line("Мука", 10), line(undefined, 5)] })).toBe("Мука × 10, Товар #7 × 5");
    expect(orderItemsSummary({ items: [line("Мука", 300), line("Мука", 3, true)] })).toBe(
      "Мука × 300, Мука (бонус) × 3",
    );
    expect(orderItemsSummary({ items: [line("Мука", 10), line("Отруби", 5), line("Сечка", 1), line("Жмых", 2)] })).toBe(
      "Мука × 10, Отруби × 5 и ещё 2",
    );
    expect(orderItemsSummary({ items: [] })).toBe("");
  });
});
