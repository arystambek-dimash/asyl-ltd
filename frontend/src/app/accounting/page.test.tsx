import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { resetNavigation, routerCalls } from "@/test-utils/next-navigation";
import CashierPage from "./page";

const SUPERUSER = { is_superuser: true, permissions: [] as string[] };
const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  paid: false,
  queueError: false,
  poll: async () => {},
  me: null as unknown,
}));
vi.mock("@/lib/use-visible-polling", () => ({
  useVisiblePolling: (poll: () => Promise<void>, _interval: number, active: boolean) => {
    if (active) mocks.poll = poll;
  },
}));
vi.mock("@/store/auth", () => ({ useAuth: () => ({ me: mocks.me, loading: false }) }));
vi.mock("@/components/layout/app-shell", () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock("@/components/require-perm", () => ({
  RequirePerm: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock("next/navigation", () => import("@/test-utils/next-navigation"));
vi.mock("@/lib/toast", () => ({ showSuccess: vi.fn() }));
vi.mock("@/lib/api", () => ({
  api: { get: (...args: unknown[]) => mocks.get(...args), post: (...args: unknown[]) => mocks.post(...args) },
  apiError: (error: unknown) => (error instanceof Error ? error.message : "Ошибка"),
  isCanceledRequest: () => false,
}));

const queueItem = {
  id: 1,
  order: 1,
  amount: "100",
  currency: "KZT",
  method: "cash",
  status: "received",
  client_name: "Клиент",
};
type User = ReturnType<typeof userEvent.setup>;

/** Сводка и фильтры кассы живут в окнах — тест открывает их так же, как кассир. */
async function openSummary(user: User) {
  await user.click(await screen.findByRole("button", { name: /Аналитика/ }));
}
async function openFilters(user: User) {
  await user.click(await screen.findByRole("button", { name: /Фильтры/ }));
}
async function closeModal(user: User) {
  await user.click(screen.getAllByRole("button", { name: "Закрыть" })[0]);
}

function card(title: string) {
  const node = screen.getByText(title).parentElement;
  if (!node) throw new Error("Карточка отсутствует");
  return within(node);
}
beforeEach(() => {
  resetNavigation("/accounting");
  mocks.me = SUPERUSER;
  mocks.paid = false;
  mocks.queueError = false;
  mocks.get.mockReset();
  mocks.post.mockReset();
  mocks.get.mockImplementation(async (raw: string) => {
    const url = new URL(raw, "http://localhost");
    if (url.pathname === "/reports/summary/") {
      const value = mocks.paid ? "100" : "0";
      return {
        data: {
          income: {
            total: value,
            cash: value,
            cashless: "0",
            gross: value,
            refunded: "0",
            currency: "KZT",
            by_currency: { KZT: value },
          },
        },
      };
    }
    if (url.pathname === "/orders/payments-queue/") {
      if (mocks.queueError) throw new Error("Очередь временно недоступна");
      const rows = mocks.paid ? [] : [queueItem];
      if (url.searchParams.has("page")) return { data: { results: rows, count: rows.length, next: null } };
      return { data: rows };
    }
    if (url.pathname === "/orders/awaiting-payment/") return { data: { results: [], count: 0, next: null } };
    if (url.pathname === "/orders/awaiting-shipment/") return { data: { results: [], count: 0, next: null } };
    if (url.pathname === "/orders/to-refund/") return { data: { results: [], count: 0, next: null } };
    if (url.pathname === "/clients/debts/")
      return {
        data: mocks.paid
          ? []
          : [
              {
                client_id: 1,
                client_name: "Клиент",
                client_phone: "",
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
          results: [],
          page: 1,
          pages: 1,
          count: 0,
          status_counts: {},
          summary: { paid_by_currency: { KZT: "100", USD: "0" }, refunded_by_currency: { KZT: "0", USD: "0" } },
        },
      };
    return { data: [] };
  });
  mocks.post.mockImplementation(async () => {
    mocks.paid = true;
    return { data: {} };
  });
});

it("confirmation refreshes income, debt and the overview queue when returning to overview", async () => {
  const user = userEvent.setup();
  render(<CashierPage />);
  await openSummary(user);
  await waitFor(() => expect(card("Дебиторка").getByText(/100/)).toBeInTheDocument());
  await user.click(screen.getByRole("tab", { name: /^Оплаты/ }));
  await user.click(await screen.findByRole("button", { name: "Подтвердить получение" }));
  await waitFor(() => expect(mocks.post).toHaveBeenCalledWith("/orders/1/payments/1/confirm/"));
  await user.click(screen.getByRole("tab", { name: "Общее" }));
  await openSummary(user);
  await waitFor(() => expect(card("Чистое поступление за всё время").getAllByText(/100/).length).toBeGreaterThan(0));
  expect(card("Дебиторка").queryByText(/100/)).not.toBeInTheDocument();
  expect(card("Ожидает подтверждения").getByText("0")).toBeInTheDocument();
  expect(mocks.get.mock.calls.some(([url]) => url === "/reports/summary/?section=income")).toBe(true);
});

it("uses overview dates for its payment card and never presents a failed queue as zero", async () => {
  const user = userEvent.setup();
  render(<CashierPage />);
  await openSummary(user);
  await waitFor(() => expect(card("Ожидает подтверждения").getAllByText(/100/).length).toBeGreaterThan(0));
  mocks.queueError = true;
  await closeModal(user);
  await openFilters(user);
  fireEvent.change(screen.getByLabelText("С даты"), { target: { value: "2026-09-01" } });
  await waitFor(() =>
    expect(mocks.get.mock.calls.some(([url]) => url === "/orders/payments-queue/?summary=1&date_from=2026-09-01")).toBe(
      true,
    ),
  );
  await closeModal(user);
  expect(await screen.findByText("Очередь временно недоступна")).toBeInTheDocument();
  await openSummary(user);
  expect(card("Ожидает подтверждения").getByText("—")).toBeInTheDocument();
  expect(card("Ожидает подтверждения").queryByText("0")).not.toBeInTheDocument();
});

it("loads only overview totals initially and fetches each confirmation page once on demand", async () => {
  const user = userEvent.setup();
  render(<CashierPage />);
  await openSummary(user);
  await waitFor(() => expect(card("Ожидает подтверждения").getAllByText(/100/).length).toBeGreaterThan(0));
  const urls = () => mocks.get.mock.calls.map(([url]) => String(url));
  expect(urls().filter((url) => url.startsWith("/orders/payments-queue/"))).toEqual([
    "/orders/payments-queue/?summary=1",
  ]);
  expect(urls().some((url) => url.startsWith("/orders/awaiting-payment/"))).toBe(false);
  await user.click(screen.getByRole("tab", { name: /^Оплаты/ }));
  await screen.findByRole("button", { name: "Подтвердить получение" });
  expect(urls().filter((url) => url === "/orders/payments-queue/?page=1&page_size=50")).toHaveLength(1);
  expect(urls().filter((url) => url === "/orders/awaiting-payment/?page=1&page_size=50")).toHaveLength(1);
  // Заявки на заказ переехали в «Заказы».
  expect(urls().some((url) => url.startsWith("/orders/?"))).toBe(false);
});

it("gives a cashier locked to a department the queue of every department and links only own orders", async () => {
  const user = userEvent.setup();
  mocks.me = {
    is_superuser: false,
    permissions: ["payments.confirm", "payments.view", "reports.view", "orders.view", "orders.confirm"],
    sales_department: { id: 1, code: "main", name: "Мельница", color: "#123456" },
  };
  const baseGet = mocks.get.getMockImplementation()!;
  mocks.get.mockImplementation(async (raw: string) => {
    const url = new URL(raw, "http://localhost");
    if (url.pathname === "/orders/payments-queue/" && url.searchParams.has("page")) {
      const own = { ...queueItem, id: 1, order: 11, department: "main", department_name: "Мельница" };
      const foreign = { ...queueItem, id: 2, order: 12, department: "field", department_name: "Нью-Сити" };
      return { data: { results: [own, foreign], count: 2, next: null } };
    }
    return baseGet(raw);
  });
  render(<CashierPage />);
  await user.click(await screen.findByRole("tab", { name: /^Оплаты/ }));
  expect(await screen.findByRole("link", { name: "Заказ #11" })).toBeInTheDocument();
  const urls = mocks.get.mock.calls.map(([url]) => String(url));
  expect(urls).toContain("/orders/payments-queue/?page=1&page_size=50");
  // «Ждут оплаты» сервер сам режет по отделу кассира.
  expect(urls).toContain("/orders/awaiting-payment/?page=1&page_size=50");
  // Бейдж отдела показывает, чья оплата; карточку заказа другого отдела касса не открывает.
  expect(screen.getByText("Нью-Сити")).toBeInTheDocument();
  expect(screen.getByText("Заказ #12")).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Заказ #12" })).not.toBeInTheDocument();
});

it("takes payment for a shipped order or moves it to debt from «Ждут оплаты»", async () => {
  const user = userEvent.setup();
  const baseGet = mocks.get.getMockImplementation()!;
  mocks.get.mockImplementation(async (raw: string) => {
    const url = new URL(raw, "http://localhost");
    if (url.pathname === "/orders/awaiting-payment/")
      return {
        data: {
          results: [
            {
              id: 632,
              client_name: "Покупатель",
              status: "shipped",
              payment_open: true,
              payment_open_methods: ["cash", "kaspi", "remote", "invoice"],
              payment_request_open: true,
              currency: "KZT",
              department: "field",
              department_name: "Нью-Сити",
              total_amount: "300",
              paid_total: "100",
              remaining_amount: "200",
              settlement_intent: "instant",
              pending_payments: [],
              items: [],
            },
          ],
          count: 1,
          next: null,
        },
      };
    return baseGet(raw);
  });
  render(<CashierPage />);
  await user.click(screen.getByRole("tab", { name: /^Оплаты/ }));
  expect(await screen.findByRole("link", { name: "Заказ #632" })).toBeInTheDocument();
  expect(screen.getByText("Оплата не завершена")).toBeInTheDocument();
  expect(screen.getByText(/оплачено 100 ₸ из 300 ₸/)).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: /Принять оплату/ }));
  expect(await screen.findByLabelText(/Сумма/)).toHaveValue(200);
  await user.click(screen.getByRole("button", { name: "Принять" }));
  await waitFor(() =>
    expect(mocks.post).toHaveBeenCalledWith("/orders/632/payments/", {
      amount: "200",
      method: "cash",
      stage: "received",
    }),
  );

  await user.click(screen.getByRole("button", { name: "В долг" }));
  const dialog = await screen.findByRole("dialog", { name: "Оставить заказ #632 в долг?" });
  await user.click(within(dialog).getByRole("button", { name: "В долг" }));
  await waitFor(() => expect(mocks.post).toHaveBeenCalledWith("/orders/632/to-debt/"));
});

it("desktop tab click mirrors the view into the URL and deep links open the tab", async () => {
  const user = userEvent.setup();
  render(<CashierPage />);
  await user.click(screen.getByRole("tab", { name: /^Оплаты/ }));
  expect(routerCalls.replace).toContain("/accounting?view=confirm");

  resetNavigation("/accounting?view=overview");
  await openSummary(user);
  expect(await screen.findByText("Дебиторка")).toBeInTheDocument();
  expect(screen.queryByRole("tab", { name: "Журнал" })).not.toBeInTheDocument();
});

/** Подтверждённый, ещё не отгруженный заказ: сервер открыл предоплату деньгами у кассы. */
const toShipOrder = {
  id: 700,
  client_name: "Предоплата",
  status: "confirmed",
  payment_open: true,
  payment_open_methods: ["cash", "kaspi", "remote"],
  payment_request_open: false,
  currency: "KZT",
  department: "main",
  department_name: "Мельница",
  total_amount: "500",
  paid_total: "0",
  remaining_amount: "500",
  overpaid_amount: "0.00",
  pending_payments: [],
  payments: [],
  items: [],
  created_at: "2026-09-22T10:00:00",
};

it("takes a prepayment for an order awaiting shipment from «К отгрузке»", async () => {
  const user = userEvent.setup();
  const baseGet = mocks.get.getMockImplementation()!;
  mocks.get.mockImplementation(async (raw: string) => {
    const url = new URL(raw, "http://localhost");
    if (url.pathname === "/orders/awaiting-shipment/")
      return { data: { results: [toShipOrder], count: 1, next: null } };
    return baseGet(raw);
  });
  render(<CashierPage />);
  await user.click(screen.getByRole("tab", { name: /^Оплаты/ }));
  await user.click(await screen.findByRole("tab", { name: "К отгрузке, 1" }));

  expect(await screen.findByRole("link", { name: "Заказ #700" })).toBeInTheDocument();
  expect(screen.getByText("Ожидает загрузки")).toBeInTheDocument();
  // Счёт на телефон и «В долг» — только после отгрузки.
  expect(screen.queryByRole("button", { name: /Отправить удалённый счёт/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "В долг" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /Принять оплату/ }));
  await user.click(screen.getByRole("button", { name: /Удалённая оплата/ }));
  await user.click(screen.getByRole("button", { name: "Принять" }));
  await waitFor(() =>
    expect(mocks.post).toHaveBeenCalledWith("/orders/700/payments/", {
      amount: "500",
      method: "remote",
      stage: "received",
    }),
  );
  const urls = mocks.get.mock.calls.map(([url]) => String(url));
  expect(urls).toContain("/orders/awaiting-shipment/?page=1&page_size=50");
});

it("returns an overpayment to the client from «К возврату»", async () => {
  const user = userEvent.setup();
  const baseGet = mocks.get.getMockImplementation()!;
  mocks.get.mockImplementation(async (raw: string) => {
    const url = new URL(raw, "http://localhost");
    if (url.pathname === "/orders/to-refund/")
      return {
        data: {
          results: [
            {
              ...toShipOrder,
              id: 701,
              total_amount: "300",
              paid_total: "500",
              remaining_amount: "0",
              overpaid_amount: "200.00",
              payments: [
                {
                  id: 91,
                  order: 701,
                  currency: "KZT",
                  amount: "500.00",
                  method: "cash",
                  status: "confirmed",
                  paid_at: "2026-09-22T10:00:00",
                  recorded_by: null,
                  available_for_refund: "500.00",
                },
              ],
            },
          ],
          count: 1,
          next: null,
        },
      };
    return baseGet(raw);
  });
  render(<CashierPage />);
  await user.click(screen.getByRole("tab", { name: /^Оплаты/ }));
  await user.click(await screen.findByRole("tab", { name: "К возврату, 1" }));

  expect(await screen.findByRole("link", { name: "Заказ #701" })).toBeInTheDocument();
  expect(screen.getByText("200 ₸")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /Вернуть переплату/ }));
  const dialog = await screen.findByRole("dialog", { name: "Вернуть оплату" });
  await user.type(within(dialog).getByLabelText("Причина"), "Уменьшили заказ");
  await user.click(within(dialog).getByRole("button", { name: "Оформить возврат" }));
  await waitFor(() =>
    expect(mocks.post).toHaveBeenCalledWith("/payment-transactions/91/refund/", {
      amount: "200",
      reason: "Уменьшили заказ",
      mode: "auto",
    }),
  );
});

it("keeps the Kaspi QR refund window open after the order leaves «К возврату»", async () => {
  const user = userEvent.setup();
  const qrRefund = {
    id: 5,
    status: "awaiting_customer",
    amount: "200.00",
    refunded_amount: null,
    client_name: null,
    customer_url: "https://pay.example/refund/5",
    link_expires_at: null,
    operations: [],
    receipt_url: null,
    error_code: null,
    error_message: null,
    created_at: "2026-09-23T10:00:00",
  };
  let refundStarted = false;
  const overpaid = {
    ...toShipOrder,
    id: 702,
    total_amount: "300",
    paid_total: "500",
    remaining_amount: "0",
    overpaid_amount: "200.00",
    payments: [
      {
        id: 92,
        order: 702,
        currency: "KZT",
        amount: "500.00",
        method: "kaspi",
        status: "confirmed",
        paid_at: "2026-09-22T10:00:00",
        recorded_by: null,
        available_for_refund: "500.00",
        provider: { channel: "qr" },
      },
    ],
  };
  const baseGet = mocks.get.getMockImplementation()!;
  mocks.get.mockImplementation(async (raw: string) => {
    const url = new URL(raw, "http://localhost");
    if (url.pathname === "/payment-transactions/92/qr-refund/") return { data: qrRefund };
    // Начатый возврат резервирует деньги — заказ уходит из «К возврату».
    if (url.pathname === "/orders/to-refund/" && !refundStarted)
      return { data: { results: [overpaid], count: 1, next: null } };
    return baseGet(raw);
  });
  mocks.post.mockImplementation(async () => {
    refundStarted = true;
    return { data: { method: "apipay_qr", qr_refund: qrRefund } };
  });
  render(<CashierPage />);
  await user.click(screen.getByRole("tab", { name: /^Оплаты/ }));
  await user.click(await screen.findByRole("tab", { name: "К возврату, 1" }));
  await user.click(await screen.findByRole("button", { name: /Вернуть переплату/ }));
  const dialog = await screen.findByRole("dialog", { name: "Вернуть оплату" });
  await user.type(within(dialog).getByLabelText("Причина"), "Уменьшили заказ");
  await user.click(within(dialog).getByRole("button", { name: "Показать QR" }));

  expect(await screen.findByRole("dialog", { name: "Возврат по QR" })).toBeInTheDocument();
  await waitFor(() => expect(screen.queryByRole("link", { name: "Заказ #702" })).not.toBeInTheDocument());
  expect(screen.getByRole("dialog", { name: "Возврат по QR" })).toBeInTheDocument();
});
