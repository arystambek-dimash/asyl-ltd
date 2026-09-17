import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Suspense } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import ClientDebtPage from "./page";
import { resetNavigation } from "@/test-utils/next-navigation";

const mocks = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("next/navigation", () => import("@/test-utils/next-navigation"));
vi.mock("@/store/auth", () => ({
  useAuth: () => ({ me: { is_superuser: true, permissions: [] }, loading: false }),
}));
vi.mock("@/components/layout/app-shell", () => ({
  AppShell: ({ title, actions, children }: { title: string; actions?: React.ReactNode; children: React.ReactNode }) => (
    <main>
      <h1>{title}</h1>
      {actions}
      {children}
    </main>
  ),
}));
vi.mock("@/components/require-perm", () => ({
  RequirePerm: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock("@/lib/api", () => ({
  api: { get: (...args: unknown[]) => mocks.get(...args) },
  apiError: (error: unknown) => (error instanceof Error ? error.message : "Ошибка"),
  blobApiError: async () => "Ошибка",
  isCanceledRequest: () => false,
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
  total_amount: "195840",
  paid_total: "0",
  is_fully_paid: false,
  debt_override: false,
  created_at: "2026-07-28T19:50:00",
  shipped_at: "2026-07-28T19:50:00",
  truck_number: "",
  items: [{ id: 7, product: 1, product_label: "АТ 1с 50кг · Красный 50 кг", quantity: 100, price: "1958.4" }],
  payments: [],
  pending_payments: [],
};

const debtDetail = {
  client: { id: 1, name: "Клиент", phone: "", currency: "KZT" },
  debt_total: "195840",
  debt_currency: "KZT",
  debt_by_currency: { KZT: "195840" },
  orders_count: 1,
  unpaid_count: 1,
  partial_count: 0,
  orders: [debtOrder],
  stores: [],
};

function mockPhone() {
  // Тот же приём, что и в mobile-cashier.test.tsx: matchMedia матчит всегда,
  // useIsMobile() → true.
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    writable: true,
    value: () => ({ matches: true, media: "", addEventListener: () => {}, removeEventListener: () => {} }),
  });
}

beforeEach(() => {
  resetNavigation("/accounting/debts/clients/1");
  mocks.get.mockReset();
  mocks.get.mockImplementation(async (raw: string) => {
    const url = new URL(raw, "http://localhost");
    if (url.pathname === "/clients/1/debt-detail/") return { data: debtDetail };
    if (url.pathname === "/clients/1/history/") return { data: { payments: [] } };
    return { data: [] };
  });
});

afterEach(() => {
  // jsdom не знает matchMedia сам по себе — вернуть окружение к «нет
  // matchMedia», чтобы соседний тест не унаследовал мобильный режим.
  // @ts-expect-error тестовая подчистка глобала
  delete window.matchMedia;
});

// Страница — клиентский компонент с use(params): без Suspense-обёртки React
// подвешивает рендер и тело документа остаётся пустым.
async function renderPage() {
  const params = Promise.resolve({ id: "1" });
  await act(async () => {
    render(
      <Suspense>
        <ClientDebtPage params={params} />
      </Suspense>,
    );
    await params;
  });
}

it("renders debt orders as cards on phones with payment actions only inside details", async () => {
  mockPhone();
  const user = userEvent.setup();
  await renderPage();

  expect(await screen.findByText("#130")).toBeInTheDocument();
  expect(screen.getByText("Остаток")).toBeInTheDocument();
  expect(screen.queryByRole("columnheader", { name: "Отгружен" })).not.toBeInTheDocument();
  // Свёрнутая карточка: ни «Оплатить», ни «Открыть», ни «Внести оплату».
  expect(screen.queryByRole("button", { name: "Оплатить" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Внести оплату/ })).not.toBeInTheDocument();
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
    if (url.pathname === "/clients/1/history/") return { data: { payments: [] } };
    return { data: [] };
  });
  await renderPage();

  expect(await screen.findByRole("link", { name: /\+7 700 123 45 67/ })).toHaveAttribute("href", "tel:+77001234567");
  expect(screen.getByText("Текущий долг")).toBeInTheDocument();
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
    if (url.pathname === "/clients/1/history/") return { data: { payments: [] } };
    return { data: [] };
  });
  await renderPage();

  await user.click(await screen.findByRole("cell", { name: "#130" }));
  expect(screen.getByRole("button", { name: /Принять оплату/ })).toBeDisabled();
  expect(screen.getByRole("button", { name: /Отправить удалённый счёт/ })).toBeDisabled();
});
