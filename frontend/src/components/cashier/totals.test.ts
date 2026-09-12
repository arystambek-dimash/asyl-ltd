import { describe, expect, it } from "vitest";
import type { ClientDebt } from "@/lib/types";
import { debtTotals, incomeTotals, queueTotals } from "./totals";

const debtRow: ClientDebt = {
  client_id: 1,
  client_name: "Клиент",
  client_phone: "",
  debt_total: "100",
  debt_currency: "KZT",
  debt_by_currency: { KZT: "100" },
  orders_count: 1,
  unpaid_count: 1,
  partial_count: 0,
  stores_count: 0,
  overdue_count: 0,
};

describe("queueTotals", () => {
  it("sums grouped rows and counts payments, not groups", () => {
    const totals = queueTotals([
      { currency: "KZT", method: "cash", amount: "100", count: 2 },
      { currency: "KZT", method: "kaspi", amount: "50", count: 1 },
      { currency: "USD", method: "cash", amount: "5", count: 1 },
    ]);
    expect(totals).toMatchObject({ currency: "KZT", total: 150, cash: 100, count: 4, other: [["USD", 5]] });
  });
  it("treats rows without a count as single payments", () => {
    expect(queueTotals([{ currency: "KZT", method: "cash", amount: "100" }]).count).toBe(1);
  });
});

describe("debtTotals", () => {
  it("never adds currencies together", () => {
    const totals = debtTotals([
      debtRow,
      { ...debtRow, client_id: 2, debt_by_currency: { USD: "5" }, overdue_count: 1 },
    ]);
    expect(totals).toMatchObject({ currency: "KZT", total: 100, other: [["USD", 5]], clients: 2, overdue: 1 });
  });
});

describe("incomeTotals", () => {
  it("reads the primary currency and keeps the rest separate", () => {
    const totals = incomeTotals({
      from: null,
      to: null,
      income: {
        total: "90",
        cash: "40",
        cashless: "50",
        gross: "100",
        refunded: "10",
        payments: 3,
        refunds: 1,
        currency: "KZT",
        by_currency: { KZT: "90", USD: "5" },
        cash_by_currency: { KZT: "40" },
        cashless_by_currency: { KZT: "50" },
        gross_by_currency: { KZT: "100", USD: "5" },
        refunded_by_currency: { KZT: "10" },
      },
    });
    expect(totals).toMatchObject({
      currency: "KZT",
      total: 90,
      cash: 40,
      cashless: 50,
      gross: 100,
      refunded: 10,
      payments: 3,
      otherCurrencies: [["USD", 5]],
    });
    expect(totals.grossFor("USD")).toBe(5);
  });
  it("is empty without data", () => {
    expect(incomeTotals(null)).toMatchObject({ currency: "KZT", total: 0, payments: 0, otherCurrencies: [] });
  });
});
