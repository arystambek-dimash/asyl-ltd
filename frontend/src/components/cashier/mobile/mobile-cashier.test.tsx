import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, beforeEach, expect, it, vi } from "vitest";
import CashierPage from "@/app/accounting/page";
import { resetNavigation, routerCalls } from "@/test-utils/next-navigation";
import { CASHIER_DEPARTMENTS, stubPhoneMatchMedia } from "@/test-utils/cashier";

type SalesDepartment = { id: number; code: string; name: string; color: string } | null;
const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  me: {
    is_superuser: true,
    permissions: [] as string[],
    id: 7,
    username: "kassa",
    first_name: "",
    last_name: "",
    sales_department: null as SalesDepartment,
  },
  awaitingCount: 0,
}));
const defaultMe = mocks.me;
vi.mock("next/navigation", () => import("@/test-utils/next-navigation"));
vi.mock("@/lib/use-visible-polling", () => ({ useVisiblePolling: () => {} }));
vi.mock("@/store/auth", () => ({ useAuth: () => ({ me: mocks.me, loading: false }) }));
vi.mock("@/components/layout/app-shell", () => import("@/test-utils/app-shell"));
vi.mock("@/components/require-perm", () => import("@/test-utils/require-perm"));
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
const awaitingOrder = {
  id: 21,
  client: 3,
  client_name: "Покупатель",
  status: "shipped",
  payment_open: true,
  payment_open_methods: ["cash", "kaspi", "remote", "invoice"],
  payment_request_open: true,
  currency: "KZT",
  department: "field",
  department_name: "Нью-Сити",
  total_amount: "250",
  paid_total: "0",
  remaining_amount: "250",
  settlement_intent: "pending",
  pending_payments: [],
  items: [],
  created_at: `${todayIso}T08:00:00`,
  shipped_at: `${todayIso}T09:00:00`,
};
/** Подтверждённый, ещё не отгруженный заказ: сервер открыл предоплату деньгами у кассы. */
const toShipOrder = {
  ...awaitingOrder,
  id: 31,
  status: "confirmed",
  payment_open_methods: ["cash", "kaspi", "remote"],
  payment_request_open: false,
  shipped_at: null,
};
const transaction = {
  id: 5,
  order: 1,
  amount: "100",
  currency: "KZT",
  method: "cash",
  method_label: "Наличные",
  status: "confirmed",
  status_label: "Оплачено",
  effective_status: "confirmed",
  effective_status_label: "Оплачено",
  paid_at: `${todayIso}T10:00:00`,
  client_name: "Клиент",
  available_for_refund: "100",
};

beforeAll(stubPhoneMatchMedia);

beforeEach(() => {
  resetNavigation("/accounting");
  localStorage.clear();
  mocks.me = defaultMe;
  mocks.awaitingCount = 0;
  mocks.get.mockReset();
  mocks.post.mockReset();
  mocks.post.mockResolvedValue({ data: {} });
  mocks.get.mockImplementation(async (raw: string) => {
    const url = new URL(raw, "http://localhost");
    if (url.pathname === "/departments/") return { data: CASHIER_DEPARTMENTS };
    if (url.pathname === "/reports/summary/") {
      return {
        data: {
          from: url.searchParams.get("date_from"),
          to: url.searchParams.get("date_to"),
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
            by_method_by_currency: { KZT: { cash: "100" } },
            payments_by_method: { cash: 1 },
            method_labels: { cash: "Наличные" },
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
    if (url.pathname === "/orders/awaiting-payment/") {
      if (url.searchParams.get("summary") === "1")
        return { data: mocks.awaitingCount ? [{ currency: "KZT", amount: "250", count: mocks.awaitingCount }] : [] };
      return { data: { results: [awaitingOrder], count: 1, next: null } };
    }
    if (url.pathname === "/orders/awaiting-shipment/")
      return { data: { results: [toShipOrder], count: 1, next: null } };
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
          status_labels: { requested: "Ожидает", received: "В кассе", confirmed: "Оплачено", rejected: "Отклонено" },
          summary: {
            paid_by_currency: { KZT: "100", USD: "0" },
            refunded_by_currency: { KZT: "0", USD: "0" },
            paid_by_method: { KZT: { cash: "100" } },
            method_labels: { cash: "Наличные" },
          },
        },
      };
    return { data: [] };
  });
});

it("shows the home menu with live subtitles and opens a section by pushing ?view=", async () => {
  const user = userEvent.setup();
  mocks.awaitingCount = 2;
  render(<CashierPage />);
  expect(await screen.findByRole("heading", { name: "Все отделы" })).toBeInTheDocument();
  expect(screen.getByTestId("section")).toHaveTextContent("kassa");
  const menu = within(screen.getByRole("navigation", { name: "Разделы кассы" }));
  await waitFor(() => expect(menu.getByText("1 оплата на 100 ₸ · 2 ждут оплаты на 250 ₸")).toBeInTheDocument());
  expect(menu.getByText("1 клиент · 100 ₸")).toBeInTheDocument();
  expect(menu.getAllByRole("button").map((b) => b.textContent)).toEqual([
    expect.stringContaining("Оплаты"),
    expect.stringContaining("Долги клиентов"),
    expect.stringContaining("Транзакции"),
    expect.stringContaining("Отчёт по поступлениям"),
  ]);
  // Удалённая оплата живёт только во вкладке POS: строки на главной нет даже с полными правами.
  expect(screen.queryByRole("button", { name: /Удаленная оплата/ })).not.toBeInTheDocument();
  const headline = await screen.findByRole("button", { name: /Поступления за сегодня/ });
  expect(headline).toHaveTextContent("100 ₸");
  expect(
    mocks.get.mock.calls.some(
      ([url]) => url === `/reports/summary/?section=income&date_from=${todayIso}&date_to=${todayIso}`,
    ),
  ).toBe(true);

  await user.click(menu.getByRole("button", { name: /^Оплаты/ }));
  expect(routerCalls.push).toEqual(["/accounting?view=confirm"]);
  expect(await screen.findByRole("heading", { name: "Оплаты" })).toBeInTheDocument();
  expect(screen.getByTestId("section")).toHaveTextContent("Все отделы");
  await user.click(screen.getByRole("button", { name: "Назад в кассу" }));
  expect(routerCalls.back).toBe(1);
  expect(await screen.findByRole("heading", { name: "Все отделы" })).toBeInTheDocument();
});

it("opens POS from the single-button bottom bar: with history from home, by replace from a sub-screen", async () => {
  const user = userEvent.setup();
  render(<CashierPage />);
  const bar = within(await screen.findByRole("navigation", { name: "Панель кассы" }));
  expect(bar.getAllByRole("button").map((b) => b.textContent)).toEqual(["POS"]);
  expect(bar.getByRole("button", { name: "POS" })).not.toHaveAttribute("aria-current");
  // Панель — слот footer у AppShell (вне прокрутки и анимации контента), а не часть children.
  expect(within(screen.getByTestId("footer")).getByRole("navigation", { name: "Панель кассы" })).toBeInTheDocument();

  await user.click(bar.getByRole("button", { name: "POS" }));
  expect(routerCalls.push).toEqual(["/accounting?view=pos"]);
  expect(await screen.findByRole("heading", { name: "POS" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Оплата" })).toHaveAttribute("aria-current", "page");
  // Внутри POS — свои вкладки, панели кассы нет.
  expect(screen.queryByRole("navigation", { name: "Панель кассы" })).not.toBeInTheDocument();
  expect(
    within(screen.getByRole("navigation", { name: "Режим POS" }))
      .getAllByRole("button")
      .map((b) => b.textContent),
  ).toEqual(["Оплата", "Удаленно", "История"]);
  await user.click(screen.getByRole("button", { name: "Назад в кассу" }));
  expect(routerCalls.back).toBe(1);
  expect(await screen.findByRole("heading", { name: "Все отделы" })).toBeInTheDocument();

  // С подэкрана — заменой адреса: «‹» из POS ведёт на главную (историей, раз подэкран открыли с неё),
  // а не обратно на подэкран.
  await user.click(screen.getByRole("button", { name: /Долги клиентов/ }));
  expect(await screen.findByRole("heading", { name: "Долги клиентов" })).toBeInTheDocument();
  expect(screen.getByRole("navigation", { name: "Панель кассы" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "POS" }));
  expect(routerCalls.replace).toEqual(["/accounting?view=pos"]);
  expect(await screen.findByRole("heading", { name: "POS" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Назад в кассу" }));
  expect(routerCalls.back).toBe(2);
  expect(routerCalls.replace).toEqual(["/accounting?view=pos"]);
  expect(await screen.findByRole("heading", { name: "Все отделы" })).toBeInTheDocument();
});

it("replaces to the home screen from a deep-linked POS", async () => {
  const user = userEvent.setup();
  resetNavigation("/accounting?view=debts");
  render(<CashierPage />);
  await user.click(await screen.findByRole("button", { name: "POS" }));
  expect(routerCalls.replace).toEqual(["/accounting?view=pos"]);
  await user.click(await screen.findByRole("button", { name: "Назад в кассу" }));
  expect(routerCalls.back).toBe(0);
  expect(routerCalls.replace).toEqual(["/accounting?view=pos", "/accounting"]);
});

it("shows no POS bar without the right to take payments", async () => {
  mocks.me = { ...mocks.me, is_superuser: false, permissions: ["payments.confirm", "payments.view"] };
  render(<CashierPage />);
  expect(await screen.findByRole("heading", { name: "Все отделы" })).toBeInTheDocument();
  expect(screen.queryByRole("navigation", { name: "Панель кассы" })).not.toBeInTheDocument();
  const menu = within(screen.getByRole("navigation", { name: "Разделы кассы" }));
  expect(menu.queryByRole("button", { name: /POS/ })).not.toBeInTheDocument();
});

it("lets staff with access to every department switch the cashier's department", async () => {
  const user = userEvent.setup();
  render(<CashierPage />);
  await user.click(await screen.findByRole("button", { name: "Все отделы" }));
  const sheet = await screen.findByRole("dialog", { name: "Отдел" });
  expect(within(sheet).getByRole("button", { name: "Все отделы" })).toHaveAttribute("aria-pressed", "true");
  await user.click(within(sheet).getByRole("button", { name: "Мельница" }));

  expect(await screen.findByRole("heading", { name: "Мельница" })).toBeInTheDocument();
  expect(screen.queryByRole("dialog", { name: "Отдел" })).not.toBeInTheDocument();
  // Хранится отдельно для каждого пользователя.
  expect(localStorage.getItem("asyl_cashier_department:7")).toBe("main");
  expect(localStorage.getItem("asyl_cashier_department")).toBeNull();
  await waitFor(() =>
    expect(mocks.get.mock.calls.some(([url]) => url === "/clients/debts/?department=main")).toBe(true),
  );
  expect(
    mocks.get.mock.calls.some(
      ([url]) => url === `/reports/summary/?section=income&date_from=${todayIso}&date_to=${todayIso}&department=main`,
    ),
  ).toBe(true);
  // Итоги «Ждут оплаты» на главной считаются по выбранному отделу.
  await waitFor(() =>
    expect(mocks.get.mock.calls.some(([url]) => url === "/orders/awaiting-payment/?summary=1&department=main")).toBe(
      true,
    ),
  );

  // Подэкраны работают по тому же отделу: он виден над заголовком, а из шторки фильтров поле «Отдел» ушло.
  await user.click(screen.getByRole("button", { name: /Транзакции/ }));
  await waitFor(() =>
    expect(
      mocks.get.mock.calls.some(([url]) => String(url).match(/^\/payment-transactions\/\?.*department=main/)),
    ).toBe(true),
  );
  expect(screen.getByTestId("section")).toHaveTextContent("Мельница");
  await user.click(screen.getByRole("button", { name: "Назад в кассу" }));
  await user.click(await screen.findByRole("button", { name: /Долги клиентов/ }));
  await user.click(await screen.findByRole("button", { name: /Фильтры/ }));
  const filters = await screen.findByRole("dialog", { name: "Фильтры" });
  expect(within(filters).queryByLabelText("Отдел")).not.toBeInTheDocument();
  expect(within(filters).getByLabelText("Магазин")).toBeInTheDocument();
});

it("remembers the chosen department on the device and forgets a department that no longer exists", async () => {
  localStorage.setItem("asyl_cashier_department:7", "field");
  const { unmount } = render(<CashierPage />);
  expect(await screen.findByRole("heading", { name: "Нью-Сити" })).toBeInTheDocument();
  await waitFor(() =>
    expect(mocks.get.mock.calls.some(([url]) => url === "/clients/debts/?department=field")).toBe(true),
  );
  unmount();

  localStorage.setItem("asyl_cashier_department:7", "closed");
  render(<CashierPage />);
  expect(await screen.findByRole("heading", { name: "Все отделы" })).toBeInTheDocument();
  await waitFor(() => expect(localStorage.getItem("asyl_cashier_department:7")).toBe("all"));
});

it("locks a cashier to the department from the employee card", async () => {
  mocks.me = {
    ...mocks.me,
    is_superuser: false,
    permissions: ["payments.create", "payments.confirm", "payments.view", "reports.view"],
    first_name: "Асель",
    last_name: "Нурланова",
    sales_department: { id: 2, code: "field", name: "Нью-Сити", color: "#654321" },
  };
  localStorage.setItem("asyl_cashier_department:7", "main");
  render(<CashierPage />);
  expect(await screen.findByRole("heading", { name: "Нью-Сити" })).toBeInTheDocument();
  expect(screen.getByTestId("section")).toHaveTextContent("Асель Нурланова");
  expect(screen.queryByRole("button", { name: "Нью-Сити" })).not.toBeInTheDocument();
  // Касса закреплённого кассира работает по его отделу — что бы ни лежало в хранилище.
  await waitFor(() =>
    expect(mocks.get.mock.calls.some(([url]) => url === "/clients/debts/?department=field")).toBe(true),
  );
  expect(mocks.get.mock.calls.some(([url]) => String(url).includes("department=main"))).toBe(false);
});

it("shows a locked cashier the shared queue of every department, keeping the rest on their own", async () => {
  const user = userEvent.setup();
  mocks.awaitingCount = 2;
  mocks.me = {
    ...mocks.me,
    is_superuser: false,
    permissions: ["payments.confirm", "payments.view", "reports.view", "orders.view", "orders.confirm"],
    sales_department: { id: 2, code: "field", name: "Нью-Сити", color: "#654321" },
  };
  const baseGet = mocks.get.getMockImplementation()!;
  mocks.get.mockImplementation(async (raw: string) => {
    const url = new URL(raw, "http://localhost");
    if (url.pathname === "/orders/payments-queue/" && url.searchParams.get("summary") !== "1") {
      const own = { ...queueItem, id: 1, order: 11, department: "field", department_name: "Нью-Сити" };
      const foreign = { ...queueItem, id: 2, order: 12, department: "main", department_name: "Мельница" };
      return { data: { results: [own, foreign], count: 2, next: null } };
    }
    return baseGet(raw);
  });
  const urls = () => mocks.get.mock.calls.map(([url]) => String(url));
  render(<CashierPage />);
  const menu = within(await screen.findByRole("navigation", { name: "Разделы кассы" }));
  await waitFor(() => expect(menu.getByText("1 оплата на 100 ₸ · 2 ждут оплаты на 250 ₸")).toBeInTheDocument());
  // Оплаты к подтверждению на главной — по всем отделам; «Ждут оплаты», долги и отчёт — по своему.
  expect(urls()).toContain("/orders/payments-queue/?summary=1");
  expect(urls()).toContain("/orders/awaiting-payment/?summary=1&department=field");
  await waitFor(() => expect(urls()).toContain("/clients/debts/?department=field"));
  expect(urls()).toContain(
    `/reports/summary/?section=income&date_from=${todayIso}&date_to=${todayIso}&department=field`,
  );

  await user.click(menu.getByRole("button", { name: /^Оплаты/ }));
  expect(await screen.findByRole("heading", { name: "Оплаты" })).toBeInTheDocument();
  expect(screen.getByTestId("section")).toHaveTextContent("Все отделы");
  await waitFor(() => expect(urls()).toContain("/orders/payments-queue/?page=1&page_size=50"));
  expect(urls()).toContain("/orders/awaiting-payment/?department=field&page=1&page_size=50");
  expect(urls().filter((url) => url.startsWith("/orders/payments-queue/?") && url.includes("department="))).toEqual([]);
  // Заявки на заказ живут в «Заказах»: касса их больше не грузит.
  expect(urls().some((url) => url.startsWith("/orders/?"))).toBe(false);
  // Бейдж отдела показывает, чья оплата; карточку чужого заказа касса не открывает.
  expect(screen.getByRole("link", { name: "Заказ #11" })).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Заказ #12" })).not.toBeInTheDocument();
  expect(screen.getByText("Заказ #12")).toBeInTheDocument();
  expect(screen.getByText("Мельница")).toBeInTheDocument();
  await user.click(screen.getAllByRole("button", { name: "Подтвердить получение" })[1]);
  await waitFor(() => expect(mocks.post).toHaveBeenCalledWith("/orders/12/payments/2/confirm/"));
});

it("opens the only available section directly, without a home screen or a bar", async () => {
  mocks.me = { ...mocks.me, is_superuser: false, permissions: ["payments.view"] };
  render(<CashierPage />);
  expect(await screen.findByRole("heading", { name: "Транзакции" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Назад в кассу" })).not.toBeInTheDocument();
  expect(screen.queryByRole("navigation", { name: "Панель кассы" })).not.toBeInTheDocument();
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
  expect(await screen.findByRole("tab", { name: /Проверка/ })).toHaveAttribute("aria-selected", "true");
  await user.click(await screen.findByRole("button", { name: "Подтвердить получение" }));
  await waitFor(() => expect(mocks.post).toHaveBeenCalledWith("/orders/1/payments/1/confirm/"));

  // Отгруженный заказ без долга ждёт оплаты: касса принимает оплату или переводит его в долг.
  await user.click(screen.getByRole("tab", { name: /Ждут оплаты/ }));
  expect(await screen.findByRole("link", { name: "Заказ #21" })).toBeInTheDocument();
  expect(screen.getByText("Способ оплаты не выбран")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Принять оплату/ })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "В долг" }));
  const dialog = await screen.findByRole("dialog", { name: "Оставить заказ #21 в долг?" });
  await user.click(within(dialog).getByRole("button", { name: "В долг" }));
  await waitFor(() => expect(mocks.post).toHaveBeenCalledWith("/orders/21/to-debt/"));
});

it("takes a prepayment on the payments screen from «К отгрузке»", async () => {
  const user = userEvent.setup();
  resetNavigation("/accounting?view=confirm");
  render(<CashierPage />);
  await user.click(await screen.findByRole("tab", { name: "К отгрузке, 1" }));

  expect(await screen.findByRole("link", { name: "Заказ #31" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "В долг" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /Принять оплату/ }));
  await user.click(screen.getByRole("button", { name: "Принять" }));
  await waitFor(() =>
    expect(mocks.post).toHaveBeenCalledWith("/orders/31/payments/", {
      amount: "250",
      method: "cash",
    }),
  );
  const urls = mocks.get.mock.calls.map(([url]) => String(url));
  expect(urls).toContain("/orders/awaiting-shipment/?page=1&page_size=50");
  // «К возврату» — в кассе на компьютере: телефон её не грузит.
  expect(urls.some((url) => url.startsWith("/orders/to-refund/"))).toBe(false);
});

it("narrows the payments screen by quick filters: today and department", async () => {
  const user = userEvent.setup();
  resetNavigation("/accounting?view=confirm");
  const urls = () => mocks.get.mock.calls.map(([url]) => String(url));
  render(<CashierPage />);
  const quick = within(await screen.findByRole("group", { name: "Быстрые фильтры" }));
  expect(quick.getByRole("button", { name: "Все даты" })).toHaveAttribute("aria-pressed", "true");

  await user.click(quick.getByRole("button", { name: "Сегодня" }));
  await waitFor(() =>
    expect(urls()).toContain(`/orders/payments-queue/?date_from=${todayIso}&date_to=${todayIso}&page=1&page_size=50`),
  );
  expect(urls()).toContain(`/orders/awaiting-payment/?date_from=${todayIso}&date_to=${todayIso}&page=1&page_size=50`);

  await user.click(await quick.findByRole("button", { name: "Нью-Сити" }));
  await waitFor(() =>
    expect(urls()).toContain(
      `/orders/awaiting-payment/?date_from=${todayIso}&date_to=${todayIso}&department=field&page=1&page_size=50`,
    ),
  );
  expect(urls()).toContain(
    `/orders/payments-queue/?date_from=${todayIso}&date_to=${todayIso}&department=field&page=1&page_size=50`,
  );
  expect(quick.getByRole("button", { name: "Нью-Сити" })).toHaveAttribute("aria-pressed", "true");
});

it("hides department chips from a cashier locked to a department", async () => {
  resetNavigation("/accounting?view=confirm");
  mocks.me = {
    ...mocks.me,
    is_superuser: false,
    permissions: ["payments.confirm", "payments.view"],
    sales_department: { id: 2, code: "field", name: "Нью-Сити", color: "#654321" },
  };
  render(<CashierPage />);
  const quick = within(await screen.findByRole("group", { name: "Быстрые фильтры" }));
  expect(quick.getByRole("button", { name: "Сегодня" })).toBeInTheDocument();
  expect(quick.queryByRole("button", { name: "Мельница" })).not.toBeInTheDocument();
});

it("returns a mistakenly confirmed payment to review from the transaction sheet", async () => {
  const user = userEvent.setup();
  const baseGet = mocks.get.getMockImplementation()!;
  mocks.get.mockImplementation(async (raw: string) => {
    const response = await baseGet(raw);
    const url = new URL(raw, "http://localhost");
    if (url.pathname === "/payment-transactions/") {
      return { data: { ...response.data, results: [{ ...transaction, can_reopen: true }] } };
    }
    return response;
  });
  resetNavigation("/accounting?view=transactions");
  render(<CashierPage />);
  await user.click(await screen.findByRole("button", { name: /PAY-000005/ }));
  const sheet = await screen.findByRole("dialog", { name: "Оплачено" });
  await user.click(within(sheet).getByRole("button", { name: /Вернуть на проверку/ }));
  const dialog = await screen.findByRole("dialog", { name: "Вернуть PAY-000005 на проверку?" });
  await user.click(within(dialog).getByRole("button", { name: "Вернуть на проверку" }));
  await waitFor(() => expect(mocks.post).toHaveBeenCalledWith("/orders/1/payments/5/reopen/"));
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
  mocks.me = { ...mocks.me, is_superuser: false, permissions: ["payments.confirm", "payments.view"] };
  render(<CashierPage />);
  const headline = await screen.findByRole("button", { name: /Ожидает подтверждения/ });
  expect(headline).toHaveTextContent("100 ₸");
});
