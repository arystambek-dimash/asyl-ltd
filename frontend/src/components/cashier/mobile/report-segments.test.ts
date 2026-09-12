import { describe, expect, it } from "vitest";
import type { IncomeSummary } from "@/components/cashier/totals";
import { reportSegments } from "./report-segments";

const income: IncomeSummary["income"] = {
  total: "400",
  cash: "300",
  cashless: "100",
  gross: "400",
  refunded: "0",
  payments: 3,
  refunds: 0,
  currency: "KZT",
  by_currency: { KZT: "400" },
  cash_by_currency: { KZT: "300" },
  cashless_by_currency: { KZT: "100" },
  gross_by_currency: { KZT: "400" },
  refunded_by_currency: {},
};
const summary: IncomeSummary = {
  from: null,
  to: null,
  income: {
    ...income,
    by_method_by_currency: { KZT: { kaspi: "100", cash: "300" } },
    payments_by_method: { cash: 2, kaspi: 1 },
  },
  departments: [
    {
      code: "main",
      name: "Мельница",
      color: "#111",
      orders: null,
      sales_by_currency: null,
      received_by_currency: { KZT: "400" },
      refunded_by_currency: {},
      net_by_currency: { KZT: "400" },
      payments: 3,
    },
  ],
};

describe("reportSegments", () => {
  it("splits by department with payment counts", () => {
    const result = reportSegments(summary, "departments", "KZT");
    expect(result.segments).toEqual([{ key: "main", label: "Мельница", value: 400, color: "#111" }]);
    expect(result.counts).toEqual({ main: 3 });
    expect(result.fallback).toBe(false);
  });
  it("splits by method, largest first, with method labels", () => {
    const result = reportSegments(summary, "methods", "KZT");
    expect(result.segments.map((s) => [s.key, s.label, s.value])).toEqual([
      ["cash", "Наличные", 300],
      ["kaspi", "QR", 100],
    ]);
    expect(result.counts).toEqual({ cash: 2, kaspi: 1 });
  });
  it("falls back to cash/cashless for an older backend", () => {
    const result = reportSegments({ ...summary, income }, "methods", "KZT");
    expect(result.fallback).toBe(true);
    expect(result.segments.map((s) => [s.label, s.value])).toEqual([
      ["Наличные", 300],
      ["Безналичные", 100],
    ]);
  });
  it("is empty without data", () => {
    expect(reportSegments(null, "departments", "KZT")).toEqual({ segments: [], counts: {}, fallback: false });
  });
});
