import { describe, expect, it } from "vitest";
import { primaryMoneyCurrency } from "./currency-map";
import { currencySymbol, formatMoney, sumDebtByCurrency, sumMoneyByCurrency } from "./utils";

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
    expect(sumDebtByCurrency([{ debt_total: "не число", debt_currency: "KZT" }])).toEqual({ KZT: 0 });
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

describe("primaryMoneyCurrency", () => {
  it("выбирает бизнес-валюту без сравнения номиналов", () => {
    expect(primaryMoneyCurrency({ KZT: 1000, USD: 5 })).toBe("KZT");
    expect(primaryMoneyCurrency({ KZT: 100, USD: 5000 })).toBe("KZT");
    expect(primaryMoneyCurrency({ KZT: 100, USD: 5000 }, "USD")).toBe("USD");
  });

  it("игнорирует нулевые валюты", () => {
    expect(primaryMoneyCurrency({ USD: 0, KZT: 10 })).toBe("KZT");
  });

  it("по умолчанию — тенге", () => {
    expect(primaryMoneyCurrency({})).toBe("KZT");
    expect(primaryMoneyCurrency({ USD: 0 })).toBe("KZT");
  });
});
