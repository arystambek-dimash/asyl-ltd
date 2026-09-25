import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { makePayment, makeQrRefund } from "@/test-utils/factories";
import { currentUrl, resetNavigation } from "@/test-utils/next-navigation";
import { renderRoutePage } from "@/test-utils/route-page";
import OrderDetailPage from "./page";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  delete: vi.fn(),
  order: {} as Record<string, unknown>,
  me: { is_superuser: false, permissions: [] as string[] },
}));

vi.mock("next/navigation", () => import("@/test-utils/next-navigation"));
vi.mock("@/store/auth", () => ({ useAuth: () => ({ me: mocks.me, loading: false }) }));
vi.mock("@/components/layout/app-shell", () => import("@/test-utils/app-shell"));
vi.mock("@/components/require-perm", () => import("@/test-utils/require-perm"));
vi.mock("@/lib/toast", () => ({ showSuccess: vi.fn() }));
vi.mock("@/lib/api", () => ({
  api: {
    get: (...args: unknown[]) => mocks.get(...args),
    post: (...args: unknown[]) => mocks.post(...args),
    delete: (...args: unknown[]) => mocks.delete(...args),
  },
  apiError: (error: unknown) => (error instanceof Error ? error.message : "Ошибка"),
  isCanceledRequest: () => false,
}));

const confirmedPayment = makePayment({ id: 91, order: 40, refunded_amount: "0.00" });

/** Подтверждённый, ещё не отгруженный заказ: сервер открыл предоплату деньгами у кассы. */
const confirmedOrder = {
  id: 40,
  client: 3,
  client_name: "Покупатель",
  currency: "KZT",
  status: "confirmed",
  payment_status: "unpaid",
  department: "main",
  transport_type: "truck",
  truck_number: "",
  items: [{ id: 1, product: 2, product_label: "Мука", quantity: 4, unit_price: "25000", price: "25000" }],
  total_amount: "100000",
  paid_total: "0",
  remaining_amount: "100000",
  payment_open: true,
  payment_open_methods: ["cash", "kaspi", "remote"],
  payment_request_open: false,
  overpaid_amount: "0.00",
  is_fully_paid: false,
  payments: [],
  pending_payments: [],
  created_at: "2026-09-20T09:00:00Z",
};

beforeEach(() => {
  resetNavigation("/orders/40");
  mocks.me = { is_superuser: false, permissions: ["orders.view", "payments.create", "payments.confirm"] };
  mocks.order = confirmedOrder;
  mocks.get.mockReset();
  mocks.post.mockReset();
  mocks.delete.mockReset();
  mocks.delete.mockResolvedValue({ data: null });
  mocks.post.mockResolvedValue({ data: { method: "cash" } });
  mocks.get.mockImplementation(async (raw: string) => {
    const url = new URL(raw, "http://localhost");
    if (url.pathname === "/orders/40/") return { data: mocks.order };
    return { data: [] };
  });
});

async function renderPage() {
  await renderRoutePage(OrderDetailPage, "40");
  await screen.findByRole("heading", { name: "Заказ #40" });
}

it("takes a prepayment before shipment only with money at the till", async () => {
  const user = userEvent.setup();
  await renderPage();
  await user.click(screen.getByRole("tab", { name: /Оплата/ }));

  expect(screen.getByRole("button", { name: /Принять оплату/ })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Отправить удалённый счёт/ })).not.toBeInTheDocument();
  // До отгрузки долга нет — бейдж «Не оплачен» в шапке не нужен.
  const heading = screen.getByRole("heading", { name: "Заказ #40" });
  expect(within(heading.parentElement!).queryByText("Не оплачен")).not.toBeInTheDocument();
});

it("shows the badge of a prepaid order before shipment", async () => {
  mocks.order = { ...confirmedOrder, payment_status: "settled", paid_total: "100000", remaining_amount: "0" };
  await renderPage();
  const heading = screen.getByRole("heading", { name: "Заказ #40" });
  expect(within(heading.parentElement!).getByText("Оплачен")).toBeInTheDocument();
});

it("links a shipped order to the client debt only when the server calls it a debt", async () => {
  const user = userEvent.setup();
  mocks.me = { is_superuser: false, permissions: ["orders.view", "reports.view"] };
  mocks.order = { ...confirmedOrder, status: "shipped", payment_open: false, is_debt: true };
  await renderPage();
  await user.click(screen.getByRole("tab", { name: /Оплата/ }));
  expect(screen.getByRole("link", { name: /Открыть долг клиента/ })).toHaveAttribute(
    "href",
    "/accounting/debts/clients/3",
  );
});

it("hides the debt link while the order is not a debt", async () => {
  const user = userEvent.setup();
  mocks.me = { is_superuser: false, permissions: ["orders.view", "reports.view"] };
  mocks.order = { ...confirmedOrder, is_debt: false };
  await renderPage();
  await user.click(screen.getByRole("tab", { name: /Оплата/ }));
  expect(screen.queryByRole("link", { name: /Открыть долг клиента/ })).not.toBeInTheDocument();
});

it("shows one payment badge in the header and the «Оплата» card", async () => {
  const user = userEvent.setup();
  await renderPage();
  await user.click(screen.getByRole("tab", { name: /Оплата/ }));
  // Карточка «Оплата» следует тем же правилам, что шапка: до отгрузки «Не оплачен» не показываем.
  expect(screen.queryByText("Не оплачен")).not.toBeInTheDocument();
});

it("marks a payment under review in the header and the «Оплата» card", async () => {
  const user = userEvent.setup();
  mocks.order = { ...confirmedOrder, pending_payments: [{ ...confirmedPayment, status: "received" }] };
  await renderPage();
  await user.click(screen.getByRole("tab", { name: /Оплата/ }));
  expect(screen.getAllByText("На проверке")).toHaveLength(2);
});

it("offers to return an overpayment to the client", async () => {
  const user = userEvent.setup();
  mocks.order = {
    ...confirmedOrder,
    total_amount: "70000",
    paid_total: "100000",
    remaining_amount: "0",
    overpaid_amount: "30000.00",
    payment_status: "settled",
    payments: [confirmedPayment],
  };
  await renderPage();

  expect(screen.getByText(/— вернуть клиенту/)).toHaveTextContent("Переплата 30 000 ₸ — вернуть клиенту");
  await user.click(screen.getByRole("button", { name: /Вернуть переплату/ }));
  const dialog = await screen.findByRole("dialog", { name: "Вернуть оплату" });
  expect(within(dialog).getByLabelText("Сумма возврата")).toHaveValue(30000);
});

it("keeps the Kaspi QR refund window open after the overpayment is taken off the order", async () => {
  const user = userEvent.setup();
  const qrPayment = { ...confirmedPayment, method: "kaspi", provider: { channel: "qr" } };
  const overpaid = {
    ...confirmedOrder,
    total_amount: "70000",
    paid_total: "100000",
    remaining_amount: "0",
    overpaid_amount: "30000.00",
    payment_status: "settled",
    payments: [qrPayment],
  };
  const qrRefund = makeQrRefund({
    id: 5,
    amount: "30000.00",
    customer_url: "https://pay.example/refund/5",
    link_expires_at: null,
    created_at: "2026-09-23T10:00:00Z",
  });
  mocks.order = overpaid;
  const baseGet = mocks.get.getMockImplementation()!;
  mocks.get.mockImplementation(async (raw: string) =>
    raw === "/payment-transactions/91/qr-refund/" ? { data: qrRefund } : baseGet(raw),
  );
  mocks.post.mockImplementation(async () => {
    // Начатый возврат сразу резервирует деньги: переплаты на заказе больше нет.
    mocks.order = { ...overpaid, overpaid_amount: "0.00" };
    return { data: { method: "apipay_qr", qr_refund: qrRefund } };
  });
  await renderPage();

  await user.click(screen.getByRole("button", { name: /Вернуть переплату/ }));
  const dialog = await screen.findByRole("dialog", { name: "Вернуть оплату" });
  await user.type(within(dialog).getByLabelText("Причина"), "Уменьшили заказ");
  await user.click(within(dialog).getByRole("button", { name: "Показать QR" }));

  expect(await screen.findByRole("dialog", { name: "Возврат по QR" })).toBeInTheDocument();
  await waitFor(() => expect(screen.queryByText(/— вернуть клиенту/)).not.toBeInTheDocument());
  expect(screen.getByRole("dialog", { name: "Возврат по QR" })).toBeInTheDocument();
});

it("reopens the receive dialog when the prepayment from the order form failed", async () => {
  resetNavigation("/orders/40?pay=kaspi&amount=5000&back=%2Forders");
  await renderPage();

  const dialog = await screen.findByRole("dialog", { name: "Принять оплату" });
  expect(within(dialog).getByLabelText("Сумма")).toHaveValue(5000);
  expect(within(dialog).getByRole("button", { name: "QR" })).toHaveAttribute("aria-pressed", "true");
  expect(within(dialog).getByRole("alert")).toHaveTextContent("оплата не прошла");
  // Признак повтора уходит из адреса — обновление страницы окно не откроет.
  await waitFor(() => expect(currentUrl()).toBe("/orders/40?back=%2Forders"));
});

it("explains that an unconfirmed order cannot take the payment yet", async () => {
  resetNavigation("/orders/40?pay=cash&amount=5000");
  mocks.order = {
    ...confirmedOrder,
    status: "pending",
    payment_open: false,
    payment_open_methods: [],
  };
  await renderPage();

  expect(await screen.findByText(/ещё не подтверждён — оплату примите после подтверждения/)).toBeInTheDocument();
  expect(screen.queryByRole("dialog", { name: "Принять оплату" })).not.toBeInTheDocument();
  await waitFor(() => expect(currentUrl()).toBe("/orders/40"));
});

it("asks to check the order instead of reopening the dialog when the payment answer was lost", async () => {
  resetNavigation("/orders/40?pay=cash&amount=5000&pay_check=1");
  mocks.order = { ...confirmedOrder, paid_total: "5000", remaining_amount: "95000", payment_status: "partial" };
  await renderPage();

  expect(await screen.findByText(/ответ об оплате не пришёл/)).toHaveTextContent("5 000 ₸");
  expect(screen.queryByRole("dialog", { name: "Принять оплату" })).not.toBeInTheDocument();
  await waitFor(() => expect(currentUrl()).toBe("/orders/40"));
});

it("opens the confirmation window without the page notice about the postponed payment", async () => {
  const user = userEvent.setup();
  resetNavigation("/orders/40?pay=cash&amount=5000");
  mocks.me = { is_superuser: false, permissions: ["orders.view", "orders.confirm", "payments.create"] };
  mocks.order = {
    ...confirmedOrder,
    status: "pending",
    client_department: "main",
    payment_open: false,
    payment_open_methods: [],
  };
  const baseGet = mocks.get.getMockImplementation()!;
  mocks.get.mockImplementation(async (raw: string) => {
    const url = new URL(raw, "http://localhost");
    if (url.pathname === "/departments/") return { data: [{ code: "main", name: "Мельница", is_active: true }] };
    if (url.pathname === "/orders/40/confirm-context/")
      return { data: { items: {}, transport_locked: false, client_country: "" } };
    return baseGet(raw);
  });
  await renderPage();
  const notice = /ещё не подтверждён — оплату примите после подтверждения/;
  expect(await screen.findByText(notice)).toBeInTheDocument();

  // Окно подтверждения показывает только свои ошибки: пояснение об оплате — не отказ подтверждения.
  await user.click(screen.getByRole("button", { name: "Проверить и подтвердить" }));
  const dialog = await screen.findByRole("dialog", { name: "Подтвердить заказ #40" });
  expect(within(dialog).queryByText(notice)).not.toBeInTheDocument();
});

it("asks how many bags were loaded before shipping the order from its card", async () => {
  const user = userEvent.setup();
  mocks.me = { is_superuser: false, permissions: ["orders.view", "orders.edit"] };
  await renderPage();

  await user.click(screen.getByText("Изменить статус"));
  await user.selectOptions(screen.getByRole("combobox", { name: "Статус заказа" }), "shipped");

  // Как в списке: без окна подсчёта сервер списал бы склад по числу мешков из заказа.
  const dialog = await screen.findByRole("dialog", { name: "Сколько мешков отгружено?" });
  expect(mocks.post).not.toHaveBeenCalled();
  await user.clear(within(dialog).getByRole("spinbutton"));
  await user.type(within(dialog).getByRole("spinbutton"), "3");
  await user.click(within(dialog).getByRole("button", { name: /Завершить · 3 меш\./ }));
  await waitFor(() =>
    expect(mocks.post).toHaveBeenCalledWith("/orders/40/set-status/", { status: "shipped", bags_loaded: 3 }),
  );
});

it("asks before cancelling the order from its card", async () => {
  const user = userEvent.setup();
  mocks.me = { is_superuser: false, permissions: ["orders.view", "orders.edit"] };
  await renderPage();

  await user.click(screen.getByText("Изменить статус"));
  await user.selectOptions(screen.getByRole("combobox", { name: "Статус заказа" }), "cancelled");

  expect(await screen.findByRole("dialog", { name: "Отменить заказ?" })).toBeInTheDocument();
  expect(mocks.post).not.toHaveBeenCalled();
});

it("sends a status request without the bag count for staff without orders.edit", async () => {
  const user = userEvent.setup();
  mocks.post.mockResolvedValue({ data: { applied: false } });
  await renderPage();

  await user.click(screen.getByText("Изменить статус"));
  await user.selectOptions(screen.getByRole("combobox", { name: "Статус заказа" }), "cancelled");

  // Запрос на одобрение мешков не несёт — окно подсчёта ему не нужно.
  await waitFor(() => expect(mocks.post).toHaveBeenCalledWith("/orders/40/set-status/", { status: "cancelled" }));
  expect(await screen.findByText("Запрос на смену статуса отправлен на одобрение.")).toBeInTheDocument();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("opens the confirmation window when the list asked for it", async () => {
  resetNavigation("/orders/40?confirm=1&back=%2Forders");
  mocks.me = { is_superuser: false, permissions: ["orders.view", "orders.confirm"] };
  mocks.order = { ...confirmedOrder, status: "pending", payment_open: false, payment_open_methods: [] };
  await renderPage();

  expect(await screen.findByRole("dialog", { name: "Подтвердить заказ #40" })).toBeInTheDocument();
  // Признак уходит из адреса — обновление страницы окно заново не откроет.
  await waitFor(() => expect(currentUrl()).toBe("/orders/40?back=%2Forders"));
});

it("archives the order from its card and returns to the list", async () => {
  const user = userEvent.setup();
  mocks.me = { is_superuser: false, permissions: ["orders.view", "orders.edit"] };
  await renderPage();

  await user.click(screen.getByRole("button", { name: "Действия" }));
  await user.click(screen.getByRole("menuitem", { name: /В архив/ }));
  const dialog = await screen.findByRole("dialog", { name: "Переместить заказ в архив?" });
  await user.click(within(dialog).getByRole("button", { name: "В архив" }));

  await waitFor(() => expect(mocks.delete).toHaveBeenCalledWith("/orders/40/"));
  await waitFor(() => expect(currentUrl()).toBe("/orders"));
});

it("does not archive the order from its card while the truck is loading", async () => {
  const user = userEvent.setup();
  mocks.me = { is_superuser: false, permissions: ["orders.view", "orders.edit"] };
  mocks.order = { ...confirmedOrder, status: "loading" };
  await renderPage();

  // Как в списке: сначала погрузку завершают или возвращают.
  await user.click(screen.getByRole("button", { name: "Действия" }));
  expect(screen.getByRole("menuitem", { name: /В архив/ })).toBeDisabled();
  expect(screen.getByText("Сначала завершите или верните текущую погрузку")).toBeInTheDocument();
});
