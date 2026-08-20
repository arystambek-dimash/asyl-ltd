import { describe, expect, it } from "vitest";
import { reportChartCurrencies, reportChartSeries, shipmentSettlement } from "./report-analytics";
import type { ReportSummary } from "./types";

const shipped = (over: Partial<ReportSummary["shipped"]> = {}): ReportSummary["shipped"] => ({
  revenue: "1000",
  orders: 5,
  bags: 100,
  paid_amount: "100",
  debt_amount: "900",
  awaiting_amount: "0",
  currency: "KZT",
  revenue_by_currency: { KZT: "1000" },
  paid_amount_by_currency: { KZT: "100" },
  debt_amount_by_currency: { KZT: "900" },
  awaiting_amount_by_currency: { KZT: "0" },
  ...over,
});

const day = (over: Partial<ReportSummary["days"][number]> = {}): ReportSummary["days"][number] => ({
  date: "2026-07-01",
  orders: 1,
  bags: 10,
  revenue: "100",
  paid_amount: "100",
  debt_amount: "0",
  awaiting_amount: "0",
  cash: "40",
  cashless: "10",
  gross_received: "50",
  refunded: "0",
  received: "50",
  payments: 1,
  refunds: 0,
  revenue_by_currency: { KZT: "100" },
  paid_amount_by_currency: { KZT: "100" },
  debt_amount_by_currency: {},
  awaiting_amount_by_currency: {},
  cash_by_currency: { KZT: "40" },
  cashless_by_currency: { KZT: "10" },
  gross_received_by_currency: { KZT: "50" },
  refunded_by_currency: {},
  received_by_currency: { KZT: "50" },
  ...over,
});

describe("shipmentSettlement", () => {
  it("берёт фактическое погашение и текущий долг из независимых полей", () => {
    const split = shipmentSettlement(shipped());
    expect(split.currency).toBe("KZT");
    expect(split.revenue).toBe(1000);
    expect(split.debt).toBe(900);
    expect(split.paidToDate).toBe(100);
    expect(split.awaiting).toBe(0);
    expect(split.debtSharePct).toBe(90);
  });

  it("другие валюты не смешивает с основной, а раскладывает отдельно", () => {
    const split = shipmentSettlement(
      shipped({
        revenue_by_currency: { KZT: "1000", USD: "500" },
        paid_amount_by_currency: { KZT: "100", USD: "250" },
        debt_amount_by_currency: { KZT: "900", USD: "200" },
        awaiting_amount_by_currency: { KZT: "0", USD: "50" },
      }),
    );
    expect(split.revenue).toBe(1000);
    expect(split.others).toEqual([{ currency: "USD", revenue: 500, debt: 200, paidToDate: 250, awaiting: 50 }]);
  });

  it("без отгрузок доля долга неопределена, а не 0%", () => {
    const split = shipmentSettlement(
      shipped({
        revenue: "0",
        paid_amount: "0",
        debt_amount: "0",
        awaiting_amount: "0",
        revenue_by_currency: {},
        paid_amount_by_currency: {},
        debt_amount_by_currency: {},
        awaiting_amount_by_currency: {},
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

  it("не выдаёт неоплаченный instant-заказ за оплаченный", () => {
    const split = shipmentSettlement(
      shipped({
        paid_amount: "0",
        debt_amount: "0",
        awaiting_amount: "1000",
        paid_amount_by_currency: { KZT: "0" },
        debt_amount_by_currency: { KZT: "0" },
        awaiting_amount_by_currency: { KZT: "1000" },
      }),
    );
    expect(split).toMatchObject({ paidToDate: 0, debt: 0, awaiting: 1000 });
  });
});

describe("reportChartSeries", () => {
  it("разворачивает дни в хронологию и берёт суммы только выбранной валюты", () => {
    const series = reportChartSeries(
      [
        day({ date: "2026-07-02", revenue_by_currency: { KZT: "200" }, received_by_currency: { KZT: "80" } }),
        day({
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

  it("legacy flat поля относит только к их валюте", () => {
    const legacy = day({ revenue: "100", received: "50", revenue_by_currency: {}, received_by_currency: {} });
    expect(reportChartSeries([legacy], "USD", "KZT", "USD")[0]).toMatchObject({ revenue: 0, received: 50 });
    expect(reportChartSeries([legacy], "KZT", "KZT", "USD")[0]).toMatchObject({ revenue: 100, received: 0 });
  });

  it("предлагает переключатель для несовпадающих валют отгрузки и кассы", () => {
    const data = {
      shipped: shipped({ revenue_by_currency: { KZT: "1000" } }),
      income: { currency: "USD", total: "50" },
      days: [day({ revenue_by_currency: { KZT: "100" }, received_by_currency: { USD: "50" } })],
    } as ReportSummary;
    expect(reportChartCurrencies(data)).toEqual(["KZT", "USD"]);
  });
});
