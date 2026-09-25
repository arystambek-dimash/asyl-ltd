import { describe, expect, it } from "vitest";
import type { ClientDebt } from "@/lib/types";
import { formatCompactCurrency } from "@/lib/utils";
import { debtTotals, formatCompactTotals, queueTotals } from "./totals";

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

describe("formatCompactTotals", () => {
  it("joins currencies with a plus instead of adding them", () => {
    expect(formatCompactTotals({ currency: "KZT", total: 1_200_000, other: [["USD", 500]] })).toBe(
      `${formatCompactCurrency(1_200_000, "KZT")} + ${formatCompactCurrency(500, "USD")}`,
    );
    expect(formatCompactTotals({ currency: "KZT", total: 0, other: [] })).toBe(formatCompactCurrency(0, "KZT"));
  });
});
