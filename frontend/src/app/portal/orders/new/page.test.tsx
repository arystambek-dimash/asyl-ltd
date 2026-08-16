import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import PortalNewOrderPage from "./page";

const useApiMock = vi.hoisted(() => vi.fn());
const pushMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/use-api", () => ({ useApi: useApiMock }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: pushMock }) }));
vi.mock("@/components/layout/app-shell", () => ({
  AppShell: ({ children }: { children: ReactNode }) => <main>{children}</main>,
}));

describe("PortalNewOrderPage stock privacy", () => {
  beforeEach(() => {
    pushMock.mockReset();
    useApiMock.mockReset();
    useApiMock.mockImplementation((url: string) => {
      if (url === "/portal/stores/") {
        return { data: [], loading: false, error: "", reload: vi.fn() };
      }
      return {
        data: [
          {
            id: 1,
            label: "Мука красная · 50 кг",
            weight_kg: "50.00",
            price: "12000.00",
            currency: "KZT",
            available_bags: 0,
          },
          {
            id: 2,
            label: "Мука синяя · 25 кг",
            weight_kg: "25.00",
            price: null,
            currency: "KZT",
            available_bags: 4321,
          },
        ],
        loading: false,
        error: "",
        reload: vi.fn(),
      };
    });
  });

  it("не раскрывает остаток и не блокирует товар по складскому количеству", async () => {
    const user = userEvent.setup();
    render(<PortalNewOrderPage />);

    const productSelect = screen.getByLabelText("Товар, позиция 1");
    const zeroStockProduct = screen.getByRole("option", { name: /Мука красная · 50 кг · 12.000 ₸/ });
    expect(zeroStockProduct).toBeEnabled();
    expect(screen.queryByText(/4321|4 321/)).not.toBeInTheDocument();
    expect(screen.queryByText(/в наличии|нет в наличии|остаток/i)).not.toBeInTheDocument();

    await user.selectOptions(productSelect, "1");
    expect(screen.getByLabelText("Количество мешков, позиция 1")).not.toHaveAttribute("max");
  });
});
