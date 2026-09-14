import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, beforeEach, expect, it, vi } from "vitest";
import CashierPage from "@/app/accounting/page";
import type { TopbarBack } from "@/components/layout/topbar";
import { resetNavigation, routerCalls } from "@/test-utils/next-navigation";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  me: { is_superuser: true, permissions: [] as string[] },
  poll: null as null | (() => Promise<unknown>),
  paymentStatus: "requested",
}));
vi.mock("next/navigation", () => import("@/test-utils/next-navigation"));
vi.mock("@/lib/use-visible-polling", () => ({
  useVisiblePolling: (poll: () => Promise<unknown>, intervalMs: number, active: boolean) => {
    if (active && intervalMs === 3_000) mocks.poll = poll;
  },
}));
vi.mock("@/store/auth", () => ({ useAuth: () => ({ me: mocks.me, loading: false }) }));
vi.mock("@/components/layout/app-shell", () => ({
  AppShell: ({
    title,
    back,
    children,
    footer,
  }: {
    title: string;
    back?: TopbarBack;
    children: React.ReactNode;
    footer?: React.ReactNode;
  }) => (
    <div>
      <h1>{title}</h1>
      {back && (
        <button type="button" onClick={back.onClick}>
          {back.label}
        </button>
      )}
      {children}
      <div data-testid="footer">{footer}</div>
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
vi.mock("next/image", () => ({
  // eslint-disable-next-line @next/next/no-img-element
  default: ({ src, alt }: { src: string; alt: string }) => <img src={src} alt={alt} />,
}));

const debtors = [
  {
    client_id: 1,
    client_name: "Асан Бекмуратов",
    client_phone: "87011234567",
    debt_total: "195840",
    debt_currency: "KZT",
    debt_by_currency: { KZT: "195840" },
    orders_count: 2,
    unpaid_count: 2,
    partial_count: 0,
    stores_count: 0,
    overdue_count: 0,
  },
  {
    client_id: 2,
    client_name: "Мерей Ахметова",
    client_phone: "87779998877",
    debt_total: "5000",
    debt_currency: "KZT",
    debt_by_currency: { KZT: "5000" },
    orders_count: 1,
    unpaid_count: 1,
    partial_count: 0,
    stores_count: 0,
    overdue_count: 0,
  },
];

const debtOrder = (id: number, currency: string, total: string, department = "main") => ({
  id,
  client: 1,
  currency,
  status: "shipped",
  department,
  department_name: department === "main" ? "Мельница" : "Нью-Сити",
  total_amount: total,
  paid_total: "0",
  remaining_amount: total,
  pending_payments: [],
  items: [],
  truck_number: "",
});

const detail = {
  client: { id: 1, name: "Асан Бекмуратов", phone: "87011234567", currency: "KZT" },
  debt_total: "195840",
  orders_count: 2,
  unpaid_count: 2,
  partial_count: 0,
  stores: [],
  orders: [debtOrder(130, "KZT", "195840"), debtOrder(131, "USD", "500", "field")],
};

function qrPayment(status: string) {
  return {
    id: 501,
    order: 130,
    amount: "195840.00",
    currency: "KZT",
    method: "kaspi",
    status,
    paid_at: "2026-09-12T10:00:00",
    recorded_by: 1,
    provider: {
      invoice_id: 77,
      channel: "qr",
      status: status === "confirmed" ? "paid" : "pending",
      phone_number: null,
      qr_token_url: "https://qr.kaspi.kz/pos",
      qr_image_url: "https://api.apipay.kz/qr/pos.png",
      qr_expires_at: "2026-09-12T10:05:00",
      total_refunded: "0.00",
      available_for_refund: "0.00",
      refunds: [],
    },
  };
}

beforeAll(() => {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    writable: true,
    value: () => ({ matches: true, media: "", addEventListener: () => {}, removeEventListener: () => {} }),
  });
});

beforeEach(() => {
  resetNavigation("/accounting?view=pos");
  localStorage.clear();
  mocks.me = { is_superuser: true, permissions: [] };
  mocks.poll = null;
  mocks.paymentStatus = "requested";
  mocks.get.mockReset();
  mocks.post.mockReset();
  mocks.post.mockResolvedValue({ data: qrPayment("requested") });
  mocks.get.mockImplementation(async (raw: string) => {
    const url = new URL(raw, "http://localhost");
    if (url.pathname === "/departments/")
      return {
        data: [
          {
            id: 1,
            code: "main",
            name: "Мельница",
            color: "#123456",
            is_active: true,
            is_default: true,
            order_count: 0,
          },
          {
            id: 2,
            code: "field",
            name: "Нью-Сити",
            color: "#654321",
            is_active: true,
            is_default: false,
            order_count: 0,
          },
        ],
      };
    if (url.pathname === "/clients/debts/") return { data: debtors };
    if (url.pathname === "/clients/1/debt-detail/") return { data: detail };
    if (url.pathname === "/orders/130/payments/501/") return { data: qrPayment(mocks.paymentStatus) };
    if (url.pathname === "/payment-transactions/")
      return {
        data: {
          results: [],
          page: 1,
          pages: 1,
          count: 0,
          status_counts: {},
          summary: {
            paid_by_currency: { KZT: "0", USD: "0" },
            refunded_by_currency: { KZT: "0", USD: "0" },
            paid_by_method: {},
          },
        },
      };
    return { data: [] };
  });
});

async function pickFirstOrder(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: /Асан Бекмуратов/ }));
  await user.click(await screen.findByRole("button", { name: /Заказ #130/ }));
}

it("takes a debt payment by Kaspi QR", async () => {
  const user = userEvent.setup();
  render(<CashierPage />);

  await user.type(await screen.findByPlaceholderText("Поиск клиента: имя или телефон"), "Асан");
  expect(screen.queryByText("Мерей Ахметова")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /Асан Бекмуратов/ }));
  expect(await screen.findByRole("button", { name: /Заказ #131/ })).toBeDisabled();
  expect(screen.getByText("QR только в тенге")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /Заказ #130/ }));

  expect(screen.getByText("195 840 ₸")).toBeInTheDocument();
  for (let i = 0; i < 3; i += 1) await user.click(screen.getByRole("button", { name: "Стереть" }));
  expect(screen.getByText("195 ₸")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Цифра 0" }));
  expect(screen.getByText("1 950 ₸")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Весь остаток" }));
  await user.click(screen.getByRole("button", { name: /Показать QR/ }));

  expect(mocks.post).toHaveBeenCalledWith("/orders/130/payments/", {
    method: "kaspi",
    channel: "qr",
    amount: "195840",
  });
  expect(await screen.findByRole("img", { name: "Kaspi QR для оплаты" })).toHaveAttribute(
    "src",
    "https://api.apipay.kz/qr/pos.png",
  );
  expect(screen.getByText("Ожидаем оплату…")).toBeInTheDocument();

  mocks.paymentStatus = "confirmed";
  await act(async () => {
    await mocks.poll?.();
  });
  expect(await screen.findByRole("heading", { name: "Оплачено" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Новая оплата" }));
  expect(await screen.findByPlaceholderText("Поиск клиента: имя или телефон")).toBeInTheDocument();
});

it("keeps the amount when the QR cannot be created", async () => {
  const user = userEvent.setup();
  mocks.post.mockRejectedValue(new Error("Платёжный сервис недоступен"));
  render(<CashierPage />);
  await pickFirstOrder(user);

  await user.click(screen.getByRole("button", { name: /Показать QR/ }));

  expect(await screen.findByRole("alert")).toHaveTextContent("Платёжный сервис недоступен");
  expect(screen.getByText("195 840 ₸")).toBeInTheDocument();
});

it("sends a Kaspi invoice to the client's phone", async () => {
  const user = userEvent.setup();
  const invoice = qrPayment("requested");
  mocks.post.mockResolvedValue({
    data: {
      ...invoice,
      method: "invoice",
      provider: {
        ...invoice.provider,
        channel: "phone",
        phone_number: "87011234567",
        qr_image_url: null,
        qr_token_url: null,
      },
    },
  });
  render(<CashierPage />);

  // Вкладки внутри POS живут в состоянии — адрес не меняется.
  await user.click(await screen.findByRole("button", { name: "Удаленно" }));
  expect(routerCalls.replace).toEqual([]);
  expect(await screen.findByRole("heading", { name: "Удаленная оплата" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Удаленно" })).toHaveAttribute("aria-current", "page");
  await pickFirstOrder(user);
  await user.click(screen.getByRole("button", { name: "Далее" }));
  expect(screen.getByLabelText("Телефон покупателя")).toHaveValue("87011234567");
  await user.click(screen.getByRole("button", { name: /Отправить счёт/ }));

  expect(mocks.post).toHaveBeenCalledWith("/orders/130/payments/", {
    method: "invoice",
    amount: "195840",
    phone_number: "87011234567",
  });
  expect(await screen.findByText(/Счёт отправлен на 87011234567/)).toBeInTheDocument();
});

it("steps back inside POS and closes it from the first step", async () => {
  const user = userEvent.setup();
  render(<CashierPage />);
  await user.click(await screen.findByRole("button", { name: /Асан Бекмуратов/ }));
  await screen.findByRole("button", { name: /Заказ #130/ });

  await user.click(screen.getByRole("button", { name: "Назад" }));
  expect(await screen.findByPlaceholderText("Поиск клиента: имя или телефон")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Назад в кассу" }));
  expect(routerCalls.replace).toEqual(["/accounting"]);
});

it("keeps the payment in progress while the cashier looks at the history inside POS", async () => {
  const user = userEvent.setup();
  render(<CashierPage />);
  await pickFirstOrder(user);
  await user.click(screen.getByRole("button", { name: /Показать QR/ }));
  await screen.findByRole("img", { name: "Kaspi QR для оплаты" });

  await user.click(screen.getByRole("button", { name: "История" }));
  expect(await screen.findByRole("heading", { name: "История" })).toBeInTheDocument();
  expect(await screen.findByText("Транзакций пока нет.")).toBeInTheDocument();
  // «‹» из истории возвращает к начатой оплате.
  await user.click(screen.getByRole("button", { name: "Назад" }));

  expect(await screen.findByRole("heading", { name: "POS" })).toBeInTheDocument();
  expect(screen.getByRole("img", { name: "Kaspi QR для оплаты" })).toBeInTheDocument();
  expect(routerCalls.replace).toEqual([]);
});

it("opens the remote tab by deep link and keeps the client when the cashier leaves and comes back", async () => {
  const user = userEvent.setup();
  resetNavigation("/accounting?view=remote");
  render(<CashierPage />);
  expect(await screen.findByRole("heading", { name: "Удаленная оплата" })).toBeInTheDocument();
  await user.click(await screen.findByRole("button", { name: /Асан Бекмуратов/ }));
  await screen.findByRole("button", { name: /Заказ #130/ });

  // Домой и обратно через панель — начатая оплата на месте, на своей вкладке.
  resetNavigation("/accounting");
  await user.click(await screen.findByRole("button", { name: "POS" }));
  expect(routerCalls.push).toEqual(["/accounting?view=pos"]);
  expect(await screen.findByRole("heading", { name: "Удаленная оплата" })).toBeInTheDocument();
  expect(await screen.findByRole("button", { name: /Заказ #130/ })).toBeInTheDocument();
});

it("keeps the tab the cashier chose inside POS when a payment is started over", async () => {
  const user = userEvent.setup();
  render(<CashierPage />);
  await user.click(await screen.findByRole("button", { name: "Удаленно" }));
  await pickFirstOrder(user);
  // Назад к списку клиентов: адрес по-прежнему ?view=pos, но вкладка остаётся «Удаленно».
  await user.click(screen.getByRole("button", { name: "Назад" }));
  await user.click(await screen.findByRole("button", { name: "Назад" }));
  expect(await screen.findByPlaceholderText("Поиск клиента: имя или телефон")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Удаленная оплата" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Удаленно" })).toHaveAttribute("aria-current", "page");
});

it("returns from history to the payment tab even before a client is picked", async () => {
  const user = userEvent.setup();
  render(<CashierPage />);
  await user.click(await screen.findByRole("button", { name: "История" }));
  expect(await screen.findByRole("heading", { name: "История" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Назад в кассу" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Назад" }));
  expect(await screen.findByRole("heading", { name: "POS" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Оплата" })).toHaveAttribute("aria-current", "page");
});

it("hides the history tab inside POS from a cashier who cannot view payments", async () => {
  mocks.me = { ...mocks.me, is_superuser: false, permissions: ["payments.create"] };
  render(<CashierPage />);
  expect(await screen.findByRole("heading", { name: "POS" })).toBeInTheDocument();
  // Вкладки POS — тоже слот footer, не children.
  expect(
    within(within(screen.getByTestId("footer")).getByRole("navigation", { name: "Режим POS" }))
      .getAllByRole("button")
      .map((b) => b.textContent),
  ).toEqual(["Оплата", "Удаленно"]);
});

it("keeps the cashier on the step while the QR is being created", async () => {
  const user = userEvent.setup();
  let resolve: (value: unknown) => void = () => {};
  mocks.post.mockReturnValue(new Promise((r) => (resolve = r)));
  render(<CashierPage />);
  await pickFirstOrder(user);

  await user.click(screen.getByRole("button", { name: /Показать QR/ }));
  await user.click(screen.getByRole("button", { name: "Назад" }));
  // Панель внизу ждёт ответа сервера вместе с кассиром.
  expect(screen.getByRole("button", { name: "Удаленно" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Удаленно" }));
  // Ключевая проверка: клавиатура суммы всё ещё на экране — заказ не сброшен на список.
  expect(screen.getByRole("button", { name: "Стереть" })).toBeInTheDocument();
  expect(screen.getByText("195 840 ₸")).toBeInTheDocument();
  expect(mocks.post).toHaveBeenCalledTimes(1);
  expect(routerCalls.replace).toEqual([]);

  await act(async () => resolve({ data: qrPayment("requested") }));
  expect(await screen.findByRole("img", { name: "Kaspi QR для оплаты" })).toBeInTheDocument();
});

it("tells the cashier when the payment status cannot be checked", async () => {
  const user = userEvent.setup();
  render(<CashierPage />);
  await pickFirstOrder(user);
  await user.click(screen.getByRole("button", { name: /Показать QR/ }));
  await screen.findByRole("img", { name: "Kaspi QR для оплаты" });

  const baseGet = mocks.get.getMockImplementation()!;
  mocks.get.mockImplementation(async (raw: string) => {
    if (raw.startsWith("/orders/130/payments/501/")) throw new Error("Нет сети");
    return baseGet(raw);
  });
  await act(async () => {
    await mocks.poll?.();
  });
  expect(screen.getByRole("alert")).toHaveTextContent("Не удаётся проверить статус: Нет сети");

  mocks.get.mockImplementation(baseGet);
  mocks.paymentStatus = "confirmed";
  await act(async () => {
    await mocks.poll?.();
  });
  expect(await screen.findByRole("heading", { name: "Оплачено" })).toBeInTheDocument();
});

it("never shows «Оплачено» when the server did not issue a QR", async () => {
  const user = userEvent.setup();
  mocks.post.mockResolvedValue({ data: { ...qrPayment("confirmed"), provider: null } });
  render(<CashierPage />);
  await pickFirstOrder(user);
  const debtorLoads = () => mocks.get.mock.calls.filter(([url]) => String(url).startsWith("/clients/debts/")).length;
  const before = debtorLoads();

  await user.click(screen.getByRole("button", { name: /Показать QR/ }));

  expect(await screen.findByRole("alert")).toHaveTextContent("Сервер не выдал Kaspi QR");
  expect(screen.queryByRole("heading", { name: "Оплачено" })).not.toBeInTheDocument();
  expect(debtorLoads()).toBe(before);
});

it("explains a reservation left by a failed QR attempt", async () => {
  const user = userEvent.setup();
  let reserved = false;
  const baseGet = mocks.get.getMockImplementation()!;
  mocks.get.mockImplementation(async (raw: string) => {
    const url = new URL(raw, "http://localhost");
    if (url.pathname === "/clients/1/debt-detail/" && reserved) {
      return {
        data: {
          ...detail,
          orders: [{ ...detail.orders[0], pending_payments: [{ id: 700, amount: "195840.00" }] }, detail.orders[1]],
        },
      };
    }
    return baseGet(raw);
  });
  mocks.post.mockImplementation(async () => {
    reserved = true;
    throw new Error("Статус создаваемого счёта ещё уточняется");
  });
  render(<CashierPage />);
  await pickFirstOrder(user);

  await user.click(screen.getByRole("button", { name: /Показать QR/ }));

  expect(await screen.findByText("Всё уже ожидает подтверждения")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Показать QR/ })).not.toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent("Статус создаваемого счёта ещё уточняется");
  await user.click(screen.getByRole("button", { name: "Открыть «Историю»" }));
  expect(await screen.findByRole("heading", { name: "История" })).toBeInTheDocument();
});

it("opens POS from the bottom bar only for staff who can take payments", async () => {
  const user = userEvent.setup();
  resetNavigation("/accounting");
  const { unmount } = render(<CashierPage />);
  await user.click(await screen.findByRole("button", { name: "POS" }));
  // С главной — с историей: аппаратный «назад» вернёт домой.
  expect(routerCalls.push).toEqual(["/accounting?view=pos"]);
  expect(await screen.findByRole("heading", { name: "POS" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Оплата" })).toHaveAttribute("aria-current", "page");
  unmount();

  resetNavigation("/accounting");
  mocks.me = { ...mocks.me, is_superuser: false, permissions: ["payments.confirm", "payments.view"] };
  render(<CashierPage />);
  expect(await screen.findByRole("heading", { name: "Все отделы" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "POS" })).not.toBeInTheDocument();
  expect(screen.queryByRole("navigation", { name: "Панель кассы" })).not.toBeInTheDocument();
});

it("offers only the orders of the department chosen in the cashier header", async () => {
  const user = userEvent.setup();
  localStorage.setItem("asyl_cashier_department", "field");
  render(<CashierPage />);
  await waitFor(() =>
    expect(mocks.get.mock.calls.some(([url]) => url === "/clients/debts/?department=field")).toBe(true),
  );
  await user.click(await screen.findByRole("button", { name: /Асан Бекмуратов/ }));

  expect(await screen.findByRole("button", { name: /Заказ #131/ })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Заказ #130/ })).not.toBeInTheDocument();
});
