import { describe, expect, it } from "vitest";
import { paidSplit, reportChartSeries } from "./report-analytics";
import type { ReportSummary } from "./types";

const shipped = (over: Partial<ReportSummary["shipped"]> = {}): ReportSummary["shipped"] => ({
  revenue: "1000",
  orders: 5,
  bags: 100,
  debt_amount: "900",
  currency: "KZT",
  revenue_by_currency: { KZT: "1000" },
  debt_amount_by_currency: { KZT: "900" },
  ...over,
});

const day = (over: Partial<ReportSummary["days"][number]> = {}): ReportSummary["days"][number] => ({
  date: "2026-07-01",
  orders: 1,
  bags: 10,
  revenue: "100",
  debt_amount: "0",
  cash: "40",
  cashless: "10",
  received: "50",
  payments: 1,
  revenue_by_currency: { KZT: "100" },
  debt_amount_by_currency: {},
  cash_by_currency: { KZT: "40" },
  cashless_by_currency: { KZT: "10" },
  received_by_currency: { KZT: "50" },
  ...over,
});

describe("paidSplit", () => {
  it("делит отгруженное на «в долг» и «оплачено сразу» с долей долга", () => {
    const split = paidSplit(shipped());
    expect(split.currency).toBe("KZT");
    expect(split.revenue).toBe(1000);
    expect(split.debt).toBe(900);
    expect(split.paidNow).toBe(100);
    expect(split.debtSharePct).toBe(90);
  });

  it("другие валюты не смешивает с основной, а раскладывает отдельно", () => {
    const split = paidSplit(
      shipped({
        revenue_by_currency: { KZT: "1000", USD: "500" },
        debt_amount_by_currency: { KZT: "900", USD: "200" },
      }),
    );
    expect(split.revenue).toBe(1000);
    expect(split.others).toEqual([{ currency: "USD", revenue: 500, debt: 200, paidNow: 300 }]);
  });

  it("без отгрузок доля долга неопределена, а не 0%", () => {
    const split = paidSplit(
      shipped({ revenue: "0", debt_amount: "0", revenue_by_currency: {}, debt_amount_by_currency: {} }),
    );
    expect(split.revenue).toBe(0);
    expect(split.debtSharePct).toBeNull();
  });

  it("кривые данные (долг больше отгрузки) не дают отрицательного «сразу» и >100%", () => {
    const split = paidSplit(shipped({ revenue_by_currency: { KZT: "100" }, debt_amount_by_currency: { KZT: "150" } }));
    expect(split.paidNow).toBe(0);
    expect(split.debtSharePct).toBe(100);
  });

  it("округляет долю до целых процентов", () => {
    const split = paidSplit(shipped({ revenue_by_currency: { KZT: "300" }, debt_amount_by_currency: { KZT: "100" } }));
    expect(split.debtSharePct).toBe(33);
  });
});

describe("reportChartSeries", () => {
  it("разворачивает дни в хронологию и берёт суммы только выбранной валюты", () => {
    const series = reportChartSeries(
      [
        day({ date: "2026-07-02", revenue_by_currency: { KZT: "200" }, received_by_currency: { KZT: "80" } }),
        day({ date: "2026-07-01", revenue_by_currency: { KZT: "100", USD: "999" }, received_by_currency: {} }),
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
});
