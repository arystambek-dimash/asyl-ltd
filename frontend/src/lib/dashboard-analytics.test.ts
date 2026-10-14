import { describe, expect, it } from "vitest";
import { adaptDashboardDebt, todayIncome, todayShipments } from "@/lib/dashboard-analytics";
import type { ReportSummary } from "@/lib/types";

function reportSummary(): ReportSummary {
  return {
    departments: [],
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
      by_method_by_currency: {},
      payments_by_method: {},
      method_labels: {},
    },
    shipped: {
      revenue: "0.00",
      orders: 0,
      bags: 0,
      paid_amount: "0.00",
      debt_amount: "0.00",
      currency: "KZT",
      revenue_by_currency: {},
      paid_amount_by_currency: {},
      debt_amount_by_currency: {},
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
    clients: [],
    days: [],
  };
}

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
      debtOthers: [["USD", 25]],
      overdueTotal: 5,
      overdueCurrency: "USD",
      overdueOthers: [],
      overdueClients: 1,
    });
  });

  it("shows zero tenge while the report has not loaded", () => {
    expect(adaptDashboardDebt(undefined)).toEqual({
      debtTotal: 0,
      debtCurrency: "KZT",
      debtOthers: [],
      overdueTotal: 0,
      overdueCurrency: "KZT",
      overdueOthers: [],
      overdueClients: 0,
    });
  });
});

describe("todayShipments", () => {
  it("takes today and yesterday from server days and splits trucks and wagons", () => {
    const days = [
      { date: "2026-10-12", bags: 2134, orders: 11, truck_bags: 2134, train_bags: 0, kg: "106700.00" },
      { date: "2026-10-13", bags: 897, orders: 9, truck_bags: 620, train_bags: 277, kg: "44850.00" },
    ];

    expect(todayShipments(days, "2026-10-13", "2026-10-12")).toEqual({
      shippedToday: 897,
      shippedTodayOrders: 9,
      truckBagsToday: 620,
      trainBagsToday: 277,
      shippedTodayKg: 44850,
      shippedYesterday: 2134,
    });
  });

  it("shows zeros for a day the server did not return", () => {
    expect(todayShipments([], "2026-10-13", "2026-10-12")).toEqual({
      shippedToday: 0,
      shippedTodayOrders: 0,
      truckBagsToday: 0,
      trainBagsToday: 0,
      shippedTodayKg: 0,
      shippedYesterday: 0,
    });
  });
});

describe("todayIncome", () => {
  it("reads the one-day report in the currency the server chose", () => {
    const report = reportSummary();
    report.income.currency = "USD";
    report.income.by_currency = { USD: "75.00", KZT: "1000.00" };
    report.income.payments = 3;

    expect(todayIncome(report)).toEqual({ receivedToday: 75, receivedTodayCount: 3, moneyCurrency: "USD" });
  });

  it("shows zero tenge while the report has not loaded", () => {
    expect(todayIncome(null)).toEqual({ receivedToday: 0, receivedTodayCount: 0, moneyCurrency: "KZT" });
  });
});
