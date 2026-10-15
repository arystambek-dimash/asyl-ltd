import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useCartStore } from "@/store/cart";

import MyShopPage from "./page";

const mocks = vi.hoisted(() => ({
  useApi: vi.fn(),
  usePagedApi: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  me: { id: 3, is_client: false, is_superuser: false, permissions: [] as string[] },
  reload: vi.fn(),
  refresh: vi.fn(),
  toast: vi.fn(),
}));

vi.mock("@/lib/use-api", () => ({ useApi: mocks.useApi }));
vi.mock("@/lib/use-paged-api", () => ({ usePagedApi: mocks.usePagedApi }));
vi.mock("@/lib/api", () => ({ api: { post: mocks.post, put: mocks.put }, apiError: (e: Error) => e.message }));
vi.mock("@/lib/toast", () => ({ showToast: mocks.toast }));
vi.mock("@/store/auth", () => ({ useAuth: () => ({ me: mocks.me }) }));
vi.mock("@/components/layout/app-shell", () => import("@/test-utils/app-shell"));
vi.mock("next/link", () => import("@/test-utils/next-link"));

const summary = {
  client: 77,
  name: "Магазин Иван Петров",
  currency: "KZT",
  debt_total: "150000.00",
  debt_currency: "KZT",
  debt_by_currency: { KZT: "150000.00" },
  month_bags: 30,
  month_amounts: { KZT: "150000.00" },
};

const products = [
  {
    id: 1,
    name: "Д1с",
    label: "Д1с · Красный 50 кг",
    color_label: "Красный",
    weight_kg: "50.00",
    price: "5000.00",
    currency: "KZT",
  },
  {
    id: 2,
    name: "Без цены",
    label: "Без цены · Синий 25 кг",
    color_label: "Синий",
    weight_kg: "25.00",
    price: null,
    currency: "KZT",
  },
];

const order = {
  id: 41,
  created_at: new Date().toISOString(),
  shipped_at: new Date().toISOString(),
  status: "shipped",
  status_label: "Отгружено",
  currency: "KZT",
  items: [
    { id: 1, product: 1, product_label: "Д1с · Красный 50 кг", quantity: 30, unit_price: "5000.00", is_bonus: false },
  ],
  bags: 30,
  total_amount: "150000.00",
  paid_total: "0.00",
  remaining_amount: "150000.00",
  payment_status: "unpaid",
  cancel_blocker: "",
};

function apiState(data: unknown) {
  return { data, loading: false, error: "", errorStatus: null, reload: mocks.reload, refresh: mocks.refresh };
}

describe("MyShopPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.me.permissions = [];
    useCartStore.setState({ carts: {} });
    mocks.useApi.mockImplementation((url: string | null) =>
      apiState(url === "/my-shop/" ? summary : url === "/my-shop/catalog/" ? products : null),
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
  });

  it("показывает только товары с ценой и оформляет корзину одной кнопкой, не списывая склад", async () => {
    const user = userEvent.setup();
    mocks.post.mockResolvedValue({ data: { ...order, id: 42, bags: 2 } });
    render(<MyShopPage />);

    expect(screen.getByText(/без грузчика, склад не списывается/)).toBeInTheDocument();
    expect(screen.queryByText("Без цены · Синий 25 кг")).not.toBeInTheDocument();
    expect(screen.getByText(/Ещё 1 товар без цены/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Добавить в корзину: Д1с · Красный 50 кг" }));
    await user.click(screen.getByRole("button", { name: "Больше: Д1с · Красный 50 кг" }));
    await user.click(screen.getByRole("button", { name: "Оформить" }));

    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(/склад не списывается/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: /Оформить 2 мешка/ }));

    await waitFor(() =>
      expect(mocks.post).toHaveBeenCalledWith("/my-shop/orders/", { items: [{ product: 1, quantity: 2 }] }),
    );
    expect(mocks.toast).toHaveBeenCalledWith("Заказ №42: 2 мешка оформлено", "success");
    expect(useCartStore.getState().carts["3"].lines).toEqual([]);
    expect(screen.getByText("№41")).toBeInTheDocument();
  });

  it("«Повторить» кладёт заказ в корзину, отмена спрашивает подтверждение", async () => {
    const user = userEvent.setup();
    mocks.post.mockResolvedValue({ data: { ...order, status: "cancelled" } });
    render(<MyShopPage />);

    await user.click(screen.getByRole("tab", { name: /Мои заказы/ }));
    await user.click(screen.getByRole("button", { name: /Повторить/ }));
    expect(useCartStore.getState().carts["3"].lines).toEqual([{ product: 1, quantity: 30 }]);
    expect(screen.getByRole("button", { name: "Оформить" })).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: /Мои заказы/ }));
    await user.click(screen.getByRole("button", { name: /Отменить/ }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Отменить заказ" }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledWith("/my-shop/orders/41/cancel/"));
    expect(mocks.refresh).toHaveBeenCalled();
  });

  it("без магазина объясняет, кто его включает", () => {
    mocks.useApi.mockReturnValue({ ...apiState(null), error: "Not found", errorStatus: 404 });
    render(<MyShopPage />);

    expect(screen.getByText("У вас нет своего магазина")).toBeInTheDocument();
  });

  it("без права на цены кнопки «Товар в прайс» нет", () => {
    render(<MyShopPage />);

    expect(screen.queryByRole("button", { name: /Товар в прайс/ })).not.toBeInTheDocument();
  });

  it("с правом на цены сотрудник сам ставит цену товару своего магазина", async () => {
    const user = userEvent.setup();
    mocks.me.permissions = ["clients.set_price"];
    mocks.put.mockResolvedValue({ data: {} });
    render(<MyShopPage />);

    await user.click(screen.getByRole("button", { name: /Товар в прайс/ }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).queryByRole("button", { name: /Новый товар/ })).not.toBeInTheDocument();
    // Тот же выбор, что в заказе: поиск, марки, справа — цена или «без цены».
    await user.click(within(dialog).getByRole("button", { name: /^Товар/ }));
    expect(within(dialog).getByRole("option", { name: /Д1с.*5\s000/ })).toBeInTheDocument();
    await user.type(within(dialog).getByLabelText("Поиск товара"), "без");
    await user.click(within(dialog).getByRole("option", { name: /Без цены/ }));
    await user.type(within(dialog).getByLabelText("Цена за мешок"), "4500");
    await user.click(within(dialog).getByRole("button", { name: "Сохранить цену" }));

    await waitFor(() =>
      expect(mocks.put).toHaveBeenCalledWith("/clients/77/prices/", {
        prices: [{ product: 2, currency: "KZT", price: "4500" }],
      }),
    );
    expect(mocks.post).not.toHaveBeenCalled();
    expect(mocks.toast).toHaveBeenCalledWith("«Без цены · Синий 25 кг» — в прайсе магазина", "success");
    expect(mocks.reload).toHaveBeenCalled();
  });

  it("с правом на каталог заводит новый товар и сразу ставит ему цену", async () => {
    const user = userEvent.setup();
    mocks.me.permissions = ["clients.set_price", "catalog.create"];
    mocks.post.mockResolvedValue({ data: { id: 9, label: "Отруби · Красный 25 кг" } });
    mocks.put.mockResolvedValue({ data: {} });
    render(<MyShopPage />);

    await user.click(screen.getByRole("button", { name: /Товар в прайс/ }));
    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: /Новый товар/ }));
    await user.type(within(dialog).getByLabelText("Название"), "Отруби");
    await user.selectOptions(within(dialog).getByLabelText("Фасовка"), "25");
    await user.type(within(dialog).getByLabelText("Цена за мешок"), "3000");
    await user.click(within(dialog).getByRole("button", { name: "Сохранить цену" }));

    await waitFor(() =>
      expect(mocks.put).toHaveBeenCalledWith("/clients/77/prices/", {
        prices: [{ product: 9, currency: "KZT", price: "3000" }],
      }),
    );
    expect(mocks.post).toHaveBeenCalledWith("/products/", { name: "Отруби", weight_kg: "25", color: "Red" });
    expect(mocks.toast).toHaveBeenCalledWith("«Отруби · Красный 25 кг» — в прайсе магазина", "success");
  });
});
