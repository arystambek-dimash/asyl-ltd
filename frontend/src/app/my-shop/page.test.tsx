import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useCartStore } from "@/store/cart";

import MyShopPage from "./page";

const mocks = vi.hoisted(() => ({
  useApi: vi.fn(),
  usePagedApi: vi.fn(),
  post: vi.fn(),
  reload: vi.fn(),
  refresh: vi.fn(),
  toast: vi.fn(),
}));

vi.mock("@/lib/use-api", () => ({ useApi: mocks.useApi }));
vi.mock("@/lib/use-paged-api", () => ({ usePagedApi: mocks.usePagedApi }));
vi.mock("@/lib/api", () => ({ api: { post: mocks.post }, apiError: (e: Error) => e.message }));
vi.mock("@/lib/toast", () => ({ showToast: mocks.toast }));
vi.mock("@/store/auth", () => ({ useAuth: () => ({ me: { id: 3, is_client: false } }) }));
vi.mock("@/components/layout/app-shell", () => import("@/test-utils/app-shell"));
vi.mock("next/link", () => import("@/test-utils/next-link"));

const summary = {
  name: "Магазин Иван Петров",
  warehouse_name: "Мельница",
  currency: "KZT",
  debt_total: "150000.00",
  debt_currency: "KZT",
  debt_by_currency: { KZT: "150000.00" },
  month_bags: 30,
  month_amounts: { KZT: "150000.00" },
};

const products = [
  { id: 1, label: "Д1с · Красный 50 кг", weight_kg: "50.00", price: "5000.00", currency: "KZT" },
  { id: 2, label: "Без цены · Синий 25 кг", weight_kg: "25.00", price: null, currency: "KZT" },
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

  it("показывает только товары с ценой и списывает корзину со склада одной кнопкой", async () => {
    const user = userEvent.setup();
    mocks.post.mockResolvedValue({ data: { ...order, id: 42, bags: 2 } });
    render(<MyShopPage />);

    expect(screen.getByText(/списываются со склада «Мельница» сразу/)).toBeInTheDocument();
    expect(screen.queryByText("Без цены · Синий 25 кг")).not.toBeInTheDocument();
    expect(screen.getByText(/Ещё 1 товар без цены/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Добавить в корзину: Д1с · Красный 50 кг" }));
    await user.click(screen.getByRole("button", { name: "Больше: Д1с · Красный 50 кг" }));
    await user.click(screen.getByRole("button", { name: "Оформить" }));

    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(/без грузчика/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: /Списать 2 мешка/ }));

    await waitFor(() =>
      expect(mocks.post).toHaveBeenCalledWith("/my-shop/orders/", { items: [{ product: 1, quantity: 2 }] }),
    );
    expect(mocks.toast).toHaveBeenCalledWith("Заказ №42: 2 мешка списано со склада", "success");
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
});
