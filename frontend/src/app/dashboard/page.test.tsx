import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import DashboardPage from "./page";

const auth = vi.hoisted(() => ({ permissions: ["dashboard.view"], superuser: false }));
const metrics = vi.hoisted(() => ({
  chooseWarehouse: vi.fn(),
  intakeError: "",
  syncAvailable: true,
}));
vi.mock("@/store/auth", () => ({
  useAuth: () => ({ me: { id: 1, permissions: auth.permissions, is_superuser: auth.superuser }, loading: false }),
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
    stockWarehouse: "Мельница",
    warehouses: [
      { id: 1, code: "main", name: "Мельница", address: "", is_active: true, is_default: true },
      { id: 2, code: "second", name: "Мельница 2", address: "", is_active: true, is_default: false },
    ],
    warehouseId: 1,
    chooseWarehouse: metrics.chooseWarehouse,
    settings: { departments: [{ id: 1, code: "main", name: "Мельница" }], warehouses: [], updated_at: "" },
    commitSettings: vi.fn(),
    intake: {
      day: "2026-10-13",
      total: 1109,
      all_time_total: 166_947,
      analytics_sync: { status: "synced", available: metrics.syncAvailable, detail: "Журнал камер отстал" },
      cameras: [
        {
          camera: "cam3",
          name: "Робот Кука",
          total: 1109,
          colors: [
            { color: "red", total: 600, percent: 54 },
            { color: "blue", total: 509, percent: 46 },
          ],
        },
      ],
    },
    intakeError: metrics.intakeError,
    loading: false,
    lastUpdatedAt: null,
    loadError: "",
    reload: vi.fn(),
    canOrders: true,
    canStock: true,
    canFinance: true,
    canIntake: true,
  }),
}));

afterEach(() => {
  localStorage.clear();
  auth.permissions = ["dashboard.view"];
  auth.superuser = false;
  metrics.intakeError = "";
  metrics.syncAvailable = true;
});

it("без права на главную показывает «Нет доступа» вместо сводки", () => {
  auth.permissions = ["orders.view"];
  render(<DashboardPage />);

  expect(screen.getByText("Нет доступа")).toBeInTheDocument();
  expect(screen.queryByText("Приход сегодня")).not.toBeInTheDocument();
});

it("ведёт из плитки долга сразу на долги в кассе", async () => {
  render(<DashboardPage />);

  expect(await screen.findByRole("link", { name: /Долг клиентов/ })).toHaveAttribute("href", "/accounting?view=debts");
});

it("показывает приход с камер 24/7: всего, по камерам и цветам", async () => {
  render(<DashboardPage />);

  const panel = (await screen.findByRole("heading", { name: "Приход сегодня" })).closest("section")!;
  expect(within(panel).getAllByText("1 109")).toHaveLength(2);
  expect(within(panel).getByText("Робот Кука")).toBeInTheDocument();
  expect(within(panel).getByText("Красный 600")).toBeInTheDocument();
  expect(within(panel).getByText("Синий 509")).toBeInTheDocument();
});

it("не выдаёт ноль за приход, когда журнал камер не догнан", async () => {
  metrics.syncAvailable = false;
  render(<DashboardPage />);

  const panel = (await screen.findByRole("heading", { name: "Приход сегодня" })).closest("section")!;
  expect(within(panel).getByText("Нет данных с камер")).toBeInTheDocument();
  expect(within(panel).getByText("Журнал камер отстал")).toBeInTheDocument();
  expect(within(panel).queryByText("1 109")).not.toBeInTheDocument();
});

it("«Что показывать» видит только суперпользователь", async () => {
  render(<DashboardPage />);
  await screen.findByRole("heading", { name: "Приход сегодня" });
  expect(screen.queryByRole("button", { name: /Что показывать/ })).not.toBeInTheDocument();
});

it("суперпользователь открывает выбор отделов и складов", async () => {
  auth.superuser = true;
  render(<DashboardPage />);

  fireEvent.click(await screen.findByRole("button", { name: /Что показывать/ }));

  expect(screen.getByRole("dialog", { name: "Что показывать" })).toBeInTheDocument();
  expect(screen.getByText("Отделы")).toBeInTheDocument();
  expect(screen.getByText("Склады")).toBeInTheDocument();
});

it("показывает отгрузку дня по фурам и вагонам", async () => {
  render(<DashboardPage />);

  expect(await screen.findByText("фуры 620 · вагоны 277")).toBeInTheDocument();
  expect(screen.getByText("вчера 2 134")).toBeInTheDocument();
});

it("показывает остаток одного склада, минус и сколько позиций не влезло", async () => {
  render(<DashboardPage />);

  const panel = (await screen.findByRole("heading", { name: "Остаток · Мельница" })).closest("section")!;
  expect(within(panel).getByText("Мука 1")).toBeInTheDocument();
  expect(within(panel).queryByText("Мука 13")).not.toBeInTheDocument();
  expect(within(panel).getByRole("link", { name: "ещё 2 позиции · 868 меш." })).toHaveAttribute("href", "/warehouse");
});

it("режим ТВ: сводка поверх меню без ссылок, Esc выходит, выбор помнится", async () => {
  render(<DashboardPage />);

  fireEvent.click(await screen.findByRole("button", { name: /Режим ТВ/ }));

  const tv = screen.getByRole("dialog", { name: "Режим ТВ" });
  expect(within(tv).getByText("Приход сегодня")).toBeInTheDocument();
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

it("переключает склад сводки", async () => {
  render(<DashboardPage />);

  const select = await screen.findByRole("combobox", { name: "Склад" });
  expect(select).toHaveValue("1");
  fireEvent.change(select, { target: { value: "2" } });

  expect(metrics.chooseWarehouse).toHaveBeenCalledWith(2);
  expect(screen.getByText("Мельница · 14 позиций")).toBeInTheDocument();
});
