import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { formatCurrency, formatDateTime, todayLocalIsoDate } from "@/lib/utils";
import { makeMe } from "@/test-utils/factories";
import ShopOrdersPage from "./page";

const mocks = vi.hoisted(() => ({
  useApi: vi.fn(),
  usePagedApi: vi.fn(),
  post: vi.fn(),
  reload: vi.fn(),
  me: { value: null as unknown },
}));
vi.mock("@/lib/use-api", () => ({ useApi: mocks.useApi }));
vi.mock("@/lib/use-paged-api", () => ({ usePagedApi: mocks.usePagedApi }));
vi.mock("@/lib/api", () => ({ api: { post: mocks.post }, apiError: (error: Error) => error.message }));
vi.mock("@/store/auth", () => ({ useAuth: () => ({ me: mocks.me.value, loading: false }) }));
vi.mock("@/components/layout/app-shell", () => import("@/test-utils/app-shell"));
vi.mock("next/link", () => import("@/test-utils/next-link"));

const summary = { name: "Магазин Иван", warehouse_name: "Мельница", currency: "KZT", client: 7 };
const products = [
  {
    id: 1,
    name: "DIKHAN первый сорт",
    label: "DIKHAN первый сорт · 50 кг",
    color_label: "Красный",
    weight_kg: "50.00",
    price: "5000.00",
    currency: "KZT",
    stock_bags: 20,
  },
];
const order = {
  id: 41,
  created_at: "2026-10-16T08:00:00Z",
  sold_at: "2026-10-15T08:00:00Z",
  stock_deducted: true,
  paid_total: "0.00",
  remaining: "10000.00",
  payment: null,
  status: "completed",
  status_label: "Завершён",
  currency: "KZT",
  client_name: null,
  total_amount: "10000.00",
  items: [{ id: 1, product_label: products[0].label, quantity: 2 }],
};
const client = { id: 9, name: "Покупатель Иван", company_name: "", phone: "", currency: "KZT" };
function apiState(data: unknown) {
  return { data, loading: false, error: "", errorStatus: null, reload: mocks.reload };
}
async function fillSale(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Новый заказ" }));
  const dialog = within(screen.getByRole("dialog"));
  await user.click(dialog.getByRole("button", { name: "Товар, позиция 1" }));
  await user.click(dialog.getByRole("option"));
  await user.type(dialog.getByRole("spinbutton", { name: "Количество мешков, позиция 1" }), "2");
  return dialog;
}

describe("Заказы магазина", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.me.value = makeMe({
      permissions: ["orders.create"],
      shop: { name: summary.name, warehouse_name: summary.warehouse_name },
    });
    mocks.useApi.mockImplementation((url: string | null) =>
      apiState(
        url === "/shop-orders/summary/"
          ? summary
          : url === "/shop-orders/catalog/"
            ? products
            : url === "/orders/form-options/"
              ? { clients: [client] }
              : null,
      ),
    );
    mocks.usePagedApi.mockReturnValue({
      items: [order],
      count: 1,
      hasMore: false,
      loading: false,
      loadingMore: false,
      error: "",
      reload: mocks.reload,
      loadMore: vi.fn(),
    });
    mocks.post.mockResolvedValue({ data: order });
  });

  it("показывает отдельную историю, остаток и пополнение через обычные заказы", () => {
    render(<ShopOrdersPage />);
    expect(screen.getByText("20 мешков")).toBeInTheDocument();
    expect(screen.getByText("Розничный покупатель")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Перейти к обычным заказам" })).toHaveAttribute("href", "/orders");
    expect(screen.getByText(/после подтверждения грузчиком/)).toBeInTheDocument();
    expect(mocks.usePagedApi).toHaveBeenCalledWith("/shop-orders/", 20);
  });

  it("продаёт из магазина без клиента отдельным API", async () => {
    const user = userEvent.setup();
    render(<ShopOrdersPage />);
    const dialog = await fillSale(user);
    await user.click(dialog.getByRole("button", { name: "Создать заказ" }));
    await waitFor(() =>
      expect(mocks.post).toHaveBeenCalledWith("/shop-orders/", {
        idempotency_key: expect.any(String),
        client: null,
        items: [{ product: 1, quantity: 2, unit_price: "5000.00" }],
      }),
    );
    expect(mocks.reload).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it.each([0, -3])("продаёт при остатке %s с вручную введённой ценой без доставки", async (stock) => {
    mocks.useApi.mockImplementation((url: string | null) =>
      apiState(
        url === "/shop-orders/summary/"
          ? summary
          : url === "/shop-orders/catalog/"
            ? [{ ...products[0], stock_bags: stock, price: null }]
            : url === "/orders/form-options/"
              ? { clients: [client] }
              : null,
      ),
    );
    const user = userEvent.setup();
    render(<ShopOrdersPage />);
    await user.click(screen.getByRole("button", { name: "Новый заказ" }));
    const dialog = within(screen.getByRole("dialog"));
    expect(dialog.getByRole("heading", { name: "Новый заказ" })).toBeInTheDocument();
    expect(dialog.getByRole("heading", { name: "Состав заказа" })).toBeInTheDocument();
    expect(dialog.getByText("Итог")).toBeInTheDocument();
    for (const label of ["Доставка", "Транспорт", "Склад отгрузки", "Тягач", "Плановая дата прибытия", "Валюта"]) {
      expect(dialog.queryByText(label)).not.toBeInTheDocument();
    }
    await user.click(dialog.getByRole("button", { name: "Товар, позиция 1" }));
    expect(dialog.getByRole("option")).toHaveTextContent(`${stock} меш.`);
    await user.click(dialog.getByRole("option"));
    await user.type(dialog.getByRole("spinbutton", { name: "Количество мешков, позиция 1" }), "2");
    const submit = dialog.getByRole("button", { name: "Создать заказ" });
    expect(submit).toBeDisabled();
    await user.type(dialog.getByRole("spinbutton", { name: "Цена, позиция 1" }), "1250.25");
    expect(submit).toBeEnabled();
    await user.click(submit);
    await waitFor(() =>
      expect(mocks.post).toHaveBeenCalledWith("/shop-orders/", {
        idempotency_key: expect.any(String),
        client: null,
        items: [{ product: 1, quantity: 2, unit_price: "1250.25" }],
      }),
    );
  });

  it("сохраняет UUID при повторе после сетевой ошибки и меняет после правки", async () => {
    const user = userEvent.setup();
    mocks.post.mockRejectedValue(new Error("Сеть недоступна"));
    render(<ShopOrdersPage />);
    const dialog = await fillSale(user);
    await user.click(dialog.getByRole("button", { name: "Создать заказ" }));
    await screen.findByRole("alert");
    const firstKey = mocks.post.mock.calls[0][1].idempotency_key;
    await user.click(dialog.getByRole("button", { name: "Создать заказ" }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(2));
    expect(mocks.post.mock.calls[1][1].idempotency_key).toBe(firstKey);
    await user.clear(dialog.getByRole("spinbutton", { name: "Количество мешков, позиция 1" }));
    await user.type(dialog.getByRole("spinbutton", { name: "Количество мешков, позиция 1" }), "3");
    await user.click(dialog.getByRole("button", { name: "Создать заказ" }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(3));
    expect(mocks.post.mock.calls[2][1].idempotency_key).not.toBe(firstKey);
    const quantityKey = mocks.post.mock.calls[2][1].idempotency_key;
    await user.clear(dialog.getByRole("spinbutton", { name: "Цена, позиция 1" }));
    await user.type(dialog.getByRole("spinbutton", { name: "Цена, позиция 1" }), "7000");
    await user.click(dialog.getByRole("button", { name: "Создать заказ" }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(4));
    expect(mocks.post.mock.calls[3][1].idempotency_key).not.toBe(quantityKey);
    expect(mocks.post.mock.calls[3][1].items).toEqual([{ product: 1, quantity: 3, unit_price: "7000.00" }]);
  });

  it("позволяет выбрать клиента и отменить только продажу магазина", async () => {
    const user = userEvent.setup();
    render(<ShopOrdersPage />);
    const dialog = await fillSale(user);
    await user.click(dialog.getByRole("button", { name: "Выбрать клиента" }));
    await user.click(dialog.getByRole("button", { name: /Покупатель Иван/ }));
    await user.click(dialog.getByRole("button", { name: "Создать заказ" }));
    await waitFor(() => expect(mocks.post.mock.calls[0][1].client).toBe(9));
    await user.click(screen.getByRole("button", { name: "Отменить заказ №41" }));
    const cancel = within(screen.getByRole("dialog"));
    expect(cancel.getByText(/вернётся только на остаток магазина/)).toBeInTheDocument();
    await user.click(cancel.getByRole("button", { name: "Отменить заказ" }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledWith("/shop-orders/41/cancel/"));
  });

  it.each([
    [[], false, false],
    [["orders.edit"], true, false],
    [["payments.create"], false, true],
  ])("дополнения доступны по собственным правам %j", async (permissions, backdate, payment) => {
    mocks.me.value = makeMe({
      permissions: ["orders.create", ...permissions],
      shop: { name: summary.name, warehouse_name: summary.warehouse_name },
    });
    const user = userEvent.setup();
    render(<ShopOrdersPage />);
    const dialog = await fillSale(user);
    expect(Boolean(dialog.queryByRole("heading", { name: "Дополнительно" }))).toBe(backdate || payment);
    expect(Boolean(dialog.queryByRole("checkbox", { name: "Задним числом" }))).toBe(backdate);
    expect(Boolean(dialog.queryByRole("checkbox", { name: "Оплата сразу" }))).toBe(payment);
  });

  it("принимает оплату в API магазина, повторяет тот же UUID и меняет при правке оплаты", async () => {
    mocks.me.value = makeMe({
      permissions: ["orders.create", "payments.create"],
      shop: { name: summary.name, warehouse_name: summary.warehouse_name },
    });
    mocks.post.mockRejectedValue(new Error("Нет сети"));
    const user = userEvent.setup();
    render(<ShopOrdersPage />);
    const dialog = await fillSale(user);
    await user.click(dialog.getByRole("checkbox", { name: "Оплата сразу" }));
    expect(dialog.getByLabelText("Сумма оплаты")).toHaveValue(10000);
    await user.clear(dialog.getByLabelText("Сумма оплаты"));
    await user.type(dialog.getByLabelText("Сумма оплаты"), "1500.25");
    await user.click(dialog.getByRole("button", { name: "QR" }));
    await user.click(dialog.getByRole("button", { name: "Создать и принять оплату" }));
    await screen.findByRole("alert");
    expect(mocks.post.mock.calls[0][0]).toBe("/shop-orders/");
    expect(mocks.post.mock.calls[0][1]).toMatchObject({ payment: { amount: "1500.25", method: "kaspi" } });
    expect(mocks.post.mock.calls[0][1]).not.toHaveProperty("backdate");
    const key = mocks.post.mock.calls[0][1].idempotency_key;
    await user.click(dialog.getByRole("button", { name: "Создать и принять оплату" }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(2));
    expect(mocks.post.mock.calls[1][1].idempotency_key).toBe(key);
    await user.clear(dialog.getByLabelText("Сумма оплаты"));
    await user.type(dialog.getByLabelText("Сумма оплаты"), "2000");
    await user.click(dialog.getByRole("button", { name: "Создать и принять оплату" }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(3));
    expect(mocks.post.mock.calls[2][1].idempotency_key).not.toBe(key);
    const amountKey = mocks.post.mock.calls[2][1].idempotency_key;
    await user.click(dialog.getByRole("button", { name: "Наличные" }));
    await user.click(dialog.getByRole("button", { name: "Создать и принять оплату" }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(4));
    expect(mocks.post.mock.calls[3][1].idempotency_key).not.toBe(amountKey);
    expect(mocks.post.mock.calls[3][1].payment).toEqual({ amount: "2000.00", method: "cash" });
    expect(mocks.post.mock.calls.every(([url]) => url === "/shop-orders/")).toBe(true);
  });

  it("для USD оставляет наличные и перечисление без QR и удалённой оплаты", async () => {
    mocks.me.value = makeMe({
      permissions: ["orders.create", "payments.create", "orders.edit"],
      shop: { name: summary.name, warehouse_name: summary.warehouse_name },
    });
    mocks.useApi.mockImplementation((url: string | null) =>
      apiState(
        url === "/shop-orders/summary/"
          ? { ...summary, currency: "USD" }
          : url === "/shop-orders/catalog/"
            ? products
            : url === "/orders/form-options/"
              ? { clients: [client] }
              : null,
      ),
    );
    const user = userEvent.setup();
    render(<ShopOrdersPage />);
    const dialog = await fillSale(user);
    await user.click(dialog.getByRole("checkbox", { name: "Оплата сразу" }));
    expect(dialog.queryByRole("button", { name: "QR" })).not.toBeInTheDocument();
    expect(dialog.queryByRole("button", { name: /Удалённая оплата/ })).not.toBeInTheDocument();
    expect(dialog.getByRole("button", { name: /1С перечисление/ })).toBeInTheDocument();
    await user.click(dialog.getByRole("checkbox", { name: "Задним числом" }));
    await user.click(dialog.getByRole("checkbox", { name: /Оплачен полностью/ }));
    expect(dialog.queryByRole("button", { name: "QR" })).not.toBeInTheDocument();
    expect(dialog.getByRole("button", { name: "Наличные" })).toBeInTheDocument();
  });

  it("проверяет сумму оплаты и сбрасывает дополнения после создания", async () => {
    mocks.me.value = makeMe({
      permissions: ["orders.create", "payments.create"],
      shop: { name: summary.name, warehouse_name: summary.warehouse_name },
    });
    const user = userEvent.setup();
    render(<ShopOrdersPage />);
    const dialog = await fillSale(user);
    await user.click(dialog.getByRole("checkbox", { name: "Оплата сразу" }));
    const amount = dialog.getByLabelText("Сумма оплаты");
    await user.clear(amount);
    await user.type(amount, "10001");
    expect(dialog.getByRole("button", { name: "Создать и принять оплату" })).toBeDisabled();
    await user.clear(amount);
    await user.type(amount, "1000");
    await user.click(dialog.getByRole("button", { name: "Создать и принять оплату" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "Новый заказ" }));
    expect(within(screen.getByRole("dialog")).getByRole("checkbox", { name: "Оплата сразу" })).not.toBeChecked();
  });

  it("оформляет завершённую продажу задним числом с отдельной полной оплатой без доставки", async () => {
    mocks.me.value = makeMe({
      permissions: ["orders.create", "orders.edit", "payments.create"],
      shop: { name: summary.name, warehouse_name: summary.warehouse_name },
    });
    const user = userEvent.setup();
    render(<ShopOrdersPage />);
    const dialog = await fillSale(user);
    await user.click(dialog.getByRole("checkbox", { name: "Оплата сразу" }));
    await user.click(dialog.getByRole("checkbox", { name: "Задним числом" }));
    expect(dialog.queryByRole("checkbox", { name: "Оплата сразу" })).not.toBeInTheDocument();
    expect(dialog.getByText("Завершён")).toBeInTheDocument();
    expect(dialog.queryByText("Ожидает загрузки")).not.toBeInTheDocument();
    expect(dialog.queryByText("Доставка")).not.toBeInTheDocument();
    expect(dialog.getByText(/Остаток магазина при этом не списывается/)).toBeInTheDocument();
    expect(dialog.getByLabelText("Дата")).toHaveAttribute("max", todayLocalIsoDate());
    fireEvent.change(dialog.getByLabelText("Дата"), { target: { value: "2026-10-01" } });
    await user.click(dialog.getByRole("checkbox", { name: /Оплачен полностью/ }));
    await user.click(dialog.getByRole("button", { name: /1С перечисление/ }));
    await user.click(dialog.getByRole("button", { name: "Создать задним числом" }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
    expect(mocks.post.mock.calls[0][1].backdate).toEqual({ date: "2026-10-01", paid: true, payment_method: "bank" });
    expect(mocks.post.mock.calls[0][1]).not.toHaveProperty("payment");
    await user.click(screen.getByRole("button", { name: "Новый заказ" }));
    const fresh = within(screen.getByRole("dialog"));
    expect(fresh.getByRole("checkbox", { name: "Задним числом" })).not.toBeChecked();
    expect(fresh.getByRole("checkbox", { name: "Оплата сразу" })).not.toBeChecked();
  });

  it("не принимает будущую дату, сохраняет UUID повтора и обновляет после смены даты", async () => {
    mocks.me.value = makeMe({
      permissions: ["orders.create", "orders.edit"],
      shop: { name: summary.name, warehouse_name: summary.warehouse_name },
    });
    mocks.post.mockRejectedValue(new Error("Нет сети"));
    const user = userEvent.setup();
    render(<ShopOrdersPage />);
    const dialog = await fillSale(user);
    await user.click(dialog.getByRole("checkbox", { name: "Задним числом" }));
    expect(dialog.queryByRole("checkbox", { name: /Оплачен полностью/ })).not.toBeInTheDocument();
    const date = dialog.getByLabelText("Дата");
    fireEvent.change(date, { target: { value: "2999-01-01" } });
    expect(dialog.getByText("Дата не может быть в будущем.")).toBeInTheDocument();
    expect(dialog.getByRole("button", { name: "Создать задним числом" })).toBeDisabled();
    fireEvent.change(date, { target: { value: "2026-10-01" } });
    await user.click(dialog.getByRole("button", { name: "Создать задним числом" }));
    await screen.findByRole("alert");
    const key = mocks.post.mock.calls[0][1].idempotency_key;
    expect(mocks.post.mock.calls[0][1].backdate.paid).toBe(false);
    await user.click(dialog.getByRole("button", { name: "Создать задним числом" }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(2));
    expect(mocks.post.mock.calls[1][1].idempotency_key).toBe(key);
    fireEvent.change(date, { target: { value: "2026-10-02" } });
    await user.click(dialog.getByRole("button", { name: "Создать задним числом" }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(3));
    expect(mocks.post.mock.calls[2][1].idempotency_key).not.toBe(key);
  });

  it("показывает дату продажи и серверные оплачено/остаток и предупреждает об отмене оплаты без движения склада", async () => {
    mocks.me.value = makeMe({
      permissions: ["orders.create", "payments.create"],
      shop: { name: summary.name, warehouse_name: summary.warehouse_name },
    });
    mocks.usePagedApi.mockReturnValue({
      items: [
        {
          ...order,
          stock_deducted: false,
          paid_total: "1234.00",
          remaining: "8766.00",
          payment: { amount: "1234.00", method: "cash", paid_at: order.sold_at, voided_at: null },
        },
      ],
      count: 1,
      hasMore: false,
      loading: false,
      loadingMore: false,
      error: "",
      reload: mocks.reload,
      loadMore: vi.fn(),
    });
    const user = userEvent.setup();
    render(<ShopOrdersPage />);
    expect(screen.getByText(formatDateTime(order.sold_at))).toBeInTheDocument();
    expect(screen.getByText(`Оплачено: ${formatCurrency("1234.00", "KZT")}`.replace(/\s/g, " "))).toBeInTheDocument();
    expect(screen.getByText(`Остаток: ${formatCurrency("8766.00", "KZT")}`.replace(/\s/g, " "))).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Отменить заказ №41" }));
    expect(
      within(screen.getByRole("dialog")).getByText(/остаток магазина не изменится.*Оплата магазина будет отменена/),
    ).toBeInTheDocument();
  });

  it("показывает отменённую оплату и не предлагает повторную отмену", () => {
    mocks.usePagedApi.mockReturnValue({
      items: [
        {
          ...order,
          status: "cancelled",
          status_label: "Отменён",
          paid_total: "0.00",
          remaining: "0.00",
          payment: { amount: "10000.00", method: "kaspi", paid_at: order.sold_at, voided_at: order.created_at },
        },
      ],
      count: 1,
      hasMore: false,
      loading: false,
      loadingMore: false,
      error: "",
      reload: mocks.reload,
      loadMore: vi.fn(),
    });
    render(<ShopOrdersPage />);
    expect(screen.getByText("QR · Оплата отменена")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Отменить заказ №41" })).not.toBeInTheDocument();
  });

  it("скрывает отмену принятой оплаты без payments.create", () => {
    mocks.usePagedApi.mockReturnValue({
      items: [{ ...order, paid_total: "10000.00", remaining: "0.00" }],
      count: 1,
      hasMore: false,
      loading: false,
      loadingMore: false,
      error: "",
      reload: mocks.reload,
      loadMore: vi.fn(),
    });
    render(<ShopOrdersPage />);
    expect(screen.queryByRole("button", { name: "Отменить заказ №41" })).not.toBeInTheDocument();
  });

  it("без магазина не запрашивает магазин и не открывает продажи", () => {
    mocks.me.value = makeMe({ permissions: ["orders.create"] });
    render(<ShopOrdersPage />);
    expect(screen.getByText("У вас нет своего магазина")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Новый заказ" })).not.toBeInTheDocument();
    expect(mocks.useApi).not.toHaveBeenCalledWith("/shop-orders/summary/");
  });

  it("без права создания не запрашивает магазин", () => {
    mocks.me.value = makeMe({ shop: { name: summary.name, warehouse_name: summary.warehouse_name } });
    render(<ShopOrdersPage />);
    expect(screen.getByText("Нет доступа")).toBeInTheDocument();
    expect(mocks.useApi).not.toHaveBeenCalled();
  });
});
