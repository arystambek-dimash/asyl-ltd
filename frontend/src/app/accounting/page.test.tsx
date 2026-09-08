import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import CashierPage from "./page";

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), paid: false, queueError: false, poll: async () => {} }));
vi.mock("@/lib/use-visible-polling", () => ({
  useVisiblePolling: (poll: () => Promise<void>, _interval: number, active: boolean) => {
    if (active) mocks.poll = poll;
  },
}));
vi.mock("@/store/auth", () => ({ useAuth: () => ({ me: { is_superuser: true, permissions: [] }, loading: false }) }));
vi.mock("@/components/layout/app-shell", () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock("@/components/require-perm", () => ({
  RequirePerm: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
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
function card(title: string) {
  const node = screen.getByText(title).parentElement;
  if (!node) throw new Error("Карточка отсутствует");
  return within(node);
}
beforeEach(() => {
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
    if (url.pathname === "/orders/") return { data: { results: [], count: 0, next: null } };
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
  await waitFor(() => expect(card("Дебиторка").getByText(/100/)).toBeInTheDocument());
  await user.click(screen.getByRole("tab", { name: /Заявки и оплаты/ }));
  await user.click(await screen.findByRole("button", { name: "Подтвердить получение" }));
  await waitFor(() => expect(mocks.post).toHaveBeenCalledWith("/orders/1/payments/1/confirm/"));
  await user.click(screen.getByRole("tab", { name: "Общее" }));
  await waitFor(() => expect(card("Чистое поступление за всё время").getAllByText(/100/).length).toBeGreaterThan(0));
  expect(card("Дебиторка").queryByText(/100/)).not.toBeInTheDocument();
  expect(card("Ожидает подтверждения").getByText("0")).toBeInTheDocument();
  expect(mocks.get.mock.calls.some(([url]) => url === "/reports/summary/?section=income")).toBe(true);
});

it("uses overview dates for its payment card and never presents a failed queue as zero", async () => {
  render(<CashierPage />);
  await waitFor(() => expect(card("Ожидает подтверждения").getAllByText(/100/).length).toBeGreaterThan(0));
  mocks.queueError = true;
  fireEvent.change(screen.getByLabelText("С даты"), { target: { value: "2026-09-01" } });
  await waitFor(() =>
    expect(mocks.get.mock.calls.some(([url]) => url === "/orders/payments-queue/?summary=1&date_from=2026-09-01")).toBe(
      true,
    ),
  );
  expect(await screen.findByText("Очередь временно недоступна")).toBeInTheDocument();
  expect(card("Ожидает подтверждения").getByText("—")).toBeInTheDocument();
  expect(card("Ожидает подтверждения").queryByText("0")).not.toBeInTheDocument();
});

it("loads only overview totals initially and fetches each confirmation page once on demand", async () => {
  const user = userEvent.setup();
  render(<CashierPage />);
  await waitFor(() => expect(card("Ожидает подтверждения").getAllByText(/100/).length).toBeGreaterThan(0));
  const urls = () => mocks.get.mock.calls.map(([url]) => String(url));
  expect(urls().filter((url) => url.startsWith("/orders/payments-queue/"))).toEqual([
    "/orders/payments-queue/?summary=1",
  ]);
  expect(urls().some((url) => url.startsWith("/orders/?"))).toBe(false);
  await user.click(screen.getByRole("tab", { name: /Заявки и оплаты/ }));
  await screen.findByRole("button", { name: "Подтвердить получение" });
  expect(urls().filter((url) => url === "/orders/payments-queue/?page=1&page_size=50")).toHaveLength(1);
  expect(urls().filter((url) => url === "/orders/?status_group=pending&page=1&page_size=50")).toHaveLength(1);
});

it("keeps entered confirmation data when a background refresh removes the row from the page", async () => {
  const user = userEvent.setup();
  const baseGet = mocks.get.getMockImplementation()!;
  let includeRequest = true;
  mocks.get.mockImplementation(async (raw: string) => {
    const path = new URL(raw, "http://localhost").pathname;
    if (path === "/orders/")
      return {
        data: {
          results: includeRequest
            ? [
                {
                  id: 621,
                  department: "",
                  department_name: "Мельница",
                  client_name: "Клиент",
                  status: "pending",
                  currency: "KZT",
                  total_amount: "0",
                  items: [{ id: 7, product: 1, quantity: 2, product_label: "Мука" }],
                },
              ]
            : [],
          count: includeRequest ? 1 : 0,
          next: null,
        },
      };
    if (path === "/departments/") return { data: [{ id: 1, code: "main", name: "Мельница", is_active: true }] };
    return baseGet(raw);
  });
  render(<CashierPage />);
  await user.click(screen.getByRole("tab", { name: /Заявки и оплаты/ }));
  const confirm = await screen.findByRole("button", { name: "Проверить и подтвердить" });
  expect(screen.getAllByText("Нет отдела").length).toBeGreaterThan(0);
  await user.click(confirm);
  await user.selectOptions(await screen.findByRole("combobox", { name: "Отдел продаж" }), "main");
  await user.type(screen.getByRole("spinbutton", { name: "Цена: Мука" }), "1234");
  includeRequest = false;
  await act(async () => {
    await mocks.poll();
  });
  expect(screen.getByRole("combobox", { name: "Отдел продаж" })).toHaveValue("main");
  expect(screen.getByRole("spinbutton", { name: "Цена: Мука" })).toHaveValue(1234);
  await user.click(screen.getByRole("button", { name: "Подтвердить заказ" }));
  await waitFor(() => expect(screen.queryByRole("combobox", { name: "Отдел продаж" })).not.toBeInTheDocument());
  expect(mocks.post).toHaveBeenCalledWith("/orders/621/confirm/", { department: "main", prices: { "7": "1234" } });
});
