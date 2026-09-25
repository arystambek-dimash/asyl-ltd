import { describe, expect, it } from "vitest";
import { currencySymbol, formatMoney, sumDebtByCurrency, sumMoneyByCurrency } from "./utils";

describe("sumDebtByCurrency", () => {
  it("не складывает тенге с долларами", () => {
    // Главное правило системы: 1000 ₸ и 5 $ не дают «1005».
    const totals = sumDebtByCurrency([
      { debt_by_currency: { KZT: "1000", USD: "20" } },
      { debt_by_currency: { KZT: "500" } },
      { debt_by_currency: { USD: "5" } },
    ]);
    expect(totals).toEqual({ KZT: 1500, USD: 25 });
  });

  it("не падает на пустом списке и мусорных суммах", () => {
    expect(sumDebtByCurrency([])).toEqual({});
    expect(sumDebtByCurrency([{ debt_by_currency: { KZT: "не число" } }])).toEqual({ KZT: 0 });
  });
});

describe("sumMoneyByCurrency", () => {
  it("суммирует очередь отдельно по каждой валюте и игнорирует мусор", () => {
    const rows = [
      { amount: "1000", currency: "KZT" },
      { amount: "5", currency: "USD" },
      { amount: "oops", currency: "KZT" },
    ];
    expect(
      sumMoneyByCurrency(
        rows,
        (row) => row.amount,
        (row) => row.currency,
      ),
    ).toEqual({ KZT: 1000, USD: 5 });
  });
});

describe("money formatting", () => {
  it("не выводит NaN и не маскирует неизвестную валюту под тенге", () => {
    expect(formatMoney("не число")).toBe("0");
    expect(currencySymbol("EUR")).toBe("EUR");
  });
});
