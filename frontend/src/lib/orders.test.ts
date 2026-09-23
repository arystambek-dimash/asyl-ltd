import { describe, expect, it } from "vitest";
import { UNPRICED_TOTAL, formatEstimate, hasUnpricedItems, requestEstimate } from "./orders";
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
