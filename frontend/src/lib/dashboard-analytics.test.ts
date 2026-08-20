import { describe, expect, it } from "vitest";
import {
  adaptDashboardDebt,
  adaptOperationalShipments,
  adaptReportSummary,
  dashboardReportRange,
} from "@/lib/dashboard-analytics";
import type { ReportDay, ReportSummary } from "@/lib/types";

function reportDay(partial: Partial<ReportDay> & Pick<ReportDay, "date">): ReportDay {
  return {
    orders: 0,
    bags: 0,
    revenue: "0.00",
    paid_amount: "0.00",
    debt_amount: "0.00",
    awaiting_amount: "0.00",
    cash: "0.00",
    cashless: "0.00",
    gross_received: "0.00",
    refunded: "0.00",
    received: "0.00",
    payments: 0,
    refunds: 0,
    revenue_by_currency: {},
    paid_amount_by_currency: {},
    debt_amount_by_currency: {},
    awaiting_amount_by_currency: {},
    cash_by_currency: {},
    cashless_by_currency: {},
    gross_received_by_currency: {},
    refunded_by_currency: {},
    received_by_currency: {},
    ...partial,
  };
}

function reportSummary(days: ReportDay[]): ReportSummary {
  return {
    from: "2026-07-28",
    to: "2026-07-30",
    income: {
      total: "0.00",
      cash: "0.00",
      cashless: "0.00",
      gross: "0.00",
      refunded: "0.00",
      payments: 0,
      refunds: 0,
      currency: "KZT",
      by_currency: {},
      cash_by_currency: {},
      cashless_by_currency: {},
      gross_by_currency: {},
      refunded_by_currency: {},
    },
    shipped: {
      revenue: "0.00",
      orders: 0,
      bags: 0,
      paid_amount: "0.00",
      debt_amount: "0.00",
      awaiting_amount: "0.00",
      currency: "KZT",
      revenue_by_currency: {},
      paid_amount_by_currency: {},
      debt_amount_by_currency: {},
      awaiting_amount_by_currency: {},
    },
    debt_now: {
      total: "0.00",
      by_currency: {},
      currency: "KZT",
      orders: 0,
      overdue_by_currency: {},
      overdue_currency: "KZT",
      overdue_clients: 0,
    },
    days,
  };
}

describe("dashboardReportRange", () => {
  it("builds an inclusive local-calendar range across month boundaries", () => {
    expect(dashboardReportRange("2026-03-01", 3)).toEqual({
      from: "2026-02-27",
      to: "2026-03-01",
    });
  });
});

describe("adaptDashboardDebt", () => {
  it("uses the report aggregate and keeps overdue currency independent", () => {
    expect(
      adaptDashboardDebt({
        total: "1000.00",
        currency: "KZT",
        by_currency: { KZT: "1000.00", USD: "25.00" },
        orders: 2,
        overdue_by_currency: { USD: "5.00" },
        overdue_currency: "USD",
        overdue_clients: 1,
      }),
    ).toEqual({
      debtTotal: 1000,
      debtCurrency: "KZT",
      overdueTotal: 5,
      overdueCurrency: "USD",
      overdueClients: 1,
    });
  });
});

describe("adaptReportSummary", () => {
  it("maps the canonical report to fixed chart slots and keeps currencies separate", () => {
    const report = reportSummary([
      reportDay({
        date: "2026-07-30",
        orders: 2,
        bags: 12,
        revenue: "999999.00",
        received: "888888.00",
        payments: 1,
        revenue_by_currency: { KZT: "1200.00", USD: "300.00" },
        received_by_currency: { KZT: "250.00", USD: "10.00" },
      }),
      reportDay({
        date: "2026-07-28",
        orders: 1,
        bags: 4,
        revenue: "100.00",
        received: "50.00",
        revenue_by_currency: { USD: "100.00" },
        received_by_currency: { USD: "50.00" },
      }),
    ]);

    const metrics = adaptReportSummary(report, "2026-07-30", 3);

    expect(metrics.shippedByDay).toEqual([
      { label: "28", bags: 4, orders: 1 },
      { label: "29", bags: 0, orders: 0 },
      { label: "30", bags: 12, orders: 2 },
    ]);
    expect(metrics.spark).toEqual([
      { label: "28", revenue: 0, received: 0 },
      { label: "29", revenue: 0, received: 0 },
      { label: "30", revenue: 1200, received: 250 },
    ]);
    expect(metrics).toMatchObject({
      moneyCurrency: "KZT",
      shippedToday: 12,
      shippedYesterday: 0,
      shippedTodayOrders: 2,
      periodRevenue: 1200,
      periodReceived: 250,
      receivedToday: 250,
      receivedTodayCount: 1,
    });
  });

  it("uses legacy flat amounts only when a report has no currency maps", () => {
    const report = reportSummary([
      reportDay({
        date: "2026-07-30",
        revenue: "400.50",
        received: "125.25",
      }),
    ]);

    expect(adaptReportSummary(report, "2026-07-30", 1).spark).toEqual([
      { label: "30", revenue: 400.5, received: 125.25 },
    ]);
  });

  it("uses the report currency instead of silently zeroing a USD-only report", () => {
    const report = reportSummary([
      reportDay({
        date: "2026-07-30",
        revenue: "200.00",
        received: "75.00",
        revenue_by_currency: { USD: "200.00" },
        received_by_currency: { USD: "75.00" },
      }),
    ]);
    report.income.currency = "USD";
    report.shipped.currency = "USD";

    expect(adaptReportSummary(report, "2026-07-30", 1)).toMatchObject({
      moneyCurrency: "USD",
      spark: [{ label: "30", revenue: 200, received: 75 }],
      periodRevenue: 200,
      periodReceived: 75,
      receivedToday: 75,
    });
  });
});

describe("adaptOperationalShipments", () => {
  it("restores empty calendar slots around authoritative server totals", () => {
    const days = [
      { date: "2026-07-29", bags: 5, orders: 1 },
      { date: "2026-07-30", bags: 12, orders: 2 },
    ];

    const metrics = adaptOperationalShipments(days, "2026-07-30", 3);

    expect(metrics.shippedByDay).toEqual([
      { label: "28", bags: 0, orders: 0 },
      { label: "29", bags: 5, orders: 1 },
      { label: "30", bags: 12, orders: 2 },
    ]);
    expect(metrics).toMatchObject({
      shippedToday: 12,
      shippedYesterday: 5,
      shippedTodayOrders: 2,
    });
  });
});
