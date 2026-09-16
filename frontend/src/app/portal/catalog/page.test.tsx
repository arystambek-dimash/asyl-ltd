import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ComponentProps, ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useCartStore } from "@/store/cart";

import PortalCatalogPage from "./page";

const useApiMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/use-api", () => ({ useApi: useApiMock }));
vi.mock("@/store/auth", () => ({ useAuth: () => ({ me: { id: 7, is_client: true } }) }));
vi.mock("@/components/layout/app-shell", () => ({
  AppShell: ({ children, footer }: { children: ReactNode; footer?: ReactNode }) => (
    <main>
      {children}
      <footer>{footer}</footer>
    </main>
  ),
}));
vi.mock("next/link", () => ({
  default: ({ children, ...props }: ComponentProps<"a">) => <a {...props}>{children}</a>,
}));

describe("PortalCatalogPage", () => {
  beforeEach(() => {
    useCartStore.setState({ carts: {} });
    useApiMock.mockReset();
    useApiMock.mockReturnValue({
      data: [
        {
          id: 1,
          label: "Мука красная · 50 кг",
          weight_kg: "50.00",
          price: "12000.00",
          currency: "KZT",
          photo_url: null,
          // Старый API мог прислать поле, но клиентский UI не должен его раскрывать.
          available_bags: 9876,
        },
      ],
      loading: false,
      error: "",
      reload: vi.fn(),
    });
  });

  it("показывает товар и цену без точного складского остатка", () => {
    render(<PortalCatalogPage />);

    expect(screen.getByText("Мука красная · 50 кг")).toBeInTheDocument();
    expect(screen.getByText("12 000 ₸")).toBeInTheDocument();
    expect(screen.queryByText(/9876|9 876/)).not.toBeInTheDocument();
    expect(screen.queryByText(/в наличии|остаток/i)).not.toBeInTheDocument();
  });

  it("добавляет товар в корзину и меняет количество счётчиком", async () => {
    const user = userEvent.setup();
    render(<PortalCatalogPage />);

    await user.click(screen.getByRole("button", { name: "Добавить в корзину: Мука красная · 50 кг" }));
    const stepper = screen.getByRole("group", { name: "Количество: Мука красная · 50 кг" });
    // Сразу после добавления курсор в поле: количество набирают, а не нажимают «+» сотни раз.
    expect(within(stepper).getByRole("textbox")).toHaveFocus();
    await user.click(within(stepper).getByRole("button", { name: /Больше/ }));
    expect(within(stepper).getByRole("textbox")).toHaveValue("2");

    // Мешки заказывают сотнями — число вводится с клавиатуры.
    await user.clear(within(stepper).getByRole("textbox"));
    await user.type(within(stepper).getByRole("textbox"), "300");
    await user.tab();
    expect(within(stepper).getByRole("textbox")).toHaveValue("300");
    expect(screen.getByRole("contentinfo")).toHaveTextContent("Корзина · 300 мешков");
    expect(screen.getByRole("contentinfo")).toHaveTextContent("3 600 000 ₸");

    await user.clear(within(stepper).getByRole("textbox"));
    await user.type(within(stepper).getByRole("textbox"), "1");
    await user.tab();
    await user.click(within(stepper).getByRole("button", { name: /Убрать из корзины/ }));
    expect(screen.getByRole("button", { name: "Добавить в корзину: Мука красная · 50 кг" })).toBeInTheDocument();
    expect(useCartStore.getState().carts["7"].lines).toEqual([]);
  });
});
