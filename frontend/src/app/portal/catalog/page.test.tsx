import { render, screen } from "@testing-library/react";
import type { ComponentProps, ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import PortalCatalogPage from "./page";

const useApiMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/use-api", () => ({ useApi: useApiMock }));
vi.mock("@/components/layout/app-shell", () => ({
  AppShell: ({ children }: { children: ReactNode }) => <main>{children}</main>,
}));
vi.mock("next/link", () => ({
  default: ({ children, ...props }: ComponentProps<"a">) => <a {...props}>{children}</a>,
}));

describe("PortalCatalogPage stock privacy", () => {
  beforeEach(() => {
    useApiMock.mockReset();
    useApiMock.mockReturnValue({
      data: [
        {
          id: 1,
          label: "Мука красная · 50 кг",
          weight_kg: "50.00",
          price: "12000.00",
          currency: "KZT",
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
});
