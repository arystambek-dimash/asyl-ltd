import { fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { renderRoutePage } from "@/test-utils/route-page";
import ClientDetailPage from "./page";
import { resetNavigation } from "@/test-utils/next-navigation";

const mocks = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("next/navigation", () => import("@/test-utils/next-navigation"));
vi.mock("@/store/auth", () => ({
  useAuth: () => ({ me: { is_superuser: true, permissions: [] }, loading: false }),
}));
vi.mock("@/components/layout/app-shell", () => import("@/test-utils/app-shell"));
vi.mock("@/components/require-perm", () => import("@/test-utils/require-perm"));
vi.mock("@/lib/api", () => ({
  api: { get: (...args: unknown[]) => mocks.get(...args) },
  apiError: (error: unknown) => (error instanceof Error ? error.message : "Ошибка"),
  isCanceledRequest: () => false,
}));

// 24.09 в 00:30 по местному времени: восточнее Гринвича в UTC это ещё 23.09.
const earlyMorning = new Date(2026, 8, 24, 0, 30).toISOString();

const sale = (id: number, status: string, amount: string, extra: object = {}) => ({
  id,
  date: "2026-09-20T09:00:00+00:00",
  status,
  is_financial: !["draft", "pending", "rejected", "cancelled"].includes(status),
  settlement_intent: "debt",
  items: [{ label: "Мука 50 кг", qty: 10 }],
  bags: 10,
  amount,
  paid: "0.00",
  currency: "KZT",
  ...extra,
});

// Подписи строк отдаёт бэк (labels.py), как client_history.
const METHOD_LABELS: Record<string, string> = {
  cash: "Наличные",
  invoice: "Счёт на оплату",
  remote: "Удалённая оплата",
};
const STATUS_LABELS: Record<string, string> = { confirmed: "Оплачено", rejected: "Отклонено", received: "В кассе" };

const payment = (id: number, method: string, status: string, amount: string, counted: string) => ({
  id,
  order_id: 10,
  date: "2026-09-21T09:00:00+00:00",
  employee: "kassa",
  method,
  method_label: METHOD_LABELS[method],
  status,
  status_label: STATUS_LABELS[status],
  amount,
  counted_amount: counted,
  currency: "KZT",
  refunded_amount: "0.00",
});

const history = {
  client: { id: 1, name: "Береке", phone: "", country: "", currency: "KZT" },
  summary: {
    currency: "KZT",
    revenue: "1000.00",
    paid: "500.00",
    debt: "500.00",
    orders_count: 1,
    overpaid: "0.00",
    by_currency: { KZT: { revenue: "1000.00", paid: "500.00", debt: "500.00", overpaid: "0.00" } },
  },
  sales: [
    sale(10, "shipped", "1000.00"),
    sale(11, "cancelled", "300.00"),
    sale(12, "pending", "700.00", { date: earlyMorning, settlement_intent: "pending" }),
  ],
  payments: [
    // 600 подтверждено, 100 вернули — в «Оплачено» идёт 500.
    payment(1, "cash", "confirmed", "600.00", "500.00"),
    payment(2, "invoice", "rejected", "200.00", "0.00"),
    payment(3, "remote", "received", "50.00", "0.00"),
  ],
  debts: [],
};

beforeEach(() => {
  resetNavigation("/clients/1");
  mocks.get.mockReset();
  mocks.get.mockResolvedValue({ data: history });
});

const renderPage = () => renderRoutePage(ClientDetailPage, "1");

it("shows what we owe the client after an overpayment, per currency", async () => {
  mocks.get.mockResolvedValue({
    data: {
      ...history,
      summary: {
        ...history.summary,
        by_currency: {
          ...history.summary.by_currency,
          USD: { revenue: "2100245.00", paid: "2105920.00", debt: "0.00", overpaid: "5675.00" },
        },
      },
    },
  });
  await renderPage();

  expect(await screen.findByText("Мы должны клиенту 5 675 $")).toBeInTheDocument();
  expect(screen.queryByText(/Мы должны клиенту .* ₸/)).not.toBeInTheDocument();
});

it("goes below zero when there is no debt and we owe the client", async () => {
  mocks.get.mockResolvedValue({
    data: {
      ...history,
      summary: {
        ...history.summary,
        debt: "0.00",
        overpaid: "7059300.00",
        by_currency: {
          KZT: { revenue: "253940700.00", paid: "261000000.00", debt: "0.00", overpaid: "7059300.00" },
          USD: { revenue: "100.00", paid: "120.00", debt: "0.00", overpaid: "20.00" },
        },
      },
    },
  });
  await renderPage();

  expect(await screen.findByText("−7 059 300 ₸")).toBeInTheDocument();
  expect(screen.getByText("мы должны клиенту")).toBeInTheDocument();
  expect(screen.getByText("Также −20 $")).toBeInTheDocument();
  expect(screen.queryByText("задолженности нет")).not.toBeInTheDocument();
});

it("opens the reconciliation act import from the header", async () => {
  const user = userEvent.setup();
  await renderPage();

  await user.click(await screen.findByRole("button", { name: "Импорт акта сверки" }));

  expect(screen.getByRole("dialog", { name: "Импорт акта сверки" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Выбрать .xlsx" })).toBeInTheDocument();
});

it("sums only financial sales into the list total", async () => {
  const user = userEvent.setup();
  await renderPage();
  await user.click(await screen.findByRole("tab", { name: /Продажи/ }));

  // Отмена и заявка в таблице видны, но в итог, как и в «Сумму продаж», не входят.
  expect(screen.getByText(/итог по списку/)).toHaveTextContent("1 000 ₸");
  expect(screen.getByText("№ 11")).toBeInTheDocument();
  // Способ ещё не выбран — подпись, а не сырой код.
  expect(screen.getByRole("cell", { name: "Не выбран" })).toBeInTheDocument();
});

it("sums confirmed payments net of refunds and offers every method from the data", async () => {
  const user = userEvent.setup();
  await renderPage();
  await user.click(await screen.findByRole("tab", { name: /Погашения/ }));

  expect(screen.getByText(/итог по списку/)).toHaveTextContent("500 ₸");
  expect(screen.getByText(/итог по списку/)).not.toHaveTextContent("850");

  const payFilter = screen.getByRole("combobox", { name: "Оплата" });
  const options = within(payFilter)
    .getAllByRole("option")
    .map((o) => o.textContent);
  expect(options).toEqual(["Любой тип", "Наличные", "Счёт на оплату", "Удалённая оплата"]);
});

it("filters the period by the local calendar day shown in the table", async () => {
  const user = userEvent.setup();
  await renderPage();
  await user.click(await screen.findByRole("tab", { name: /Продажи/ }));

  fireEvent.change(screen.getByLabelText("Период с"), { target: { value: "2026-09-24" } });
  fireEvent.change(screen.getByLabelText("Период по"), { target: { value: "2026-09-24" } });

  expect(screen.getByText("№ 12")).toBeInTheDocument();
  expect(screen.queryByText("№ 10")).not.toBeInTheDocument();
});

it("keeps common filters across tabs and resets the tab-specific ones", async () => {
  const user = userEvent.setup();
  await renderPage();
  expect(await screen.findByRole("tab", { name: /Продажи/ })).toHaveTextContent("3");
  await user.click(screen.getByRole("tab", { name: /Погашения/ }));

  fireEvent.change(screen.getByLabelText("Период с"), { target: { value: "2026-09-01" } });
  fireEvent.change(screen.getByRole("combobox", { name: "Оплата" }), { target: { value: "cash" } });
  await user.click(screen.getByRole("tab", { name: /Продажи/ }));

  expect(screen.getByLabelText("Период с")).toHaveValue("2026-09-01");
  expect(screen.getByRole("combobox", { name: "Оплата" })).toHaveValue("all");

  await user.click(screen.getAllByRole("button", { name: /Сбросить фильтры/ })[0]);
  expect(screen.getByLabelText("Период с")).toHaveValue("");
  expect(screen.queryByRole("button", { name: /Сбросить фильтры/ })).not.toBeInTheDocument();
});
