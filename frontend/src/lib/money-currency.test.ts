import { describe, expect, it } from "vitest";
import { primaryDebtCurrency, sumDebtByCurrency } from "./utils";

describe("sumDebtByCurrency", () => {
  it("не складывает тенге с долларами", () => {
    // Главное правило системы: 1000 ₸ и 5 $ не дают «1005».
    const totals = sumDebtByCurrency([
      { debt_total: "1000", debt_currency: "KZT" },
      { debt_total: "5", debt_currency: "USD" },
    ]);
    expect(totals).toEqual({ KZT: 1000, USD: 5 });
  });

  it("берёт полную разбивку, когда клиент должен в двух валютах", () => {
    const totals = sumDebtByCurrency([
      { debt_total: "1000", debt_currency: "KZT", debt_by_currency: { KZT: "1000", USD: "20" } },
      { debt_total: "500", debt_currency: "KZT", debt_by_currency: { KZT: "500" } },
    ]);
    expect(totals).toEqual({ KZT: 1500, USD: 20 });
  });

  it("падает на валюту клиента, если разбивки нет", () => {
    expect(sumDebtByCurrency([{ debt_total: "300", currency: "USD" }])).toEqual({ USD: 300 });
  });

  it("считает тенге валютой по умолчанию", () => {
    expect(sumDebtByCurrency([{ debt_total: "300" }])).toEqual({ KZT: 300 });
  });

  it("не падает на пустом списке и мусорных суммах", () => {
    expect(sumDebtByCurrency([])).toEqual({});
    expect(sumDebtByCurrency([{ debt_total: null, debt_currency: "KZT" }])).toEqual({ KZT: 0 });
  });
});

describe("primaryDebtCurrency", () => {
  it("выбирает валюту с наибольшим долгом", () => {
    expect(primaryDebtCurrency({ KZT: 1000, USD: 5 })).toBe("KZT");
    expect(primaryDebtCurrency({ KZT: 100, USD: 5000 })).toBe("USD");
  });

  it("игнорирует нулевые валюты", () => {
    expect(primaryDebtCurrency({ USD: 0, KZT: 10 })).toBe("KZT");
  });

  it("по умолчанию — тенге", () => {
    expect(primaryDebtCurrency({})).toBe("KZT");
    expect(primaryDebtCurrency({ USD: 0 })).toBe("KZT");
  });
});
