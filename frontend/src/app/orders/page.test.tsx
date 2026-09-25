import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import OrdersPage from "./page";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  replace: vi.fn(),
  push: vi.fn(),
  me: { permissions: ["orders.view"] } as Record<string, unknown>,
}));
vi.mock("@/store/auth", () => ({ useAuth: () => ({ me: mocks.me, loading: false }) }));
vi.mock("@/components/layout/app-shell", () => import("@/test-utils/app-shell"));
vi.mock("@/components/require-perm", () => import("@/test-utils/require-perm"));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mocks.push, replace: mocks.replace }),
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
  mocks.push.mockReset();
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
            debt: "0",
            debt_by_currency: {},
            paid: "0",
            paid_by_currency: {},
            paid_orders: 0,
            partial_orders: 0,
            unpaid_orders: 1,
            debt_orders: 0,
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
  // Аналитика скрыта в окне и не грузится, пока его не открыли.
  expect(screen.queryByText(/Отдел Мельница/)).not.toBeInTheDocument();
  const summaryCalls = () =>
    mocks.get.mock.calls.filter(([raw]) => String(raw).startsWith("/orders/department-summary/")).length;
  expect(summaryCalls()).toBe(0);
  await user.click(screen.getByRole("button", { name: /Аналитика/ }));
  const analytics = await screen.findByRole("dialog", { name: "Аналитика заказов" });
  // Закреплённый сотрудник видит свой отдел: без выбора отдела и с его названием в аналитике.
  expect(await within(analytics).findByText(/Отдел Мельница/)).toBeInTheDocument();
  expect(summaryCalls()).toBe(1);
  expect(screen.queryByRole("button", { name: /^Отдел/ })).not.toBeInTheDocument();
  // Нажал на отдел — окно закрывается, список показывает заказы отдела.
  await user.click(within(analytics).getByRole("button", { name: /Мельница/ }));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "Аналитика заказов" })).not.toBeInTheDocument());
  await waitFor(() =>
    expect(
      mocks.get.mock.calls
        .map(([raw]) => new URL(String(raw), "http://localhost"))
        .some((url) => url.pathname === "/orders/" && url.searchParams.get("department") === "main"),
    ).toBe(true),
  );

  await user.click(requestsTab);
  expect(mocks.replace).toHaveBeenCalledWith("/orders?tab=requests", { scroll: false });
  expect(await screen.findByRole("link", { name: "Заказ #640" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Проверить и подтвердить" })).toBeInTheDocument();
  expect(screen.queryByPlaceholderText("Поиск по клиенту, номеру или #ID")).not.toBeInTheDocument();
});

it("shows no tabs to staff who edit orders but do not review requests", async () => {
  mocks.me = { permissions: ["orders.view", "orders.edit"] };
  render(<OrdersPage />);

  expect(await screen.findByPlaceholderText("Поиск по клиенту, номеру или #ID")).toBeInTheDocument();
  expect(screen.queryByRole("tab")).not.toBeInTheDocument();
});

it("shows a prepayment badge before shipment and «Не оплачен» only after it", async () => {
  const row = (fields: Record<string, unknown>) => ({
    client_name: "Клиент",
    department: "",
    currency: "KZT",
    total_amount: "100",
    items: [],
    created_at: "2026-09-01T10:00:00Z",
    ...fields,
  });
  const baseGet = mocks.get.getMockImplementation()!;
  mocks.get.mockImplementation(async (raw: string) => {
    const url = new URL(raw, "http://localhost");
    if (url.pathname === "/orders/" && url.searchParams.get("confirm_queue") !== "1")
      return {
        data: {
          results: [
            row({ id: 1, status: "confirmed", paid_total: "0", payment_status: "unpaid" }),
            row({ id: 2, status: "confirmed", paid_total: "100", payment_status: "settled" }),
          ],
          count: 2,
          next: null,
        },
      };
    return baseGet(raw);
  });
  const { rerender } = render(<OrdersPage />);

  expect((await screen.findAllByText("Оплачен")).length).toBeGreaterThan(0);
  expect(screen.queryByText("Не оплачен")).not.toBeInTheDocument();

  mocks.get.mockImplementation(async (raw: string) => {
    const url = new URL(raw, "http://localhost");
    if (url.pathname === "/orders/" && url.searchParams.get("confirm_queue") !== "1")
      return {
        data: {
          results: [row({ id: 3, status: "shipped", paid_total: "0", payment_status: "unpaid" })],
          count: 1,
          next: null,
        },
      };
    return baseGet(raw);
  });
  rerender(<></>);
  rerender(<OrdersPage />);
  expect((await screen.findAllByText("Не оплачен")).length).toBeGreaterThan(0);
});

it("opens the confirmation window of a request chosen as «Ожидает загрузки» in the list", async () => {
  const user = userEvent.setup();
  mocks.me = { permissions: ["orders.view", "orders.edit"] };
  const baseGet = mocks.get.getMockImplementation()!;
  mocks.get.mockImplementation(async (raw: string) => {
    const url = new URL(raw, "http://localhost");
    if (url.pathname === "/orders/" && url.searchParams.get("confirm_queue") !== "1")
      return {
        data: {
          results: [
            {
              id: 7,
              client_name: "Клиент",
              department: "",
              currency: "KZT",
              status: "pending",
              total_amount: "100",
              paid_total: "0",
              items: [],
              created_at: "2026-09-01T10:00:00Z",
            },
          ],
          count: 1,
          next: null,
        },
      };
    return baseGet(raw);
  });
  render(<OrdersPage />);

  const [select] = await screen.findAllByRole("combobox", { name: "Статус заказа" });
  await user.selectOptions(select, "confirmed");

  // Подтверждение живёт в карточке: она сама откроет окно по ?confirm=1.
  expect(mocks.push).toHaveBeenCalledWith("/orders/7?confirm=1");
});

it("takes «Общая» totals of the whole selection from the server, not from the loaded page", async () => {
  const user = userEvent.setup();
  const baseGet = mocks.get.getMockImplementation()!;
  mocks.get.mockImplementation(async (raw: string) => {
    const url = new URL(raw, "http://localhost");
    if (url.pathname === "/orders/list-summary/")
      return {
        data: {
          orders: 70,
          active: 12,
          total_currency: "KZT",
          total_by_currency: { KZT: "7000.00", USD: "5.00" },
          by_status_group: { confirmed: "6000.00", shipped: "1000.00" },
        },
      };
    return baseGet(raw);
  });
  render(<OrdersPage />);
  const urls = (path: string) =>
    mocks.get.mock.calls
      .map(([raw]) => new URL(String(raw), "http://localhost"))
      .filter((url) => url.pathname === path);
  await user.type(screen.getByPlaceholderText("Поиск по клиенту, номеру или #ID"), "934");
  await waitFor(() => expect(urls("/orders/").at(-1)?.searchParams.get("search")).toBe("934"));
  await user.click(screen.getByRole("button", { name: /Аналитика/ }));
  const analytics = await screen.findByRole("dialog", { name: "Аналитика заказов" });
  await user.click(within(analytics).getByRole("tab", { name: /Общая/ }));

  // Список загрузил одну страницу, а итоги — по всем 70 заказам выборки.
  expect(await within(analytics).findByText("70")).toBeInTheDocument();
  expect(within(analytics).getByText("12")).toBeInTheDocument();
  expect(within(analytics).getByText(/7\s000 ₸/)).toBeInTheDocument();
  expect(within(analytics).getByText(/ещё 5 \$/)).toBeInTheDocument();
  const [summaryUrl] = urls("/orders/list-summary/");
  // Итоги — по тем же фильтрам и поиску, что и список, без страниц и сортировки.
  expect(summaryUrl?.searchParams.get("search")).toBe("934");
  expect(summaryUrl?.searchParams.has("page")).toBe(false);
  expect(summaryUrl?.searchParams.has("ordering")).toBe(false);
});

it("stretches the empty row over the actions column for staff who only correct prices", async () => {
  mocks.me = { permissions: ["orders.view", "orders.correct_price"] };
  const baseGet = mocks.get.getMockImplementation()!;
  mocks.get.mockImplementation(async (raw: string) => {
    const url = new URL(raw, "http://localhost");
    if (url.pathname === "/orders/" && !url.searchParams.has("confirm_queue")) {
      return { data: { results: [], count: 0, next: null, previous: null } };
    }
    return baseGet(raw);
  });
  render(<OrdersPage />);
  const emptyCell = (await screen.findAllByText("Заказов пока нет.")).find((el) => el.tagName === "TD")!;
  const headerCells = emptyCell.closest("table")!.querySelectorAll("thead th");
  expect(emptyCell).toHaveAttribute("colspan", String(headerCells.length));
});
