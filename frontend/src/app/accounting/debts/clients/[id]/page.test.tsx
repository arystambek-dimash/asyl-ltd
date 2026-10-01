import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ComponentProps } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ClientDebtPaymentModal as RealModal } from "@/components/payments/client-debt-payment-modal";
import { renderRoutePage } from "@/test-utils/route-page";
import ClientDebtPage from "./page";
import { stubPhoneMatchMedia } from "@/test-utils/cashier";
import { resetNavigation } from "@/test-utils/next-navigation";
import { formatCurrency, formatDateTime } from "@/lib/utils";

const SUPERUSER = { is_superuser: true, permissions: [] as string[] };
const mocks = vi.hoisted(() => ({ get: vi.fn(), me: { is_superuser: true, permissions: [] as string[] } }));

vi.mock("next/navigation", () => import("@/test-utils/next-navigation"));
vi.mock("@/store/auth", () => ({ useAuth: () => ({ me: mocks.me, loading: false }) }));
vi.mock("@/components/layout/app-shell", () => import("@/test-utils/app-shell"));
vi.mock("@/components/require-perm", () => import("@/test-utils/require-perm"));
vi.mock("@/lib/api", () => ({
  api: { get: (...args: unknown[]) => mocks.get(...args) },
  apiError: (error: unknown) => (error instanceof Error ? error.message : "Ошибка"),
  blobApiError: async () => "Ошибка",
  isCanceledRequest: () => false,
}));
// Окно внесения проверено своими тестами; здесь — с чем страница его открывает и как применяет ответ.
vi.mock("@/components/payments/client-debt-payment-modal", () => ({
  ClientDebtPaymentModal: ({ clientId, currencies, open, onClose, onPaid }: ComponentProps<typeof RealModal>) =>
    open ? (
      <div role="dialog" aria-label="Внести оплату">
        <span>{`клиент ${clientId} · ${currencies.join(", ")}`}</span>
        <button type="button" onClick={() => onPaid("Внесено 195 840 ₸ на 1 заказ")}>
          Подтвердить
        </button>
        <button type="button" onClick={onClose}>
          Закрыть
        </button>
      </div>
    ) : null,
}));

// Заказ #130 в долге: одна позиция, без частичной оплаты — минимум полей,
// которых хватает и типу Order, и странице.
const debtOrder = {
  id: 130,
  client: 1,
  department: "main",
  department_name: "Мельница",
  currency: "KZT",
  status: "shipped",
  payment_status: "unpaid",
  payment_open: true,
  payment_open_methods: ["cash", "kaspi", "remote", "invoice"],
  payment_request_open: true,
  total_amount: "195840",
  paid_total: "0",
  remaining_amount: "195840",
  is_fully_paid: false,
  created_at: "2026-07-28T19:50:00",
  shipped_at: "2026-07-28T19:50:00",
  truck_number: "",
  items: [{ id: 7, product: 1, product_label: "АТ 1с 50кг · Красный 50 кг", quantity: 100, unit_price: "1958.4" }],
  payments: [],
  pending_payments: [],
};

const debtDetail = {
  client: { id: 1, name: "Клиент", phone: "", currency: "KZT" },
  debt_total: "195840",
  debt_currency: "KZT",
  debt_by_currency: { KZT: "195840" },
  overdue_total: "0.00",
  overdue_by_currency: {},
  orders: [debtOrder],
  stores: [],
};

// Сводка карточки клиента: «за всё время» считается по финансовым заказам.
const clientHistory = {
  client: { id: 1, name: "Клиент", phone: "", country: "", currency: "KZT" },
  summary: {
    currency: "KZT",
    revenue: "300000",
    paid: "104160",
    debt: "195840",
    orders_count: 2,
    by_currency: { KZT: { revenue: "300000", paid: "104160", debt: "195840" } },
  },
  sales: [],
  payments: [],
  debts: [],
};

beforeEach(() => {
  mocks.me = SUPERUSER;
  resetNavigation("/accounting/debts/clients/1");
  mocks.get.mockReset();
  mocks.get.mockImplementation(async (raw: string) => {
    const url = new URL(raw, "http://localhost");
    if (url.pathname === "/clients/1/debt-detail/") return { data: debtDetail };
    if (url.pathname === "/clients/1/history/") return { data: clientHistory };
    return { data: [] };
  });
});

afterEach(() => {
  // jsdom не знает matchMedia сам по себе — вернуть окружение к «нет
  // matchMedia», чтобы соседний тест не унаследовал мобильный режим.
  // @ts-expect-error тестовая подчистка глобала
  delete window.matchMedia;
});

const renderPage = () => renderRoutePage(ClientDebtPage, "1");

it("renders debt orders as cards on phones with payment actions only inside details", async () => {
  stubPhoneMatchMedia();
  const user = userEvent.setup();
  await renderPage();

  expect(await screen.findByText("#130")).toBeInTheDocument();
  expect(screen.getByText("Остаток")).toBeInTheDocument();
  expect(screen.queryByRole("columnheader", { name: "Отгружен" })).not.toBeInTheDocument();
  // Свёрнутая карточка: ни «Оплатить», ни «Открыть», ни «Внести оплату».
  // Внесение по клиенту — в шапке: на телефоне видно сразу, без раскрытия карточек.
  const card = within(screen.getByRole("listitem"));
  expect(screen.queryByRole("button", { name: "Оплатить" })).not.toBeInTheDocument();
  expect(card.queryByRole("button", { name: /Внести оплату/ })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Внести оплату" })).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: /Открыть/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Принять оплату/ })).not.toBeInTheDocument();

  const toggle = screen.getByRole("button", { name: /^#130/ });
  expect(toggle).toHaveTextContent("Открыть детали");
  await user.click(toggle);
  expect(toggle).toHaveAttribute("aria-expanded", "true");
  expect(toggle).toHaveTextContent("Скрыть детали");
  expect(await screen.findByText("АТ 1с 50кг · Красный 50 кг")).toBeInTheDocument();
  expect(screen.getByText("100 × 1 958,4 ₸")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Принять оплату/ })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Отправить удалённый счёт/ })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Запросить оплату/ })).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: /Карточка заказа/ })).toHaveAttribute(
    "href",
    "/orders/130?back=%2Faccounting%2Fdebts%2Fclients%2F1",
  );

  expect(screen.getByRole("link", { name: /К долгам/ })).toHaveAttribute("href", "/accounting?view=debts");
});

it("keeps the table on desktop and expands a row by clicking it", async () => {
  const user = userEvent.setup();
  await renderPage();
  expect(await screen.findByRole("columnheader", { name: "Отгружен" })).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: /Открыть/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Оплатить" })).not.toBeInTheDocument();

  await user.click(screen.getByRole("cell", { name: "#130" }));
  expect(screen.getByRole("button", { name: "Скрыть детали заказа #130" })).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByRole("button", { name: /Принять оплату/ })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /Карточка заказа/ })).toHaveAttribute(
    "href",
    "/orders/130?back=%2Faccounting%2Fdebts%2Fclients%2F1",
  );
});

it("shows the client with phone above the debt summary", async () => {
  mocks.get.mockImplementation(async (raw: string) => {
    const url = new URL(raw, "http://localhost");
    if (url.pathname === "/clients/1/debt-detail/")
      return { data: { ...debtDetail, client: { ...debtDetail.client, phone: "+7 700 123 45 67" } } };
    if (url.pathname === "/clients/1/history/") return { data: clientHistory };
    return { data: [] };
  });
  await renderPage();

  expect(await screen.findByRole("link", { name: /\+7 700 123 45 67/ })).toHaveAttribute("href", "tel:+77001234567");
  expect(screen.getByText("Текущий долг")).toBeInTheDocument();
});

it("takes lifetime totals from the client card summary and links to the card", async () => {
  await renderPage();

  const paid = (await screen.findByText("Оплачено за всё время")).nextElementSibling;
  expect(paid).toHaveAttribute("title", formatCurrency("104160", "KZT"));
  const revenue = screen.getByText("Сумма продаж за всё время").nextElementSibling;
  expect(revenue).toHaveAttribute("title", formatCurrency("300000", "KZT"));
  expect(screen.getByRole("link", { name: /Карточка клиента/ })).toHaveAttribute("href", "/clients/1");
});

it("disables payment for an order whose store is outside its payment window", async () => {
  const user = userEvent.setup();
  mocks.get.mockImplementation(async (raw: string) => {
    const url = new URL(raw, "http://localhost");
    if (url.pathname === "/clients/1/debt-detail/")
      return {
        data: {
          ...debtDetail,
          orders: [{ ...debtOrder, store: 5 }],
          stores: [{ id: 5, name: "Береке", payment_schedule_type: "weekly", payment_days: [1], window_open: false }],
        },
      };
    if (url.pathname === "/clients/1/history/") return { data: clientHistory };
    return { data: [] };
  });
  await renderPage();

  await user.click(await screen.findByRole("cell", { name: "#130" }));
  expect(screen.getByRole("button", { name: /Принять оплату/ })).toBeDisabled();
  expect(screen.getByRole("button", { name: /Отправить удалённый счёт/ })).toBeDisabled();
});

it("shows write-offs net of refunds on the confirmation day", async () => {
  const user = userEvent.setup();
  const refundedPayment = {
    id: 41,
    order: 130,
    currency: "KZT",
    amount: "100000",
    refunded_amount: "30000",
    method: "cash",
    method_label: "Наличные",
    status: "confirmed",
    paid_at: "2026-07-20T10:00:00",
    confirmed_at: "2026-07-22T12:30:00",
  };
  mocks.get.mockImplementation(async (raw: string) => {
    const url = new URL(raw, "http://localhost");
    if (url.pathname === "/clients/1/debt-detail/")
      return {
        data: {
          ...debtDetail,
          orders: [{ ...debtOrder, paid_total: "70000", remaining_amount: "125840", payments: [refundedPayment] }],
        },
      };
    if (url.pathname === "/clients/1/history/") return { data: clientHistory };
    return { data: [] };
  });
  await renderPage();

  await user.click(await screen.findByRole("cell", { name: "#130" }));
  await user.click(screen.getByRole("tab", { name: "Списание" }));
  // Строка сходится с «Уже оплачено» (нетто 70 000), а не с суммой приёма.
  expect(screen.getByText("+70 000 ₸")).toBeInTheDocument();
  expect(screen.getByText("из 100 000 ₸, возврат 30 000 ₸")).toBeInTheDocument();
  expect(screen.getByText(formatDateTime("2026-07-22T12:30:00"))).toBeInTheDocument();
  expect(screen.queryByText(formatDateTime("2026-07-20T10:00:00"))).not.toBeInTheDocument();
});

/** Ответы страницы с другим долгом клиента; история — как обычно. */
function serveDebtDetail(detail: object) {
  mocks.get.mockImplementation(async (raw: string) => {
    const url = new URL(raw, "http://localhost");
    if (url.pathname === "/clients/1/debt-detail/") return { data: detail };
    if (url.pathname === "/clients/1/history/") return { data: clientHistory };
    return { data: [] };
  });
}

/** Сколько раз страница запросила долг клиента: `refresh()` перечитывает его. */
const debtDetailLoads = () => mocks.get.mock.calls.filter(([url]) => url === "/clients/1/debt-detail/").length;

it("hides «Внести оплату» without payments.create", async () => {
  mocks.me = { is_superuser: false, permissions: ["reports.view"] };
  await renderPage();

  expect(await screen.findByText("Текущий долг")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Внести оплату" })).not.toBeInTheDocument();
});

it("hides «Внести оплату» when the client owes nothing", async () => {
  serveDebtDetail({ ...debtDetail, debt_total: "0.00", debt_by_currency: {}, orders: [] });
  await renderPage();

  expect(await screen.findByText("Долгов нет.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Внести оплату" })).not.toBeInTheDocument();
});

it("opens the client debt payment with the client and its debt currencies, main one first", async () => {
  mocks.me = { is_superuser: false, permissions: ["payments.create"] };
  serveDebtDetail({ ...debtDetail, debt_by_currency: { USD: "500.00", KZT: "195840" } });
  const user = userEvent.setup();
  await renderPage();

  expect(screen.queryByRole("dialog", { name: "Внести оплату" })).not.toBeInTheDocument();
  await user.click(await screen.findByRole("button", { name: "Внести оплату" }));
  const dialog = screen.getByRole("dialog", { name: "Внести оплату" });
  expect(within(dialog).getByText("клиент 1 · KZT, USD")).toBeInTheDocument();

  await user.click(within(dialog).getByRole("button", { name: "Закрыть" }));
  expect(screen.queryByRole("dialog", { name: "Внести оплату" })).not.toBeInTheDocument();
});

it("after a client debt payment closes the window, shows the notice and reloads the debt", async () => {
  const user = userEvent.setup();
  await renderPage();

  await user.click(await screen.findByRole("button", { name: "Внести оплату" }));
  const loadsBefore = debtDetailLoads();
  await user.click(
    within(screen.getByRole("dialog", { name: "Внести оплату" })).getByRole("button", { name: "Подтвердить" }),
  );

  expect(screen.getByText("Внесено 195 840 ₸ на 1 заказ")).toHaveAttribute("role", "status");
  expect(screen.queryByRole("dialog", { name: "Внести оплату" })).not.toBeInTheDocument();
  await waitFor(() => expect(debtDetailLoads()).toBe(loadsBefore + 1));
});
