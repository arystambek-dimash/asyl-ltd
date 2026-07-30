import { describe, expect, it } from "vitest";
import { amountForCurrency, finiteMoney, otherCurrencyAmounts, primaryMoneyCurrency } from "./currency-map";

describe("currency maps", () => {
  it("keeps currencies separate and does not fall back from a populated map", () => {
    const totals = { KZT: "1000.00", USD: "5.00" };
    expect(amountForCurrency(totals, "1005.00", "KZT")).toBe(1000);
    expect(amountForCurrency(totals, "1005.00", "EUR")).toBe(0);
    expect(otherCurrencyAmounts(totals, "KZT")).toEqual([["USD", 5]]);
  });

  it("supports legacy flat responses only when no map is available", () => {
    expect(amountForCurrency({}, "125.50", "KZT")).toBe(125.5);
  });

  it("normalizes malformed numeric input", () => {
    expect(finiteMoney("not-a-number")).toBe(0);
    expect(otherCurrencyAmounts({ KZT: "1", USD: "bad" }, "KZT")).toEqual([]);
  });

  it("selects a primary currency from string maps without mixing them", () => {
    expect(primaryMoneyCurrency({ KZT: "1000.00", USD: "5.00" })).toBe("KZT");
    expect(primaryMoneyCurrency({ KZT: "100.00", USD: "5000.00" })).toBe("KZT");
    expect(primaryMoneyCurrency({ KZT: "100.00", USD: "5000.00" }, "USD")).toBe("USD");
    expect(primaryMoneyCurrency({ USD: "bad" }, "USD")).toBe("USD");
  });
});
