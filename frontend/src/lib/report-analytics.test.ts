import { describe, expect, it } from "vitest";
import {
  incomeDetailRows,
  incomeTotals,
  reportChartCurrencies,
  reportChartSeries,
  shipmentSettlement,
} from "./report-analytics";
import type { ReportSummary } from "./types";
import { makeReportDay } from "@/test-utils/factories";

const shipped = (over: Partial<ReportSummary["shipped"]> = {}): ReportSummary["shipped"] => ({
  revenue: "1000",
  orders: 5,
  bags: 100,
  paid_amount: "100",
  debt_amount: "900",
  currency: "KZT",
  revenue_by_currency: { KZT: "1000" },
  paid_amount_by_currency: { KZT: "100" },
  debt_amount_by_currency: { KZT: "900" },
  ...over,
});

describe("shipmentSettlement", () => {
  it("берёт фактическое погашение и текущий долг из независимых полей", () => {
    const split = shipmentSettlement(shipped());
    expect(split.currency).toBe("KZT");
    expect(split.revenue).toBe(1000);
    expect(split.debt).toBe(900);
    expect(split.paidToDate).toBe(100);
    expect(split.debtSharePct).toBe(90);
  });

  it("другие валюты не смешивает с основной, а раскладывает отдельно", () => {
    const split = shipmentSettlement(
      shipped({
        revenue_by_currency: { KZT: "1000", USD: "500" },
        paid_amount_by_currency: { KZT: "100", USD: "250" },
        debt_amount_by_currency: { KZT: "900", USD: "200" },
      }),
    );
    expect(split.revenue).toBe(1000);
    expect(split.others).toEqual([{ currency: "USD", revenue: 500, debt: 200, paidToDate: 250 }]);
  });

  it("без отгрузок доля долга неопределена, а не 0%", () => {
    const split = shipmentSettlement(
      shipped({
        revenue: "0",
        paid_amount: "0",
        debt_amount: "0",
        revenue_by_currency: {},
        paid_amount_by_currency: {},
        debt_amount_by_currency: {},
      }),
    );
    expect(split.revenue).toBe(0);
    expect(split.debtSharePct).toBeNull();
  });

  it("кривые данные (долг больше отгрузки) не дают долю >100%", () => {
    const split = shipmentSettlement(
      shipped({ revenue_by_currency: { KZT: "100" }, debt_amount_by_currency: { KZT: "150" } }),
    );
    expect(split.debtSharePct).toBe(100);
  });

  it("округляет долю до целых процентов", () => {
    const split = shipmentSettlement(
      shipped({ revenue_by_currency: { KZT: "300" }, debt_amount_by_currency: { KZT: "100" } }),
    );
    expect(split.debtSharePct).toBe(33);
  });
});

describe("reportChartSeries", () => {
  it("разворачивает дни в хронологию и берёт суммы только выбранной валюты", () => {
    const series = reportChartSeries(
      [
        makeReportDay({ date: "2026-07-02", revenue_by_currency: { KZT: "200" }, received_by_currency: { KZT: "80" } }),
        makeReportDay({
          date: "2026-07-01",
          revenue_by_currency: { KZT: "100", USD: "999" },
          received_by_currency: { USD: "50" },
        }),
      ],
      "KZT",
    );
    expect(series.map((p) => p.date)).toEqual(["2026-07-01", "2026-07-02"]);
    expect(series[0]).toMatchObject({ label: "01.07", revenue: 100, received: 0 });
    expect(series[1]).toMatchObject({ label: "02.07", revenue: 200, received: 80 });
  });

  it("пустой список дней даёт пустую серию", () => {
    expect(reportChartSeries([], "KZT")).toEqual([]);
  });

  it("предлагает переключатель для несовпадающих валют отгрузки и кассы", () => {
    const data = {
      shipped: shipped({ revenue_by_currency: { KZT: "1000" } }),
      income: { currency: "USD", total: "50" },
      days: [makeReportDay({ revenue_by_currency: { KZT: "100" }, received_by_currency: { USD: "50" } })],
    } as ReportSummary;
    expect(reportChartCurrencies(data)).toEqual(["KZT", "USD"]);
  });
});

describe("incomeTotals", () => {
  it("reads the primary currency and keeps the rest separate", () => {
    const totals = incomeTotals({
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
        by_method_by_currency: { KZT: { cash: "40", kaspi: "50" } },
        payments_by_method: { cash: 2, kaspi: 1 },
        method_labels: { cash: "Наличные", kaspi: "QR" },
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

const income = (over: Partial<ReportSummary["income"]>): ReportSummary["income"] => ({
  total: "0",
  cash: "0",
  cashless: "0",
  gross: "0",
  refunded: "0",
  payments: 0,
  refunds: 0,
  currency: "KZT",
  by_currency: {},
  cash_by_currency: {},
  cashless_by_currency: {},
  gross_by_currency: {},
  refunded_by_currency: {},
  by_method_by_currency: {},
  payments_by_method: {},
  method_labels: {},
  ...over,
});

describe("incomeDetailRows", () => {
  it("lists other currencies and refunds per currency without adding them up", () => {
    const rows = incomeDetailRows(
      incomeTotals({
        income: income({
          by_currency: { KZT: "90", USD: "3" },
          gross_by_currency: { KZT: "100", USD: "5" },
          refunded_by_currency: { KZT: "10", USD: "2" },
        }),
      }),
    );
    expect(rows.map((row) => row.label)).toEqual([
      "Также чистыми",
      "Поступило до возвратов",
      "Возвращено",
      "Поступило до возвратов, USD",
      "Возвращено, USD",
    ]);
    expect(rows[0].value).toContain("$");
    expect(rows[1].value).toContain("₸");
  });

  it("is empty for a single currency without refunds", () => {
    expect(incomeDetailRows(incomeTotals({ income: income({ by_currency: { KZT: "90" } }) }))).toEqual([]);
  });
});
