import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, beforeEach, expect, it, vi } from "vitest";
import CashierPage from "@/app/accounting/page";
import { resetNavigation, routerCalls } from "@/test-utils/next-navigation";
import type { TopbarBack } from "@/components/layout/topbar";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  me: { is_superuser: true, permissions: [] as string[] },
  byMethod: true,
  pendingCount: 0,
}));
vi.mock("next/navigation", () => import("@/test-utils/next-navigation"));
vi.mock("@/lib/use-visible-polling", () => ({ useVisiblePolling: () => {} }));
vi.mock("@/store/auth", () => ({ useAuth: () => ({ me: mocks.me, loading: false }) }));
vi.mock("@/components/layout/app-shell", () => ({
  AppShell: ({
    title,
    back,
    trailing,
    children,
  }: {
    title: string;
    back?: TopbarBack;
    trailing?: React.ReactNode;
    children: React.ReactNode;
  }) => (
    <div>
      <h1>{title}</h1>
      {back && (
        <button type="button" onClick={back.onClick}>
          {back.label}
        </button>
      )}
      {trailing}
      {children}
    </div>
  ),
}));
vi.mock("@/components/require-perm", () => ({
  RequirePerm: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock("@/lib/toast", () => ({ showSuccess: vi.fn() }));
vi.mock("@/lib/api", () => ({
  api: { get: (...args: unknown[]) => mocks.get(...args), post: (...args: unknown[]) => mocks.post(...args) },
  apiError: (error: unknown) => (error instanceof Error ? error.message : "Ошибка"),
  isCanceledRequest: () => false,
}));

const today = new Date();
const todayIso = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
const queueItem = {
  id: 1,
  order: 1,
  amount: "100",
  currency: "KZT",
  method: "cash",
  status: "received",
  client_name: "Клиент",
};
const transaction = {
  id: 5,
  order: 1,
  amount: "100",
  currency: "KZT",
  method: "cash",
  method_label: "Наличные",
  status: "confirmed",
  effective_status: "confirmed",
  paid_at: `${todayIso}T10:00:00`,
  recorded_by: null,
  client_name: "Клиент",
  available_for_refund: "100",
};
const logEvent = {
  id: 1,
  message: "Оплата подтверждена",
  user_name: "Касса",
  order: 1,
  client_name: "Клиент",
  store_name: null,
  payload: { payment_id: 1 },
  created_at: `${todayIso}T09:00:00`,
  can_reopen: true,
  can_restore: false,
};

beforeAll(() => {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    writable: true,
    value: () => ({ matches: true, media: "", addEventListener: () => {}, removeEventListener: () => {} }),
  });
});

beforeEach(() => {
  resetNavigation("/accounting");
  mocks.me = { is_superuser: true, permissions: [] };
  mocks.byMethod = true;
  mocks.pendingCount = 0;
  mocks.get.mockReset();
  mocks.post.mockReset();
  mocks.post.mockResolvedValue({ data: {} });
  mocks.get.mockImplementation(async (raw: string) => {
    const url = new URL(raw, "http://localhost");
    if (url.pathname === "/reports/summary/") {
      return {
        data: {
          from: url.searchParams.get("from"),
          to: url.searchParams.get("to"),
          income: {
            total: "100",
            cash: "100",
            cashless: "0",
            gross: "100",
            refunded: "0",
            payments: 1,
            refunds: 0,
            currency: "KZT",
            by_currency: { KZT: "100" },
            cash_by_currency: { KZT: "100" },
            cashless_by_currency: { KZT: "0" },
            gross_by_currency: { KZT: "100" },
            refunded_by_currency: {},
            ...(mocks.byMethod
              ? { by_method_by_currency: { KZT: { cash: "100" } }, payments_by_method: { cash: 1 } }
              : {}),
          },
          departments: [
            {
              code: "main",
              name: "Мельница",
              color: "#123456",
              orders: null,
              sales_by_currency: null,
              received_by_currency: { KZT: "100" },
              refunded_by_currency: {},
              net_by_currency: { KZT: "100" },
              payments: 1,
            },
          ],
        },
      };
    }
    if (url.pathname === "/orders/payments-queue/") {
      if (url.searchParams.get("summary") === "1")
        return { data: [{ currency: "KZT", method: "cash", amount: "100", count: 1 }] };
      return { data: { results: [queueItem], count: 1, next: null } };
    }
    if (url.pathname === "/orders/") return { data: { results: [], count: mocks.pendingCount, next: null } };
    if (url.pathname === "/orders/cashier-log/") return { data: { results: [logEvent], count: 1, next: null } };
    if (url.pathname === "/clients/debts/")
      return {
        data: [
          {
            client_id: 1,
            client_name: "Клиент",
            client_phone: "+7 700 000 00 00",
            debt_total: "100",
            debt_currency: "KZT",
            debt_by_currency: { KZT: "100" },
            unpaid_count: 1,
            partial_count: 0,
            orders_count: 1,
            stores_count: 0,
            overdue_count: 0,
          },
        ],
      };
    if (url.pathname === "/payment-transactions/")
      return {
        data: {
          results: [transaction],
          page: 1,
          pages: 1,
          count: 1,
          status_counts: { confirmed: 1 },
          summary: {
            paid_by_currency: { KZT: "100", USD: "0" },
            refunded_by_currency: { KZT: "0", USD: "0" },
            paid_by_method: { KZT: { cash: "100" } },
          },
        },
      };
    return { data: [] };
  });
});

it("shows the home menu with live subtitles and opens a section by pushing ?view=", async () => {
  const user = userEvent.setup();
  mocks.pendingCount = 2;
  render(<CashierPage />);
  expect(await screen.findByRole("heading", { name: "Касса" })).toBeInTheDocument();
  const menu = within(screen.getByRole("navigation", { name: "Разделы кассы" }));
  await waitFor(() => expect(menu.getByText("2 заявки · 1 оплата на 100 ₸")).toBeInTheDocument());
  expect(menu.getByText("1 клиент · 100 ₸")).toBeInTheDocument();
  expect(menu.getAllByRole("button").map((b) => b.textContent)).toEqual([
    expect.stringContaining("Заявки и оплаты"),
    expect.stringContaining("Долги клиентов"),
    expect.stringContaining("Транзакции"),
    expect.stringContaining("Журнал"),
    expect.stringContaining("Отчёт по поступлениям"),
  ]);
  const headline = await screen.findByRole("button", { name: /Поступления за сегодня/ });
  expect(headline).toHaveTextContent("100 ₸");
  expect(
    mocks.get.mock.calls.some(([url]) => url === `/reports/summary/?section=income&from=${todayIso}&to=${todayIso}`),
  ).toBe(true);

  await user.click(menu.getByRole("button", { name: /Заявки и оплаты/ }));
  expect(routerCalls.push).toEqual(["/accounting?view=confirm"]);
  expect(await screen.findByRole("heading", { name: "Заявки и оплаты" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Назад в кассу" }));
  expect(routerCalls.back).toBe(1);
  expect(await screen.findByRole("heading", { name: "Касса" })).toBeInTheDocument();
});

it("opens the only available section directly, without a home screen", async () => {
  mocks.me = { is_superuser: false, permissions: ["payments.view"] };
  render(<CashierPage />);
  expect(await screen.findByRole("heading", { name: "Транзакции" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Назад в кассу" })).not.toBeInTheDocument();
});

it("returns to the home screen by replace when a sub-screen was opened by link", async () => {
  const user = userEvent.setup();
  resetNavigation("/accounting?view=confirm");
  render(<CashierPage />);
  await user.click(await screen.findByRole("button", { name: "Назад в кассу" }));
  expect(routerCalls.back).toBe(0);
  expect(routerCalls.replace).toEqual(["/accounting"]);
});

it("confirms a payment from the queue screen", async () => {
  const user = userEvent.setup();
  resetNavigation("/accounting?view=confirm");
  render(<CashierPage />);
  expect(await screen.findByRole("tab", { name: /Оплаты/ })).toHaveAttribute("aria-selected", "true");
  await user.click(await screen.findByRole("button", { name: "Подтвердить получение" }));
  await waitFor(() => expect(mocks.post).toHaveBeenCalledWith("/orders/1/payments/1/confirm/"));
  await user.click(screen.getByRole("tab", { name: /Заявки/ }));
  expect(await screen.findByText("Нет заявок, ожидающих подтверждения.")).toBeInTheDocument();
});

it("groups the journal by day and reopens a payment", async () => {
  const user = userEvent.setup();
  resetNavigation("/accounting?view=journal");
  render(<CashierPage />);
  expect(await screen.findByRole("heading", { name: "Сегодня" })).toBeInTheDocument();
  expect(screen.getByText("Оплата подтверждена")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Вернуть на подтверждение" }));
  await waitFor(() => expect(mocks.post).toHaveBeenCalledWith("/orders/1/payments/1/reopen/"));
});

it("does not claim the queue is empty while it failed to load", async () => {
  resetNavigation("/accounting?view=confirm");
  const baseGet = mocks.get.getMockImplementation()!;
  mocks.get.mockImplementation(async (raw: string) => {
    const url = new URL(raw, "http://localhost");
    if (url.pathname === "/orders/payments-queue/" && url.searchParams.get("summary") !== "1") {
      throw new Error("Очередь временно недоступна");
    }
    return baseGet(raw);
  });
  render(<CashierPage />);
  expect(await screen.findByText("Очередь временно недоступна")).toBeInTheDocument();
  expect(screen.queryByText("Нет оплат, ожидающих подтверждения.")).not.toBeInTheDocument();
});

it("lists debtors as tappable rows with a totals line", async () => {
  resetNavigation("/accounting?view=debts");
  render(<CashierPage />);
  const row = await screen.findByRole("link", { name: "Долг клиента Клиент" });
  expect(row).toHaveAttribute("href", "/accounting/debts/clients/1");
  expect(screen.getByText(/1 клиент/)).toBeInTheDocument();
  expect(screen.getByText("Не оплачен")).toBeInTheDocument();
});

it("draws the report for today and switches the breakdown", async () => {
  const user = userEvent.setup();
  resetNavigation("/accounting?view=report");
  render(<CashierPage />);
  expect(await screen.findByRole("button", { name: "Сегодня", pressed: true })).toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole("img", { name: /Мельница: 100%/ })).toBeInTheDocument());
  await user.click(screen.getByRole("tab", { name: "По способу" }));
  expect(screen.getByRole("img", { name: /Наличные: 100%/ })).toBeInTheDocument();
  expect(screen.getByText("1 оплата")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Всё" }));
  await waitFor(() =>
    expect(mocks.get.mock.calls.some(([url]) => url === "/reports/summary/?section=income")).toBe(true),
  );
});

it("shows cash and cashless when the backend has no method breakdown", async () => {
  const user = userEvent.setup();
  mocks.byMethod = false;
  resetNavigation("/accounting?view=report");
  render(<CashierPage />);
  await user.click(await screen.findByRole("tab", { name: "По способу" }));
  await waitFor(() => expect(screen.getByText("Безналичные")).toBeInTheDocument());
});

it("does not claim there are no debts while the list failed to load", async () => {
  resetNavigation("/accounting?view=debts");
  const baseGet = mocks.get.getMockImplementation()!;
  mocks.get.mockImplementation(async (raw: string) => {
    const url = new URL(raw, "http://localhost");
    if (url.pathname === "/clients/debts/") throw new Error("Долги временно недоступны");
    return baseGet(raw);
  });
  render(<CashierPage />);
  expect(await screen.findByText("Долги временно недоступны")).toBeInTheDocument();
  expect(screen.queryByText("Долгов нет.")).not.toBeInTheDocument();
});

it("opens a transaction sheet with actions and hands off to the refund modal", async () => {
  const user = userEvent.setup();
  resetNavigation("/accounting?view=transactions");
  render(<CashierPage />);
  expect(await screen.findByRole("heading", { name: "Сегодня" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /PAY-000005/ }));
  const sheet = await screen.findByRole("dialog", { name: "Оплачено" });
  expect(within(sheet).getByText("Журнал операции")).toBeInTheDocument();
  await user.click(within(sheet).getByRole("button", { name: /Вернуть оплату/ }));
  expect(await screen.findByRole("dialog", { name: "Вернуть оплату" })).toBeInTheDocument();
  expect(screen.queryByRole("dialog", { name: "Оплачено" })).not.toBeInTheDocument();
});

it("report sheet reset keeps the period", async () => {
  const user = userEvent.setup();
  resetNavigation("/accounting?view=report");
  render(<CashierPage />);
  await user.click(await screen.findByRole("button", { name: /Фильтры/ }));
  const reportDialog = await screen.findByRole("dialog", { name: "Фильтры" });
  expect(within(reportDialog).getByRole("button", { name: "Сбросить" })).toBeDisabled();
  await user.click(within(reportDialog).getByRole("button", { name: "Готово" }));
  expect(await screen.findByRole("button", { name: "Сегодня", pressed: true })).toBeInTheDocument();

  resetNavigation("/accounting?view=debts");
  await user.click(await screen.findByRole("button", { name: /Фильтры/ }));
  fireEvent.change(screen.getByLabelText("С даты"), { target: { value: "2026-09-01" } });
  expect(await screen.findByRole("button", { name: "Фильтры, применено: 1" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Сбросить" }));
  expect(await screen.findByRole("button", { name: "Фильтры" })).toBeInTheDocument();
});

it("invalid custom period shows the range error and no loading label", async () => {
  const user = userEvent.setup();
  resetNavigation("/accounting?view=report");
  render(<CashierPage />);
  await user.click(await screen.findByRole("button", { name: /Период/ }));
  const dialog = await screen.findByRole("dialog", { name: "Свой период" });
  fireEvent.change(within(dialog).getByLabelText("С даты"), { target: { value: "2026-09-12" } });
  fireEvent.change(within(dialog).getByLabelText("По дату"), { target: { value: "2026-09-01" } });
  await waitFor(() =>
    expect(screen.getAllByText("Дата начала не может быть позже даты окончания.").length).toBeGreaterThan(0),
  );
  expect(screen.queryByText("Загрузка…")).not.toBeInTheDocument();
});

it("home falls back to the queue headline without reports.view", async () => {
  mocks.me = { is_superuser: false, permissions: ["payments.confirm", "payments.view"] };
  render(<CashierPage />);
  const headline = await screen.findByRole("button", { name: /Ожидает подтверждения/ });
  expect(headline).toHaveTextContent("100 ₸");
});
