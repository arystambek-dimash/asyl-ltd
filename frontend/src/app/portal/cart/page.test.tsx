import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ComponentProps, ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useCartStore } from "@/store/cart";

import PortalCartPage from "./page";

const mocks = vi.hoisted(() => ({ useApi: vi.fn(), post: vi.fn(), reload: vi.fn() }));

vi.mock("@/lib/use-api", () => ({ useApi: mocks.useApi }));
vi.mock("@/lib/api", () => ({ api: { post: mocks.post }, apiError: (e: Error) => e.message }));
vi.mock("@/store/auth", () => ({ useAuth: () => ({ me: { id: 7, is_client: true } }) }));
vi.mock("@/components/layout/app-shell", () => ({
  AppShell: ({ children }: { children: ReactNode }) => <main>{children}</main>,
}));
vi.mock("next/link", () => ({
  default: ({ children, ...props }: ComponentProps<"a">) => <a {...props}>{children}</a>,
}));

const products = [
  { id: 1, label: "Красный 25 кг", weight_kg: "25.00", price: "25000.00", currency: "KZT", available_bags: 4321 },
  { id: 5, label: "Красный 50 кг", weight_kg: "50.00", price: "50000.00", currency: "KZT" },
];

function fillCart(lines: { product: number; quantity: number }[]) {
  useCartStore.setState({ carts: { "7": { lines, currency: null } } });
}

describe("PortalCartPage", () => {
  beforeEach(() => {
    mocks.post.mockReset();
    mocks.reload.mockReset();
    mocks.useApi.mockReset();
    mocks.useApi.mockImplementation((url: string) => ({
      data: url === "/portal/stores/" ? [] : products,
      loading: false,
      error: "",
      reload: mocks.reload,
    }));
    fillCart([
      { product: 1, quantity: 2 },
      { product: 5, quantity: 1 },
      { product: 9, quantity: 3 },
    ]);
  });

  it("показывает позиции, пересчитывает сумму и не раскрывает остаток", async () => {
    const user = userEvent.setup();
    render(<PortalCartPage />);

    expect(screen.getByText("25 000 ₸ × 2")).toBeInTheDocument();
    expect(screen.getByText("Товар больше недоступен")).toBeInTheDocument();
    expect(screen.getByText("3 мешка")).toBeInTheDocument();
    expect(screen.getByText("100 000 ₸")).toBeInTheDocument();
    expect(screen.queryByText(/4321|4 321|в наличии|остаток/i)).not.toBeInTheDocument();

    const stepper = screen.getByRole("group", { name: "Количество: Красный 25 кг" });
    expect(within(stepper).getByRole("textbox")).not.toHaveAttribute("max");
    await user.click(within(stepper).getByRole("button", { name: /Больше/ }));

    expect(screen.getByText("25 000 ₸ × 3")).toBeInTheDocument();
    expect(screen.getByText("125 000 ₸")).toBeInTheDocument();
  });

  it("оформляет заказ из доступных позиций, показывает номер и очищает корзину", async () => {
    const user = userEvent.setup();
    mocks.post.mockResolvedValue({ data: { id: 624 } });
    render(<PortalCartPage />);

    await user.click(screen.getByRole("radio", { name: "🚃 Вагон" }));
    await user.click(screen.getAllByRole("button", { name: "Оформить заказ" })[0]);

    await waitFor(() =>
      expect(mocks.post).toHaveBeenCalledWith("/portal/orders/", {
        items: [
          { product: 1, quantity: 2 },
          { product: 5, quantity: 1 },
        ],
        currency: "KZT",
        transport_type: "train",
        store: null,
      }),
    );
    expect(await screen.findByText("Заказ №624 оформлен")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Открыть заказ" })).toHaveAttribute("href", "/portal/orders/624");
    expect(useCartStore.getState().carts["7"].lines).toEqual([]);
  });

  it("при ошибке сервера оставляет корзину и обновляет каталог", async () => {
    const user = userEvent.setup();
    mocks.post.mockRejectedValue(new Error("Товар закончился"));
    render(<PortalCartPage />);

    await user.click(screen.getAllByRole("button", { name: "Оформить заказ" })[0]);

    expect(await screen.findByRole("alert")).toHaveTextContent("Товар закончился");
    expect(mocks.reload).toHaveBeenCalled();
    expect(useCartStore.getState().carts["7"].lines).toHaveLength(3);
  });

  it("без закреплённых цен не показывает сумму «0 ₸»", () => {
    mocks.useApi.mockImplementation((url: string) => ({
      data: url === "/portal/stores/" ? [] : products.map((product) => ({ ...product, price: null })),
      loading: false,
      error: "",
      reload: mocks.reload,
    }));
    render(<PortalCartPage />);

    expect(screen.getAllByText("Уточнит менеджер").length).toBeGreaterThan(0);
    expect(screen.queryByText("0 ₸")).not.toBeInTheDocument();
    expect(screen.getByText(/менеджер подтвердит её после заявки/)).toBeInTheDocument();
  });

  it("пустая корзина ведёт в каталог", () => {
    fillCart([]);
    render(<PortalCartPage />);

    expect(screen.getByText("Корзина пуста")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Перейти в каталог" })).toHaveAttribute("href", "/portal/catalog");
  });
});
