import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
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
