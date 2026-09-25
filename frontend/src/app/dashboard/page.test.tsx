import { render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import DashboardPage from "./page";

vi.mock("@/store/auth", () => ({ useAuth: () => ({ me: { id: 1, permissions: [] }, loading: false }) }));
vi.mock("@/components/layout/app-shell", () => import("@/test-utils/app-shell"));
vi.mock("next/link", () => import("@/test-utils/next-link"));
// Графики к ссылкам отношения не имеют, а в jsdom у них нет размеров.
vi.mock("recharts", () => {
  const Stub = () => null;
  return {
    Area: Stub,
    AreaChart: Stub,
    Bar: Stub,
    BarChart: Stub,
    CartesianGrid: Stub,
    Cell: Stub,
    ResponsiveContainer: Stub,
    Tooltip: Stub,
    XAxis: Stub,
  };
});
vi.mock("@/lib/use-dashboard-metrics", () => ({
  useDashboardMetrics: () => ({
    queue: [],
    totalBags: "0",
    shippedToday: "0",
    shippedTodayOrders: 0,
    shippedYesterday: "0",
    shippedByDay: [],
    spark: [],
    periodRevenue: 0,
    periodReceived: 0,
    receivedToday: 0,
    receivedTodayCount: 0,
    moneyCurrency: "KZT",
    stockPositionCount: 0,
    negativeStock: [],
    debtTotal: 900_000,
    debtCurrency: "KZT",
    overdueTotal: 300_000,
    overdueCurrency: "KZT",
    overdueClients: 2,
    attention: [
      { key: "overdue", count: 2 },
      { key: "payments", count: 3 },
    ],
    attentionCount: 5,
    loading: false,
    stale: false,
    lastUpdatedAt: null,
    loadError: "",
    reload: vi.fn(),
    canOrders: false,
    canStock: false,
    canFinance: true,
    canPayments: true,
  }),
}));

afterEach(() => localStorage.clear());

it("ведёт из «Нужно решить» и сводки долга сразу на нужный экран кассы", async () => {
  render(<DashboardPage />);

  const confirm = await screen.findByRole("link", { name: /Подтвердить оплаты/ });
  expect(confirm).toHaveAttribute("href", "/accounting?view=confirm");
  expect(screen.getByRole("link", { name: /Просрочена оплата/ })).toHaveAttribute("href", "/accounting?view=debts");
  expect(screen.getByRole("link", { name: /Просроченный долг/ })).toHaveAttribute("href", "/accounting?view=debts");
});

it("шапка и «Нужно решить» показывают одно и то же число задач", async () => {
  render(<DashboardPage />);

  expect(await screen.findByText("5 требуют действия")).toBeInTheDocument();
  expect(screen.getByText("5", { selector: "span" })).toBeInTheDocument();
});

it("помнит период аналитики отдельно для каждого сотрудника", async () => {
  localStorage.setItem("dashboard:period", "7");
  localStorage.setItem("dashboard:period:1", "30");
  render(<DashboardPage />);

  expect(await screen.findByRole("combobox", { name: "Период аналитики" })).toHaveValue("30");
});
