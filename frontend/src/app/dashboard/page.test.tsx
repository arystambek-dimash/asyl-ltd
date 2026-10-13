import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import DashboardPage from "./page";

const auth = vi.hoisted(() => ({ permissions: ["dashboard.view"] }));
vi.mock("@/store/auth", () => ({
  useAuth: () => ({ me: { id: 1, permissions: auth.permissions }, loading: false }),
}));
vi.mock("@/components/layout/app-shell", () => import("@/test-utils/app-shell"));
vi.mock("next/link", () => import("@/test-utils/next-link"));

const stockRows = Array.from({ length: 14 }, (_, index) => ({
  product: index + 1,
  title: `Мука ${index + 1}`,
  bags: index === 13 ? -12 : 1000 - index * 10,
}));

vi.mock("@/lib/use-dashboard-metrics", () => ({
  useDashboardMetrics: () => ({
    today: "2026-10-13",
    queue: [],
    shippedToday: 897,
    shippedTodayOrders: 9,
    truckBagsToday: 620,
    trainBagsToday: 277,
    shippedYesterday: 2134,
    receivedToday: 8_240_000,
    receivedTodayCount: 8,
    moneyCurrency: "KZT",
    debtTotal: 900_000,
    debtCurrency: "KZT",
    debtOthers: [],
    overdueTotal: 300_000,
    overdueCurrency: "KZT",
    overdueOthers: [],
    overdueClients: 2,
    stockTotal: 13_078,
    stockRows,
    attention: [
      { key: "overdue", count: 2 },
      { key: "payments", count: 3 },
    ],
    attentionCount: 5,
    loading: false,
    lastUpdatedAt: null,
    loadError: "",
    reload: vi.fn(),
    canOrders: true,
    canStock: true,
    canFinance: true,
    canPayments: true,
  }),
}));

afterEach(() => {
  localStorage.clear();
  auth.permissions = ["dashboard.view"];
});

it("без права на главную показывает «Нет доступа» вместо сводки", () => {
  auth.permissions = ["orders.view"];
  render(<DashboardPage />);

  expect(screen.getByText("Нет доступа")).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: /Подтвердить оплаты/ })).not.toBeInTheDocument();
});

it("ведёт из «Нужно решить» и плитки долга сразу на нужный экран кассы", async () => {
  render(<DashboardPage />);

  const confirm = await screen.findByRole("link", { name: /Подтвердить оплаты/ });
  expect(confirm).toHaveAttribute("href", "/accounting?view=confirm");
  expect(screen.getByRole("link", { name: /Просрочена оплата/ })).toHaveAttribute("href", "/accounting?view=debts");
  expect(screen.getByRole("link", { name: /Долг клиентов/ })).toHaveAttribute("href", "/accounting?view=debts");
});

it("показывает отгрузку дня по фурам и вагонам", async () => {
  render(<DashboardPage />);

  expect(await screen.findByText("фуры 620 · вагоны 277")).toBeInTheDocument();
  expect(screen.getByText("вчера 2 134")).toBeInTheDocument();
});

it("показывает остаток по товарам, минус и сколько позиций не влезло", async () => {
  render(<DashboardPage />);

  const panel = (await screen.findByRole("heading", { name: "Остаток по товарам" })).closest("section")!;
  expect(within(panel).getByText("Мука 1")).toBeInTheDocument();
  expect(within(panel).queryByText("Мука 13")).not.toBeInTheDocument();
  expect(within(panel).getByRole("link", { name: "ещё 2 позиции · 868 меш." })).toHaveAttribute("href", "/warehouse");
});

it("режим ТВ: сводка поверх меню без ссылок, Esc выходит, выбор помнится", async () => {
  render(<DashboardPage />);

  fireEvent.click(await screen.findByRole("button", { name: /Режим ТВ/ }));

  const tv = screen.getByRole("dialog", { name: "Режим ТВ" });
  expect(within(tv).getByText("Подтвердить оплаты")).toBeInTheDocument();
  expect(within(tv).queryAllByRole("link")).toEqual([]);
  expect(localStorage.getItem("dashboard:tv:1")).toBe("1");

  fireEvent.keyDown(window, { key: "Escape" });

  expect(screen.queryByRole("dialog", { name: "Режим ТВ" })).not.toBeInTheDocument();
  expect(localStorage.getItem("dashboard:tv:1")).toBeNull();
});

it("телевизор после перезагрузки сразу открывает режим ТВ", async () => {
  localStorage.setItem("dashboard:tv:1", "1");
  render(<DashboardPage />);

  expect(await screen.findByRole("dialog", { name: "Режим ТВ" })).toBeInTheDocument();
});
