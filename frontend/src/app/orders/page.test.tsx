import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import OrdersPage from "./page";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  replace: vi.fn(),
  me: { permissions: ["orders.view"] } as Record<string, unknown>,
}));
vi.mock("@/store/auth", () => ({ useAuth: () => ({ me: mocks.me, loading: false }) }));
vi.mock("@/components/layout/app-shell", () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock("@/components/require-perm", () => ({
  RequirePerm: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: mocks.replace }),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/lib/api", () => ({
  api: { get: (...args: unknown[]) => mocks.get(...args) },
  apiError: () => "Ошибка",
  isCanceledRequest: () => false,
}));

beforeEach(() => {
  mocks.me = { permissions: ["orders.view"] };
  mocks.replace.mockReset();
  mocks.get.mockReset();
  mocks.get.mockImplementation(async (raw: string) => {
    const url = new URL(raw, "http://localhost");
    if (url.pathname === "/orders/" && url.searchParams.get("confirm_queue") === "1") {
      return {
        data: {
          results: [
            {
              id: 640,
              client_name: "Новый клиент",
              department: "",
              client_department: "main",
              client_department_name: "Мельница",
              currency: "KZT",
              status: "pending",
              total_amount: "500",
              paid_total: "0",
              items: [],
              created_at: "2026-09-17T08:00:00Z",
            },
          ],
          count: 1,
          next: null,
        },
      };
    }
    if (url.pathname === "/orders/") {
      const page = Number(url.searchParams.get("page"));
      return {
        data: {
          results: [
            {
              id: page,
              client_name: "Клиент",
              department: "",
              department_name: "Мельница",
              truck_number: "934PPB13",
              currency: "KZT",
              status: "confirmed",
              total_amount: "100",
              paid_total: "0",
              payment_status: "unpaid",
              items: [],
              created_at: "2026-09-01T10:00:00Z",
            },
          ],
          count: 70,
          next: page < 2 ? "next" : null,
        },
      };
    }
    return { data: [] };
  });
});

it("keeps searching and sorting paginated and loads later results only on request", async () => {
  const user = userEvent.setup();
  render(<OrdersPage />);
  const orderUrls = () =>
    mocks.get.mock.calls
      .map(([url]) => new URL(String(url), "http://localhost"))
      .filter((url) => url.pathname === "/orders/");
  await waitFor(() => expect(orderUrls()).toHaveLength(1));
  expect(screen.queryByText("Очередь заказов")).not.toBeInTheDocument();
  expect(screen.queryByRole("tab", { name: /Новые заявки/ })).not.toBeInTheDocument();
  expect(screen.getAllByText("Нет отдела").length).toBeGreaterThan(0);
  expect(screen.queryByText("Мельница")).not.toBeInTheDocument();
  expect(orderUrls()[0].searchParams.get("ordering")).toBe("-id");
  await user.type(screen.getByPlaceholderText("Поиск по клиенту, номеру или #ID"), "934");
  await waitFor(() => expect(orderUrls().at(-1)?.searchParams.get("search")).toBe("934"));
  expect(orderUrls()).toHaveLength(2);
  await user.click(screen.getByRole("button", { name: "Сумма" }));
  await waitFor(() => expect(orderUrls().at(-1)?.searchParams.get("ordering")).toBe("amount"));
  expect(
    orderUrls().every((url) => url.searchParams.get("page") === "1" && url.searchParams.get("page_size") === "50"),
  ).toBe(true);
  await user.click(screen.getByRole("button", { name: /Показать ещё/ }));
  await waitFor(() => expect(orderUrls().at(-1)?.searchParams.get("page")).toBe("2"));
  expect(orderUrls().at(-1)?.searchParams.get("search")).toBe("934");
  expect(orderUrls().at(-1)?.searchParams.get("ordering")).toBe("amount");
});

it("gives staff who confirm orders a «Заявки» tab and keeps a department employee on their department", async () => {
  const user = userEvent.setup();
  mocks.me = {
    permissions: ["orders.view", "orders.confirm"],
    sales_department: { id: 1, code: "main", name: "Мельница", color: "#123456" },
  };
  const baseGet = mocks.get.getMockImplementation()!;
  mocks.get.mockImplementation(async (raw: string) => {
    const url = new URL(raw, "http://localhost");
    if (url.pathname === "/orders/department-summary/") {
      return {
        data: [
          {
            id: 1,
            code: "main",
            name: "Мельница",
            color: "#123456",
            is_active: true,
            orders: 70,
            active: 3,
            shipped: 60,
            revenue: "100",
            revenue_currency: "KZT",
            revenue_by_currency: { KZT: "100" },
          },
        ],
      };
    }
    if (url.pathname === "/departments/") {
      return {
        data: [
          { id: 1, code: "main", name: "Мельница", color: "#123456", is_active: true },
          { id: 2, code: "field", name: "Нью-Сити", color: "#654321", is_active: true },
        ],
      };
    }
    return baseGet(raw);
  });
  render(<OrdersPage />);
  const requestsTab = await screen.findByRole("tab", { name: /Заявки/ });
  await waitFor(() => expect(requestsTab).toHaveTextContent("1"));
  expect(screen.getByRole("tab", { name: "Все заказы" })).toHaveAttribute("aria-selected", "true");
  // Закреплённый сотрудник видит свой отдел: без выбора отдела и с его названием в аналитике.
  expect(await screen.findByText(/Отдел Мельница/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /^Отдел/ })).not.toBeInTheDocument();

  await user.click(requestsTab);
  expect(mocks.replace).toHaveBeenCalledWith("/orders?tab=requests", { scroll: false });
  expect(await screen.findByRole("link", { name: "Заказ #640" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Проверить и подтвердить" })).toBeInTheDocument();
  expect(screen.queryByPlaceholderText("Поиск по клиенту, номеру или #ID")).not.toBeInTheDocument();
});
